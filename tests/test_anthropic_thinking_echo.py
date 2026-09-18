"""A thinking-mode gateway refuses a replayed turn without its thinking.

Observed on an Anthropic-compatible DeepSeek endpoint: the request that
followed an assistant tool_use turn answered 400 "The content[].thinking in the
thinking mode must be passed back to the API". PawFlow replays the thinking
blocks when it still holds them, but a turn rebuilt from the transcript
(resume, wake, compaction) has none to send -- thinking is its own row there --
and this gateway, unlike Anthropic's own API, refuses the whole turn for it.
The turn is now retried once without thinking, and the verdict is remembered
for the endpoint so later calls never pay for it again.
"""

import json

import pytest

from core._llm_types import LLMCallError, LLMMessage, LLMToolCall
from core.llm_client import LLMClient
from core.llm_providers.anthropic import (
    LLMAnthropicMixin, _THINKING_ECHO_REQUIRED_ENDPOINTS)

UPSTREAM_ERROR = (
    'LLM API error 400: {"error":{"message":"The content[].thinking in the '
    'thinking mode must be passed back to the API.","type":'
    '"invalid_request_error","param":null,"code":"invalid_request_error"}}'
)

STREAM_OK = (
    'data: {"type":"message_start","message":{"model":"deepseek-flash",'
    '"usage":{"input_tokens":5,"output_tokens":1}}}\n'
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n'
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"done"}}\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":2}}\n'
    'data: {"type":"message_stop"}\n'
).encode()


class _ScriptedResponse:
    def __init__(self, status, payload, reason="OK"):
        self.status = status
        self.reason = reason
        self._payload = payload

    def read(self, _size=None):
        payload, self._payload = self._payload, b""
        return payload

    def getheaders(self):
        return []


def _scripted_transport(monkeypatch, responses):
    """Replay one canned HTTP response per request and record every body."""
    bodies = []

    class _Connection:
        def __init__(self, host, port=None, timeout=None, context=None):
            pass

        def request(self, method, path, body=None, headers=None):
            bodies.append(json.loads(body.decode()))

        def getresponse(self):
            return responses[min(len(bodies) - 1, len(responses) - 1)]

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.anthropic.http.client.HTTPConnection", _Connection)
    return bodies


def _client(**config):
    settings = {
        "api_key": "sk-test",
        "base_url": "http://localhost:11434",
        "default_model": "deepseek-flash",
    }
    settings.update(config)
    return LLMClient(provider="anthropic", config=settings)


@pytest.fixture(autouse=True)
def _isolated_endpoint_registry():
    """The learned verdict is process-wide; no test may inherit another's."""
    _THINKING_ECHO_REQUIRED_ENDPOINTS.clear()
    yield
    _THINKING_ECHO_REQUIRED_ENDPOINTS.clear()


def _messages():
    """A reasoned tool-call turn whose thinking was lost to the transcript."""
    return [
        LLMMessage("user", "go", conversation_id="conv1"),
        LLMMessage(
            "assistant", "looking", conversation_id="conv1",
            tool_calls=[LLMToolCall(
                id="call_1", name="bash", arguments={"command": "ls"})],
        ),
        LLMMessage("tool", "out", conversation_id="conv1", tool_call_id="call_1"),
    ]


def _stream(client, responses, monkeypatch, model="deepseek-flash"):
    bodies = _scripted_transport(monkeypatch, responses)
    response = LLMAnthropicMixin._stream_anthropic(
        client, _messages(), model, 0.5, 0, None, None, thinking_budget=1024)
    return bodies, response


class TestErrorDetection:
    def test_recognizes_the_gateway_wording(self):
        assert LLMAnthropicMixin._is_thinking_echo_required_error(UPSTREAM_ERROR)

    def test_ignores_unrelated_errors(self):
        assert not LLMAnthropicMixin._is_thinking_echo_required_error(
            'LLM API error 400: {"error":{"message":"invalid api key"}}')
        assert not LLMAnthropicMixin._is_thinking_echo_required_error(
            "LLM API error 400: thinking mode is not supported")
        assert not LLMAnthropicMixin._is_thinking_echo_required_error("")


class TestStreamRetry:
    def test_thinking_is_sent_without_a_verdict(self, monkeypatch):
        bodies, response = _stream(_client(), [_ScriptedResponse(200, STREAM_OK)],
                                   monkeypatch)

        assert response.content == "done"
        assert bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert bodies[0]["temperature"] == 1

    def test_rejected_turn_is_retried_without_thinking(self, monkeypatch):
        bodies, response = _stream(
            _client(),
            [_ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
             _ScriptedResponse(200, STREAM_OK)],
            monkeypatch)

        assert response.content == "done"
        assert len(bodies) == 2
        assert "thinking" in bodies[0]
        assert "thinking" not in bodies[1]
        # The caller's temperature comes back when thinking no longer forces 1.
        assert bodies[1]["temperature"] == 0.5

    def test_learned_verdict_reaches_a_fresh_client(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
            _ScriptedResponse(200, STREAM_OK),
            _ScriptedResponse(200, STREAM_OK),
        ])
        LLMAnthropicMixin._stream_anthropic(
            _client(), _messages(), "deepseek-flash", 0.5, 0, None, None,
            thinking_budget=1024)
        # Each call runs on its own clone, so the verdict has to outlive the
        # client that learned it.
        LLMAnthropicMixin._stream_anthropic(
            _client(), _messages(), "deepseek-flash", 0.5, 0, None, None,
            thinking_budget=1024)

        assert len(bodies) == 3
        assert "thinking" not in bodies[2]

    def test_verdict_is_keyed_by_endpoint_and_model(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
            _ScriptedResponse(200, STREAM_OK),
            _ScriptedResponse(200, STREAM_OK),
        ])
        LLMAnthropicMixin._stream_anthropic(
            _client(), _messages(), "deepseek-flash", 0.5, 0, None, None,
            thinking_budget=1024)
        LLMAnthropicMixin._stream_anthropic(
            _client(), _messages(), "another-model", 0.5, 0, None, None,
            thinking_budget=1024)

        assert bodies[2]["thinking"] == {"type": "enabled", "budget_tokens": 1024}

    def test_unrelated_400_is_not_retried(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, b'{"error":{"message":"invalid api key"}}',
                              reason="Bad Request"),
        ])

        with pytest.raises(LLMCallError):
            LLMAnthropicMixin._stream_anthropic(
                _client(), _messages(), "deepseek-flash", 0.5, 0, None, None,
                thinking_budget=1024)

        assert len(bodies) == 1

    def test_no_thinking_budget_never_retries(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
        ])

        with pytest.raises(LLMCallError):
            LLMAnthropicMixin._stream_anthropic(
                _client(), _messages(), "deepseek-flash", 0.5, 0, None, None)

        assert len(bodies) == 1


class TestNonStreaming:
    def test_a_learned_verdict_skips_thinking(self):
        client = _client()
        _THINKING_ECHO_REQUIRED_ENDPOINTS.add(
            client._thinking_echo_key("deepseek-flash"))
        posted = []
        client._http_post = lambda path, body, headers: (
            posted.append(body) or{
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        client._complete_anthropic(
            _messages(), "deepseek-flash", 0.5, 0, thinking_budget=1024)

        assert "thinking" not in posted[0]
        assert posted[0]["temperature"] == 0.5

    def test_without_a_verdict_thinking_is_still_sent(self):
        client = _client()
        posted = []
        client._http_post = lambda path, body, headers: (
            posted.append(body) or {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        client._complete_anthropic(
            _messages(), "deepseek-flash", 0.5, 0, thinking_budget=1024)

        assert posted[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
