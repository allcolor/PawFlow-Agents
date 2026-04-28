"""Lock Gemini ACP preempt and conversation-store race regressions."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_STREAMING = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")


def test_gemini_acp_preempt_uses_cancel_notification():
    """ACP preempt should cancel the active prompt without surfacing a CLI error."""
    body = _GEMINI[_GEMINI.index("def _gemini_send_user_message"):]
    body = body[:body.index("def cancel_gemini")]
    assert '"session/cancel"' in body
    assert "return False" in body
    assert "_kill_gemini_hard" not in body


def test_preempt_kill_fast_restarts_streaming_loop():
    """When Gemini kills on preempt, streaming must not wait for
    the old loop's cleanup before starting the resume turn.
    """
    assert "_fast_restart_after_preempt = False" in _AGENT_STREAMING
    assert "preempt killed provider CLI" in _AGENT_STREAMING
    assert "self._conv_generation[_agent_key]" in _AGENT_STREAMING
    assert "self._active_contexts.pop(_agent_key, None)" in _AGENT_STREAMING
    assert "if not _fast_restart_after_preempt:" in _AGENT_STREAMING
    assert "flowfile.set_content(_original_content)" in _AGENT_STREAMING


# ---------------------------------------------------------------------------
# Conversation store extras.tmp → extras.json Windows AV race fix
# ---------------------------------------------------------------------------

_CONV_STORE = Path("core/conversation_store.py").read_text(encoding="utf-8")


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
