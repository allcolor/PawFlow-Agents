"""A thinking-mode gateway may refuse a turn whose thinking blocks are missing.

Observed once on an Anthropic-compatible DeepSeek endpoint: a request answered
400 "The content[].thinking in the thinking mode must be passed back to the
API". PawFlow replays an assistant turn's thinking whenever it still holds it,
which covers the live tool loop, so this file pins down the net -- that refusal
is retried once without thinking -- and nothing more.

It deliberately does NOT disable thinking up front. Most turns of a long
conversation have no reasoning to replay (a model is free not to reason on a
given step: 38 of 242 tool_use turns in the conversation this was found in),
and the gateway accepts those, since only one request out of hundreds ever
failed. A blanket verdict would strip reasoning from turns that had nothing to
do with it, which is a degradation, not a fix.
"""

import json

import pytest

from core._llm_types import LLMCallError, LLMMessage, LLMToolCall
from core.llm_client import LLMClient
from core.llm_providers.anthropic import LLMAnthropicMixin

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


def _messages():
    """A tool-call turn replayed without any thinking, the common case."""
    return [
        LLMMessage("user", "go", conversation_id="conv1"),
        LLMMessage(
            "assistant", "looking", conversation_id="conv1",
            tool_calls=[LLMToolCall(
                id="call_1", name="bash", arguments={"command": "ls"})],
        ),
        LLMMessage("tool", "out", conversation_id="conv1", tool_call_id="call_1"),
    ]


def _reasoned_messages():
    """The same turn with its thinking still attached: the live tool loop."""
    messages = _messages()
    messages[1].thinking = "I must call the tool"
    return messages


def _stream(client, responses, monkeypatch, model="deepseek-flash",
            messages=None):
    bodies = _scripted_transport(monkeypatch, responses)
    response = LLMAnthropicMixin._stream_anthropic(
        client, messages or _messages(), model, 0.5, 0, None, None,
        thinking_budget=1024, call_conversation_id="conv1")
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
    def test_thinking_is_enabled_from_the_first_attempt(self, monkeypatch):
        """Reasoning or not, thinking is asked for; nothing is assumed."""
        bodies, response = _stream(
            _client(), [_ScriptedResponse(200, STREAM_OK)], monkeypatch)

        assert response.content == "done"
        assert len(bodies) == 1
        assert bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert bodies[0]["temperature"] == 1

    def test_rejected_turn_is_retried_without_thinking(self, monkeypatch):
        bodies, response = _stream(
            _client(),
            [_ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
             _ScriptedResponse(200, STREAM_OK)],
            monkeypatch, messages=_reasoned_messages())

        assert response.content == "done"
        assert len(bodies) == 2
        assert "thinking" in bodies[0]
        assert "thinking" not in bodies[1]
        # The caller's temperature comes back when thinking no longer forces 1.
        assert bodies[1]["temperature"] == 0.5

    def test_a_rejection_does_not_condemn_the_endpoint(self, monkeypatch):
        """The retry is a net for one request, never a per-endpoint verdict.

        Latching it would silently strip reasoning from every later turn,
        including the ones whose replayed reasoning is intact.
        """
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request"),
            _ScriptedResponse(200, STREAM_OK),
            _ScriptedResponse(200, STREAM_OK),
        ])
        LLMAnthropicMixin._stream_anthropic(
            _client(), _reasoned_messages(), "deepseek-flash", 0.5, 0, None, None,
            thinking_budget=1024)
        # Each call runs on its own clone, so a latched verdict would outlive
        # the client that learned it; nothing may have been latched.
        LLMAnthropicMixin._stream_anthropic(
            _client(), _reasoned_messages(), "deepseek-flash", 0.5, 0, None, None,
            thinking_budget=1024)

        assert len(bodies) == 3
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
    def test_thinking_is_enabled(self):
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
        assert posted[0]["temperature"] == 1

    def test_no_budget_means_no_thinking(self):
        client = _client()
        posted = []
        client._http_post = lambda path, body, headers: (
            posted.append(body) or {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        client._complete_anthropic(
            _messages(), "deepseek-flash", 0.5, 0)

        assert "thinking" not in posted[0]
        assert posted[0]["temperature"] == 0.5
