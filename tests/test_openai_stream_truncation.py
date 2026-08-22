"""A stream that stops behind a 200 must fail loudly and be retried.

Observed against opencode zen (ox-alpha-free): 17 of 63 calls ended without a
valid end-of-stream signal. Every one of them was accepted as a finished
answer, so the agent released the turn on a response the user never saw --
no error, no retry, nothing in the webchat.
"""

import pytest

from core._llm_types import (
    TRUNCATED_STREAM_CATEGORIES,
    LLMCallError,
    LLMClientError,
    LLMMessage,
    LLMResponse,
)
from core.llm_client import LLMClient
from core.llm_providers.openai import VALID_FINISH_REASONS


def _sse(*events: str) -> list:
    """Encode SSE events, then EOF (b"" is what a closed connection returns)."""
    return [e.encode() for e in events] + [b""]


class _Response:
    status = 200
    reason = "OK"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""

    def getheaders(self):
        return []


def _client(monkeypatch, chunks_per_attempt, *, max_retries=1,
            response_class=_Response):
    """An LLMClient whose transport replays one canned stream per attempt."""
    attempts = []

    class _Connection:
        def __init__(self, host, port=None, timeout=None):
            pass

        def request(self, method, path, body=None, headers=None):
            attempts.append(path)

        def getresponse(self):
            idx = min(len(attempts) - 1, len(chunks_per_attempt) - 1)
            return response_class(chunks_per_attempt[idx])

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.openai.http.client.HTTPConnection", _Connection)
    client = LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://localhost:11434/v1",
        "default_model": "test-model",
        "max_retries": max_retries,
    })

    def fail_non_streaming_fallback(*_args, **_kwargs):
        raise LLMCallError(
            "Truncated LLM stream: non-streaming fallback failed",
            category="stream_truncated",
        )

    monkeypatch.setattr(
        client, "_complete_openai", fail_non_streaming_fallback)
    return client, attempts


DELTA = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
DELTA_THINK = 'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n\n'
STOP = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
NETWORK_ERROR = 'data: {"choices":[{"delta":{},"finish_reason":"network_error"}]}\n\n'
SENSITIVE = 'data: {"choices":[{"delta":{},"finish_reason":"sensitive"}]}\n\n'
DONE = 'data: [DONE]\n\n'


class TestTruncationIsAnError:
    """No finish_reason and no [DONE] means the connection died mid-answer."""

    def test_silent_truncation_raises_retryable(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DELTA_THINK)])
        with pytest.raises(LLMClientError) as excinfo:
            client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert "Truncated LLM stream" in str(excinfo.value)

    def test_truncation_category_is_retryable(self, monkeypatch):
        """The category is the only retry signal: there is no status code."""
        from core.llm_providers.openai import LLMOpenaiMixin

        client, _ = _client(monkeypatch, [_sse(DELTA_THINK)])
        with pytest.raises(LLMCallError) as excinfo:
            LLMOpenaiMixin._stream_openai(
                client, [LLMMessage("user", "ping", conversation_id="conv1")], "test-model",
                0.0, 0, None, None)
        err = excinfo.value
        assert err.category == "stream_truncated"
        assert err.category in TRUNCATED_STREAM_CATEGORIES
        assert err.retryable is True

    def test_truncation_after_partial_text_still_raises(self, monkeypatch):
        """Half an answer delivered as a whole one is the quieter symptom."""
        client, _ = _client(monkeypatch, [_sse(DELTA)])
        with pytest.raises(LLMClientError):
            client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])


class TestInBandProviderError:
    """A finish_reason the spec does not define is a gateway failure."""

    def test_network_error_finish_reason_raises(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(NETWORK_ERROR, DONE)])
        from core.llm_providers.openai import LLMOpenaiMixin

        with pytest.raises(LLMCallError) as excinfo:
            LLMOpenaiMixin._stream_openai(
                client,
                [LLMMessage("user", "ping", conversation_id="conv1")],
                "test-model", 0.0, 0, None, None,
            )
        assert "network_error" in str(excinfo.value)

    def test_network_error_is_not_a_valid_finish_reason(self):
        assert "network_error" not in VALID_FINISH_REASONS
        assert {"stop", "length", "tool_calls"} <= VALID_FINISH_REASONS


