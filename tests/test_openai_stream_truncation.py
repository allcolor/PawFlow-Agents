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


def _client(monkeypatch, chunks_per_attempt, *, max_retries=1):
    """An LLMClient whose transport replays one canned stream per attempt."""
    attempts = []

    class _Connection:
        def __init__(self, host, port=None, timeout=None):
            pass

        def request(self, method, path, body=None, headers=None):
            attempts.append(path)

        def getresponse(self):
            idx = min(len(attempts) - 1, len(chunks_per_attempt) - 1)
            return _Response(chunks_per_attempt[idx])

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
    return client, attempts


DELTA = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
DELTA_THINK = 'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n\n'
STOP = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
NETWORK_ERROR = 'data: {"choices":[{"delta":{},"finish_reason":"network_error"}]}\n\n'
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
        with pytest.raises(LLMClientError) as excinfo:
            client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert "network_error" in str(excinfo.value)

    def test_network_error_is_not_a_valid_finish_reason(self):
        assert "network_error" not in VALID_FINISH_REASONS
        assert {"stop", "length", "tool_calls"} <= VALID_FINISH_REASONS


class TestWellFormedStreamsAreUntouched:
    """The guard must not turn working providers into failures."""

    def test_normal_stop_is_accepted(self, monkeypatch):
        client, _ = _client(monkeypatch, [_sse(DELTA, STOP, DONE)])
        resp = client.complete_stream([LLMMessage("user", "ping", conversation_id="conv1")])
        assert resp.content == "hello"
        assert resp.finish_reason == "stop"

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


class TestRetry:
    """A truncated stream is transient: ask again rather than give up."""

    def test_truncation_is_retried_and_the_retry_wins(self, monkeypatch):
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
