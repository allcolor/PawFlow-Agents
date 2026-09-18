"""A thinking-mode gateway requires the reasoning it produced back on replay.

Observed against opencode zen (Console Go serving deepseek-v4-flash): the
request that follows an assistant turn which reasoned -- and only that one --
answers 400 "The `reasoning_content` in the thinking mode must be passed back
to the API". Iterations whose responses carried ``thinking_chars=0`` kept
working, which is why the failure only shows up deep into a tool loop.
"""

import json

import pytest

from core._llm_types import LLMCallError, LLMMessage, LLMToolCall
from core.llm_client import LLMClient
from core.llm_providers.openai import LLMOpenaiMixin, _REASONING_ECHO_ENDPOINTS

UPSTREAM_ERROR = (
    "LLM API error 400: {\"error\":{\"param\":null,"
    "\"type\":\"invalid_request_error\",\"code\":\"invalid_request_error\","
    "\"message\":\"Error from provider (Console Go): Upstream request failed: "
    "[invalid_request_error] The `reasoning_content` in the thinking mode must "
    "be passed back to the API.\"}}"
)

STREAM_OK = (
    'data: {"choices":[{"delta":{"reasoning_content":"think"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
    'data: [DONE]\n\n'
).encode()


def _client(**config):
    settings = {
        "api_key": "sk-test",
        "base_url": "http://localhost:11434/v1",
        "default_model": "deepseek-v4-flash",
    }
    settings.update(config)
    return LLMClient(provider="openai", config=settings)


@pytest.fixture(autouse=True)
def _isolated_endpoint_registry():
    """The learned verdict is process-wide; no test may inherit another's."""
    _REASONING_ECHO_ENDPOINTS.clear()
    yield
    _REASONING_ECHO_ENDPOINTS.clear()


def _messages():
    """One reasoned tool-call turn, exactly as a tool loop replays it."""
    return [
        LLMMessage("user", "go", conversation_id="conv1"),
        LLMMessage(
            "assistant", "looking", conversation_id="conv1",
            thinking="I must call the tool",
            tool_calls=[LLMToolCall(id="call_1", name="bash", arguments={"command": "ls"})],
        ),
        LLMMessage("tool", "out", conversation_id="conv1", tool_call_id="call_1"),
    ]


class TestMessageBuilder:
    def test_reasoning_is_not_replayed_by_default(self):
        payload = _client()._build_openai_messages(
            _messages(), user_id="allcolor", conversation_id="conv1")

        assert "reasoning_content" not in payload[1]
        assert payload[1]["tool_calls"][0]["id"] == "call_1"

    def test_reasoning_is_replayed_when_enabled(self):
        payload = _client()._build_openai_messages(
            _messages(), user_id="allcolor", conversation_id="conv1",
            echo_reasoning=True)

        assert payload[1]["reasoning_content"] == "I must call the tool"

    def test_turns_without_reasoning_gain_no_field(self):
        messages = [LLMMessage("assistant", "plain", conversation_id="conv1")]

        payload = _client()._build_openai_messages(
            messages, user_id="allcolor", conversation_id="conv1",
            echo_reasoning=True)

        assert "reasoning_content" not in payload[0]


class TestServiceField:
    def test_default_is_off(self):
        assert _client().reasoning_content_echo is False

    def test_config_enables_it(self):
        assert _client(reasoning_content_echo=True).reasoning_content_echo is True

    def test_text_values_are_read_as_booleans(self):
        assert _client(reasoning_content_echo="true").reasoning_content_echo is True
        assert _client(reasoning_content_echo="false").reasoning_content_echo is False
        assert _client(reasoning_content_echo="").reasoning_content_echo is False


