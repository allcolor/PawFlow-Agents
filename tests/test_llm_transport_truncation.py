"""A response body that ends early must not kill the turn.

Observed live (2026-09-18 19:31:33): ``LLM call failed (iter 18): LLM streaming
failed after 1 attempt(s): IncompleteRead: IncompleteRead(2173 bytes read)``.
``IncompleteRead`` is raised by our own http.client streaming loop, and no
marker in ``_is_transient_transport_error`` matched it, so ``retryable`` stayed
False and the whole turn was lost on the first attempt.

The retry also has to be the *truncated* one: the drop can arrive after half an
answer was already streamed, so the buffers are reset before re-asking (the half
already handed to the live callback cannot be recalled, but it must not be
sliced into the retry's output).
"""

import http.client

import pytest

from core._llm_types import LLMMessage
from core.llm_client import LLMClient, LLMResponse


class TestClassification:
    def test_the_observed_message_is_a_transport_drop(self):
        assert LLMClient._is_transient_transport_error(
            "LLM streaming failed after 1 attempt(s): "
            "IncompleteRead: IncompleteRead(2173 bytes read)") is True

    def test_other_truncated_body_signatures(self):
        for text in (
            "ChunkedEncodingError: connection broken: IncompleteRead(0 bytes read)",
            "the peer closed connection without sending a complete message",
            "Remote end closed connection without response",
        ):
            assert LLMClient._is_transient_transport_error(text) is True

    def test_a_permanent_error_stays_permanent(self):
        assert LLMClient._is_transient_transport_error("invalid api key") is False
        assert LLMClient._is_transient_transport_error(
            "LLM API error 400: bad request") is False

    def test_a_truncated_body_is_distinguished_from_a_verdict(self):
        assert LLMClient._is_truncated_body_error(
            http.client.IncompleteRead(b"partial")) is True
        assert LLMClient._is_truncated_body_error(ConnectionResetError()) is True
        assert LLMClient._is_truncated_body_error(BrokenPipeError()) is True
        assert LLMClient._is_truncated_body_error(ValueError("bad request")) is False


class TestStreamingRetry:
    def _client(self):
        return LLMClient(provider="anthropic", config={
            "api_key": "sk-test",
            "default_model": "deepseek-flash",
            "max_retries": 2,
        })

    def test_a_truncated_body_is_retried(self, monkeypatch):
        attempts = []
        answer = "the whole answer"

        def fake_stream(self, messages, model, temperature, max_tokens, tools,
                        callback, thinking_budget=0, thinking_callback=None, **kw):
            attempts.append(model)
            if len(attempts) == 1:
                if callback:
                    callback("HALF")
                raise http.client.IncompleteRead(b"HALF")
            if callback:
                callback(answer)
            return LLMResponse(content=answer, model=model)

        monkeypatch.setattr(LLMClient, "_stream_anthropic", fake_stream)
        chunks = []
        result = self._client().complete_stream(
            [LLMMessage("user", "hi", conversation_id="conv1")],
            callback=chunks.append)

        assert len(attempts) == 2
        # The dropped attempt contributes nothing to the answer.
        assert result.content == answer
        # The half already handed to the live callback is inherent; the retry's
        # text is not appended to it as one string.
        assert chunks[0] == "HALF"
        assert chunks[-1] == answer

    def test_an_unrelated_error_is_not_retried(self, monkeypatch):
        attempts = []

        def fake_stream(self, *args, **kwargs):
            attempts.append(1)
            raise ValueError("bad request")

        monkeypatch.setattr(LLMClient, "_stream_anthropic", fake_stream)

        with pytest.raises(Exception):
            self._client().complete_stream(
                [LLMMessage("user", "hi", conversation_id="conv1")])

        assert len(attempts) == 1
