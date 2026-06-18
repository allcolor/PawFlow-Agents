"""Lock Gemini ACP preempt and conversation-store race regressions."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_STREAMING = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")


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