class TestErrorDetection:
    def test_recognizes_the_upstream_contract(self):
        assert LLMOpenaiMixin._is_reasoning_content_required_error(UPSTREAM_ERROR)

    def test_ignores_unrelated_errors(self):
        assert not LLMOpenaiMixin._is_reasoning_content_required_error(
            'LLM API error 400: {"error":{"message":"invalid api key"}}')
        assert not LLMOpenaiMixin._is_reasoning_content_required_error(
            "LLM API error 400: reasoning_content is not allowed")


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
        "core.llm_providers.openai.http.client.HTTPConnection", _Connection)
    return bodies


def _bad_request():
    return _ScriptedResponse(400, UPSTREAM_ERROR.encode(), reason="Bad Request")


class TestStreamRetry:
    def test_rejected_turn_is_retried_with_reasoning(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _bad_request(),
            _ScriptedResponse(200, STREAM_OK),
        ])

        response = LLMOpenaiMixin._stream_openai(
            _client(), _messages(), "deepseek-v4-flash", 0.0, 0, None, None)

        assert response.content == "done"
        assert len(bodies) == 2
        assert "reasoning_content" not in bodies[0]["messages"][1]
        assert bodies[1]["messages"][1]["reasoning_content"] == "I must call the tool"

    def test_learned_echo_reaches_a_fresh_client(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _bad_request(),
            _ScriptedResponse(200, STREAM_OK),
            _ScriptedResponse(200, STREAM_OK),
        ])

        LLMOpenaiMixin._stream_openai(_client(), _messages(), "m", 0.0, 0, None, None)
        # Each call runs on its own clone, so the verdict has to outlive the
        # client that learned it.
        LLMOpenaiMixin._stream_openai(_client(), _messages(), "m", 0.0, 0, None, None)

        # The second call never pays for the 400 again: its first attempt
        # already carries the reasoning.
        assert len(bodies) == 3
        assert bodies[2]["messages"][1]["reasoning_content"] == "I must call the tool"

    def test_verdict_is_keyed_by_endpoint_and_model(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _bad_request(),
            _ScriptedResponse(200, STREAM_OK),
            _ScriptedResponse(200, STREAM_OK),
        ])

        LLMOpenaiMixin._stream_openai(_client(), _messages(), "m", 0.0, 0, None, None)
        LLMOpenaiMixin._stream_openai(
            _client(), _messages(), "another-model", 0.0, 0, None, None)

        assert "reasoning_content" not in bodies[2]["messages"][1]

    def test_configured_echo_needs_no_rejection(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [_ScriptedResponse(200, STREAM_OK)])

        LLMOpenaiMixin._stream_openai(
            _client(reasoning_content_echo=True), _messages(), "m", 0.0, 0, None, None)

        assert len(bodies) == 1
        assert bodies[0]["messages"][1]["reasoning_content"] == "I must call the tool"

    def test_unrelated_400_is_not_retried(self, monkeypatch):
        bodies = _scripted_transport(monkeypatch, [
            _ScriptedResponse(400, b'{"error":{"message":"invalid api key"}}',
                              reason="Bad Request"),
        ])

        with pytest.raises(LLMCallError):
            LLMOpenaiMixin._stream_openai(
                _client(), _messages(), "m", 0.0, 0, None, None)

        assert len(bodies) == 1


class TestNonStreamingRetry:
    def test_rejected_completion_is_retried_with_reasoning(self, monkeypatch):
        bodies = []

        def _post(path, body, headers, *, base_url=""):
            # Snapshot: the retry rewrites `messages` inside this same dict.
            bodies.append(json.loads(json.dumps(body)))
            if len(bodies) == 1:
                raise LLMCallError(UPSTREAM_ERROR, category="invalid_request_error")
            return {
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            }

        client = _client()
        monkeypatch.setattr(client, "_http_post", _post)

        response = LLMOpenaiMixin._complete_openai(
            client, _messages(), "deepseek-v4-flash", 0.0, 0, None)

        assert response.content == "ok"
        assert len(bodies) == 2
        assert "reasoning_content" not in bodies[0]["messages"][1]
        assert bodies[1]["messages"][1]["reasoning_content"] == "I must call the tool"
