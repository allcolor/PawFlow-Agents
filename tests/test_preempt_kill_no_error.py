"""Lock Gemini ACP preempt and conversation-store race regressions."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_STREAMING = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
_OPENAI = Path("core/llm_providers/openai.py").read_text(encoding="utf-8")
_OPENAI_RESPONSES = Path("core/llm_providers/openai_responses.py").read_text(encoding="utf-8")
_ANTHROPIC = Path("core/llm_providers/anthropic.py").read_text(encoding="utf-8")


def test_gemini_acp_preempt_uses_live_prompt():
    """ACP preempt should steer the warm session; rescue queuing is shared."""
    body = _GEMINI[_GEMINI.index("def _gemini_send_user_message"):]
    body = body[:body.index("def cancel_gemini")]
    assert '"session/cancel"' in body
    assert '"session/prompt"' in body
    assert "preempt_req_id" in body
    assert "return True" in body
    assert "_kill_gemini_hard" not in body


def test_preempt_kill_fast_restarts_streaming_loop():
    """When Gemini kills on preempt, streaming must not wait for
    the old loop's cleanup before starting the resume turn.
    """
    assert "_fast_restart_after_preempt = False" in _AGENT_STREAMING
    assert "preempt killed provider CLI" in _AGENT_STREAMING
    assert "self._conv_generation[_agent_key]" in _AGENT_STREAMING
    assert "self._active_contexts.pop(_agent_key, None)" in _AGENT_STREAMING
    assert "if _fast_restart_after_preempt:" in _AGENT_STREAMING
    assert "Do not also" in _AGENT_STREAMING
    assert "PendingQueue drain" in _AGENT_STREAMING


def test_api_preempt_aborts_and_fast_restarts():
    """API providers (openai/anthropic/responses) have no stdin to type
    into: a new user message during an active turn must abort the in-flight
    HTTP stream, cancel in-flight tool calls, bump the generation, and seed
    a fresh turn with the same message -- the CLI fast-restart semantics.
    """
    body = _AGENT_STREAMING[_AGENT_STREAMING.index(
        "API providers (openai, openai-responses"):]
    body = body[:body.index("if (not _active_client")]
    assert "_active_client.abort()" in body
    assert "ToolRelayService.cancel_agent" in body
    assert "_conv_generation[_agent_key]" in body
    assert "_fast_restart_after_preempt = True" in body
    assert "_already_active = False" in body
    assert "supports_live_preempt" in body


def test_cancel_resume_reorders_tool_results_before_new_user_message():
    from core.llm_client import LLMMessage, LLMToolCall
    from tasks.ai._alc_setup import _repair_tool_result_order

    call = LLMMessage(
        role="assistant", content="",
        tool_calls=[
            LLMToolCall(id="a", name="read", arguments={}),
            LLMToolCall(id="b", name="search", arguments={}),
        ],
        conversation_id="c1",
    )
    resume = LLMMessage(role="user", content="continue", conversation_id="c1")
    result_b = LLMMessage(
        role="tool", content="done", tool_call_id="b", conversation_id="c1")

    repaired, changed = _repair_tool_result_order(
        [call, resume, result_b], "c1")

    assert changed is True
    assert [message.role for message in repaired[:4]] == [
        "assistant", "tool", "tool", "user"]
    assert [message.tool_call_id for message in repaired[1:3]] == ["a", "b"]
    assert "cancelled" in repaired[1].content
    assert repaired[2] is result_b


def test_api_stream_read_abort_raises_agent_cancelled():
    """abort() closes the socket mid-read; the provider must convert the
    resulting connection error into a clean AgentCancelled interruption, not
    a raw AttributeError/OSError that the agent loop turns into an error turn.
    The fix must exist in every API streaming provider (openai, responses,
    anthropic)."""
    for src in (_OPENAI, _OPENAI_RESPONSES, _ANTHROPIC):
        # The read is wrapped: try/except around response.read(4096) that
        # converts the closed-socket error into AgentCancelled when abort
        # is pending, and re-raises otherwise.
        assert (
            "try:\n                    chunk = response.read(4096)\n                except Exception:"
            in src), "read must be wrapped"
        assert "raise AgentCancelled()" in src
        assert "_abort.is_set()" in src


# ---------------------------------------------------------------------------
# Conversation store extras.tmp → extras.json Windows AV race fix
# ---------------------------------------------------------------------------

_CONV_STORE = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("core").glob("*conversation_store*.py")))  # split across _conversation_store_*.py
_CONTINUOUS_EXECUTOR = Path("engine/continuous_executor.py").read_text(encoding="utf-8")


def test_write_extras_retries_on_permission_error():
    """_write_extras must absorb the transient WinError 5 that AV /
    Defender / OneDrive cause when they briefly hold a read handle on
    the freshly-written tmp file. A handful of short retries is the
    standard pattern; without it `set_extra` blows up on Windows even
    though no PawFlow code is touching the destination."""
    # Function body should mention the retry loop and the
    # PermissionError class.
    body = _CONV_STORE[_CONV_STORE.index("def _write_extras"):]
    body = body[:body.index("def _read")]
    assert "PermissionError" in body, (
        "_write_extras must catch PermissionError to retry the rename")
    assert "for _attempt in range" in body or "for _ in range" in body, (
        "_write_extras must retry the os.replace on PermissionError")


def test_hot_metadata_write_is_best_effort():
    body = _CONV_STORE[_CONV_STORE.index("def _persist_hot_metadata"):]
    body = body[:body.index("def _ensure_loaded")]
    assert "attempts=1" in body
    assert "hot metadata extras write skipped" in body


def test_cli_session_cleanup_does_not_block_startup_ready_path():
    assert "def _cleanup_cli_sessions_async" in _CONTINUOUS_EXECUTOR
    assert "name=\"cli-session-cleanup\"" in _CONTINUOUS_EXECUTOR
    assert "daemon=True" in _CONTINUOUS_EXECUTOR
    assert "executor CLI session cleanup async" in _CONTINUOUS_EXECUTOR