class TestWellFormedStreamsAreUntouched:
    """The guard must not turn working providers into failures."""

    def test_normal_stop_is_accepted(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DELTA, STOP, DONE)])
        streamed = []
        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")],
            callback=streamed.append,
        )
        assert resp.content == "hello"
        assert resp.finish_reason == "stop"
        assert streamed == ["hello"]

    def test_done_without_finish_reason_is_an_empty_answer_not_an_error(
            self, monkeypatch):
        """[DONE] with nothing else is well formed: the provider said nothing.

        This is the shape a bare relay-proxied endpoint returns, and it must
        keep returning an empty response rather than raising.
        """
        client, _ = _client(monkeypatch, [_sse(DONE)])
        resp = client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert resp.content == ""
        assert resp.finish_reason == ""

    def test_done_stops_reading_without_waiting_for_socket_close(self, monkeypatch):
        class _KeepAliveResponse(_Response):
            def read(self, size):
                if not self._chunks:
                    raise AssertionError("read() called after data: [DONE]")
                return super().read(size)

        client, _ = _client(
            monkeypatch, [[DONE.encode()]], response_class=_KeepAliveResponse)
        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")])
        assert resp.finish_reason == ""

    def test_safety_alias_is_a_terminal_content_filter(self, monkeypatch):
        client, attempts = _client(monkeypatch, [_sse(SENSITIVE, DONE)])
        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")])
        assert len(attempts) == 1
        assert resp.finish_reason == "content_filter"


class TestNonStreamingFinishReasons:
    """The recovery request must not accept an in-band transport failure."""

    @staticmethod
    def _completion(client, monkeypatch, finish_reason):
        from core.llm_providers.openai import LLMOpenaiMixin

        monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: {
            "model": "test-model",
            "choices": [{
                "message": {"content": "answer"},
                "finish_reason": finish_reason,
            }],
            "usage": {},
        })
        return LLMOpenaiMixin._complete_openai(
            client,
            [LLMMessage("user", "ping", conversation_id="conv1")],
            "test-model", 0.0, 0, None,
        )

    def test_network_error_is_retryable_not_a_success(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DONE)])
        with pytest.raises(LLMCallError) as excinfo:
            self._completion(client, monkeypatch, "network_error")
        assert excinfo.value.category == "provider_stream_error"
        assert excinfo.value.retryable is True

    def test_safety_alias_is_normalized(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DONE)])
        resp = self._completion(client, monkeypatch, "sensitive")
        assert resp.finish_reason == "content_filter"

    def test_unknown_success_reason_remains_compatible(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DONE)])
        resp = self._completion(client, monkeypatch, "gateway_custom_success")
        assert resp.finish_reason == "gateway_custom_success"


class TestRetry:
    """Recover through non-streaming first, then the bounded replay loop."""

    def test_truncation_falls_back_to_non_streaming(self, monkeypatch):
        client, attempts = _client(
            monkeypatch, [_sse(DELTA_THINK)], max_retries=3)
        streamed = []
        thinking = []
        fallback_calls = []

        def complete_fallback(*args, **kwargs):
            fallback_calls.append((args, kwargs))
            return LLMResponse(
                content="fallback answer",
                thinking="finished reasoning",
                model="test-model",
                finish_reason="stop",
                tokens_out=4,
            )

        monkeypatch.setattr(client, "_complete_openai", complete_fallback)
        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")],
            callback=streamed.append,
            thinking_callback=thinking.append,
        )

        assert len(attempts) == 1
        assert len(fallback_calls) == 1
        assert resp.content == "fallback answer"
        assert streamed == ["fallback answer"]
        assert thinking == ["hmm", "finished reasoning"]

    def test_in_band_network_error_uses_non_streaming_fallback(
            self, monkeypatch):
        client, attempts = _client(
            monkeypatch, [_sse(NETWORK_ERROR, DONE)], max_retries=2)
        monkeypatch.setattr(
            client,
            "_complete_openai",
            lambda *_args, **_kwargs: LLMResponse(
                content="recovered",
                model="test-model",
                finish_reason="stop",
            ),
        )

        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")])

        assert len(attempts) == 1
        assert resp.content == "recovered"

    def test_truncation_is_retried_and_the_retry_wins(self, monkeypatch):
        """A failed non-streaming fallback returns to the bounded SSE retry."""
        client, attempts = _client(
            monkeypatch,
            [_sse(DELTA_THINK), _sse(DELTA, STOP, DONE)],
            max_retries=3,
        )
        resp = client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert resp.content == "hello"
        assert resp.finish_reason == "stop"
        assert len(attempts) == 2

    def test_partial_text_is_not_prefixed_onto_the_retry(self, monkeypatch):
        """The aborted attempt's deltas must not be glued to the good answer."""
        streamed = []
        client, attempts = _client(
            monkeypatch,
            [_sse(DELTA), _sse(DELTA, STOP, DONE)],
            max_retries=3,
        )
        resp = client.complete_stream(
            [LLMMessage("user", "ping", conversation_id="conv1")], callback=streamed.append)
        assert len(attempts) == 2
        assert resp.content == "hello"
        # 'hello' was streamed twice (once per attempt) but the committed
        # answer is the retry's, not the concatenation of both.
        assert "hellohello" not in resp.content

    def test_exhausted_retries_surface_an_error(self, monkeypatch):
        """Silence is the bug being fixed: never return empty and stop."""
        client, attempts = _client(
            monkeypatch, [_sse(DELTA_THINK)], max_retries=3)
        with pytest.raises(LLMClientError):
            client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert len(attempts) == 3
