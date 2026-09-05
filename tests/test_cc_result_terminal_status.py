"""Outward regressions for Claude Code stream-json terminal failures."""

import io
import json
from unittest.mock import MagicMock

import pytest

from core.llm_client import LLMClient, LLMClientError, LLMMessage
from tasks.ai.agent_exceptions import AgentCancelled


_FAILURE_REASONS = (
    "api_error",
    "malformed_tool_use_exhausted",
    "budget_exhausted",
    "structured_output_retry_exhausted",
    "tool_deferred_unavailable",
    "turn_setup_failed",
    "blocking_limit",
    "rapid_refill_breaker",
    "prompt_too_long",
    "image_error",
    "model_error",
)
_SCOPE = {
    "call_user_id": "test-user",
    "call_conversation_id": "test-conv",
    "call_agent_name": "test-agent",
    "call_ephemeral_stream": True,
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    client = LLMClient(
        "claude-code", config={"api_key": "test-key", "default_model": "sonnet",
                               "max_retries": 1})
    for name in (
        "_setup_credentials", "_spawn_cc_stream", "_recover_tokens",
        "_kill_cc_hard", "_ccs_stall_watchdog", "_ccs_pub",
    ):
        monkeypatch.setattr(client, name, MagicMock())
    monkeypatch.setattr(client, "_cleanup_proc", MagicMock(return_value=""))
    monkeypatch.setattr(client, "_get_session_workdir",
                        lambda *_: str(tmp_path))
    monkeypatch.setattr(client, "_build_cli_initial_context_prompt",
                        lambda *_args, **_kwargs: "Hi")
    monkeypatch.setattr("core._llm_client_driver.time.sleep", lambda *_: None)
    return client


def _proc(event, *preceding):
    proc = MagicMock()
    proc.stdout = io.StringIO("".join(
        json.dumps(item) + "\n" for item in (*preceding, event)))
    proc.returncode = 0
    proc.poll.return_value = None
    return proc


def _result(**fields):
    return {
        "type": "result", "subtype": "success", "is_error": False,
        "session_id": "test-session", "result": "", **fields,
    }


def _stream(client, **kwargs):
    return client._stream_claude_code(
        [LLMMessage(role="user", content="Hi", conversation_id="test-conv")],
        "sonnet", 0.7, 0, None, **_SCOPE, **kwargs)


def _complete(client, **kwargs):
    return client.complete_stream(
        [LLMMessage(role="user", content="Hi", conversation_id="test-conv")],
        **_SCOPE, **kwargs)


@pytest.mark.parametrize("status", [400, 429, 500, 503, 529])
def test_success_subtype_with_http_failure_raises_without_error_text(client, status):
    proc = _proc(_result(api_error_status=status))
    client._spawn_cc_stream.return_value = (proc, None, None)
    chunks = []

    with pytest.raises(LLMClientError, match=str(status)):
        _stream(client, callback=chunks.append)

    assert chunks == []
    assert client._result_emitted is False
    client._kill_cc_hard.assert_called_once_with(proc)
    client._cleanup_proc.assert_called_once_with(proc)


@pytest.mark.parametrize("reason", _FAILURE_REASONS)
def test_failure_terminal_reason_overrides_success_without_error_text(client, reason):
    client._spawn_cc_stream.return_value = (
        _proc(_result(terminal_reason=reason)), None, None)

    with pytest.raises(LLMClientError, match=reason):
        _stream(client)

    assert client._result_emitted is False


@pytest.mark.parametrize("fields", [
    {"is_error": True},
    {"is_error": True, "result": None},
    {"subtype": "error_during_execution"},
])
def test_existing_error_markers_never_finish_silently(client, fields):
    client._spawn_cc_stream.return_value = (_proc(_result(**fields)), None, None)

    with pytest.raises(LLMClientError, match="Claude Code"):
        _stream(client)

    assert client._result_emitted is False


def test_error_metadata_and_error_array_reach_caller_without_becoming_answer(client):
    client._spawn_cc_stream.return_value = (_proc(_result(
        api_error_status=529, terminal_reason="api_error",
        errors=[{"message": "Upstream unavailable"}, "Try later"],
    )), None, None)
    chunks = []

    with pytest.raises(LLMClientError) as info:
        _stream(client, callback=chunks.append)

    assert "529" in str(info.value)
    assert "api_error" in str(info.value)
    assert "Upstream unavailable; Try later" in str(info.value)
    assert chunks == []


def test_status_remains_retryable_with_long_result_text(client):
    client._config_ref["max_retries"] = 2
    failed = _proc(_result(api_error_status=529, result="x" * 500))
    success = _proc(_result(result="Recovered"))
    client._spawn_cc_stream.side_effect = [
        (failed, None, None), (success, None, None)]

    response = _complete(client)

    assert response.raw["result"] == "Recovered"
    assert client._spawn_cc_stream.call_count == 2
    client._kill_cc_hard.assert_called_once_with(failed)


@pytest.mark.parametrize("fields", [
    {},
    {"api_error_status": None},
    {"api_error_status": 200, "terminal_reason": "end_turn"},
    {"terminal_reason": "interrupted"},
    {"terminal_reason": "cancelled"},
])
def test_success_and_nonfailure_terminal_reasons_preserve_response(client, fields):
    event = _result(
        result="Done", model="sonnet",
        usage={"input_tokens": 12, "output_tokens": 3}, **fields)
    client._spawn_cc_stream.return_value = (_proc(event), None, None)
    chunks = []

    response = _stream(client, callback=chunks.append)

    assert response.content == "Done"
    assert response.finish_reason == "stop"
    assert response.raw == event
    assert (response.tokens_in, response.tokens_out) == (12, 3)
    assert chunks == ["Done"]
    assert client._result_emitted is True
    client._kill_cc_hard.assert_not_called()


def test_cancellation_propagates_without_retry_and_next_turn_succeeds(client):
    client._config_ref["max_retries"] = 3
    proc = _proc(
        _result(api_error_status=529),
        {"type": "content_block_delta",
         "delta": {"type": "text_delta", "text": "Partial"}},
    )
    client._spawn_cc_stream.return_value = (proc, None, None)
    cancelled = AgentCancelled("Stopped by user")

    def cancel(_text):
        raise cancelled

    with pytest.raises(AgentCancelled) as info:
        _complete(client, callback=cancel)

    assert info.value is cancelled
    assert client._spawn_cc_stream.call_count == 1
    client._kill_cc_hard.assert_called_once_with(proc)
    client._cleanup_proc.assert_called_once_with(proc)
    assert client._result_emitted is False

    client._spawn_cc_stream.return_value = (
        _proc(_result(result="Next turn")), None, None)
    assert _stream(client).content == "Next turn"


def test_auth_failure_still_refreshes_and_retries_once(client, monkeypatch):
    client._current_pool_index = 0
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(client, "_force_refresh_pool_entry", refresh)
    failed = _proc(_result(is_error=True, result="401 Unauthorized"))
    success = _proc(_result(result="Authenticated"))
    client._spawn_cc_stream.side_effect = [
        (failed, None, None), (success, None, None)]

    assert _stream(client).content == "Authenticated"

    refresh.assert_called_once_with(
        0, user_id="test-user", conversation_id="test-conv")
    assert client._spawn_cc_stream.call_count == 2
