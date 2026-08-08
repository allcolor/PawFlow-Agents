"""Native provider input usage is the primary context-gauge measurement."""

from types import SimpleNamespace
from unittest.mock import patch

from core.llm_client import LLMClient, LLMMessage, LLMResponse
from tasks.ai.context_usage import compute_context_usage


def _client(provider="openai"):
    return LLMClient(provider, {"api_key": "test", "max_retries": 1})


def test_api_response_records_full_native_prompt_before_fallback():
    client = _client()
    response = LLMResponse(
        content="ok", tokens_in=1_200,
        cache_read_tokens=180_000, cache_creation_tokens=47_222,
    )
    with patch.object(client, "_complete_openai", return_value=response), \
            patch.object(client, "publish_observed_context_usage") as publish:
        client.complete(
            [LLMMessage(role="user", content="hello",
                        conversation_id="conv-1")],
            call_user_id="u1", call_conversation_id="conv-1",
            call_agent_name="assistant", call_event_cid="event-1",
        )

    key = ("conv-1", "assistant")
    assert client._cli_observed_context_tokens_by_stream[key] == 228_422
    assert client._observed_context_mode_by_stream[key] == "request"
    assert client._observed_context_revision_by_stream[key] == 1
    publish.assert_called_once_with(
        "conv-1", "assistant", user_id="u1", event_cid="event-1",
        source="openai_native_input_usage")


def test_absent_native_usage_is_not_recorded_as_the_driver_estimate():
    client = _client()
    response = LLMResponse(
        content="ok", tokens_in=999, input_usage_native=False)
    with patch.object(client, "_complete_openai", return_value=response), \
            patch.object(client, "publish_observed_context_usage") as publish:
        result = client.complete(
            [LLMMessage(role="user", content="hello",
                        conversation_id="conv-2")],
            call_conversation_id="conv-2", call_agent_name="assistant")

    assert result.tokens_in > 0
    assert client._cli_observed_context_tokens_by_stream == {}
    publish.assert_not_called()


def test_same_native_value_still_creates_a_new_observation_revision():
    client = _client()
    response = LLMResponse(content="ok", tokens_in=5_000)
    with patch.object(client, "_complete_openai", return_value=response), \
            patch.object(client, "publish_observed_context_usage"):
        for _ in range(2):
            client.complete(
                [LLMMessage(role="user", content="hello",
                            conversation_id="conv-3")],
                call_conversation_id="conv-3", call_agent_name="assistant")

    assert client._observed_context_revision_by_stream[
        ("conv-3", "assistant")] == 2


def test_cli_provider_keeps_its_native_session_specific_recorder():
    client = _client("claude-code-interactive")
    result = LLMResponse(tokens_in=123)
    client._record_response_context_usage(
        result, call_conversation_id="conv-4", call_agent_name="assistant")
    assert client._cli_observed_context_tokens_by_stream == {}


def test_streaming_api_response_records_native_prompt_usage():
    client = _client()
    response = LLMResponse(
        content="ok", tokens_in=900, cache_read_tokens=4_100)
    with patch.object(client, "_stream_openai", return_value=response), \
            patch.object(client, "publish_observed_context_usage") as publish:
        client.complete_stream(
            [LLMMessage(role="user", content="hello",
                        conversation_id="conv-5")],
            call_user_id="u1", call_conversation_id="conv-5",
            call_agent_name="assistant", call_event_cid="event-5",
        )

    key = ("conv-5", "assistant")
    assert client._cli_observed_context_tokens_by_stream[key] == 5_000
    assert client._observed_context_mode_by_stream[key] == "request"
    publish.assert_called_once_with(
        "conv-5", "assistant", user_id="u1", event_cid="event-5",
        source="openai_native_input_usage")


def test_api_gauge_prefers_native_request_measurement():
    client = _client()
    client._record_observed_context(
        "conv-6", "assistant", 42_000, mode="request")
    ctx = {
        "active_agent_name": "assistant",
        "active_llm_provider": "openai",
        "client": client,
        "messages": [LLMMessage(
            role="user", content="far smaller than the native prompt",
            conversation_id="conv-6")],
        "resolved_svc": SimpleNamespace(
            config={"max_context_size": 100_000}),
    }

    with patch("tasks.ai.context_usage._active_context", return_value=ctx):
        usage = compute_context_usage(
            "conv-6", "assistant", store=object(), source="test")

    assert usage["used"] == 42_000
    assert usage["context_source_measured"] is True
    assert usage["context_measurement_mode"] == "request"
    assert usage["context_measurement_revision"] == 1
    assert usage["context_measurement_tokens"] == 42_000