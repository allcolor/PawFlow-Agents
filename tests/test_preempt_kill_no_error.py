"""Lock the gemini preempt-kill flow against regression:

When `send_user_message` is called mid-turn, the provider tears down
the in-flight CLI on purpose. The CLI exits non-zero — EXPECTED.
The stream loop must NOT raise `LLMClientError` in that case;
otherwise the user sees a red CLI stream error bubble AND the queued
preempt message never gets a turn (the agent
loop bails on the error before draining PendingQueue).

Guard pattern: the `_preempt_killed` flag is set by the
`*_send_user_message` preempt path and reset at the start of every
stream. The post-loop exit-code check skips the raise when the flag
is True.
"""

import re
from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_STREAMING = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")


def _has_preempt_killed_branch(src: str, label: str) -> None:
    # 1. The flag is reset at every stream start.
    assert "self._preempt_killed = False" in src, (
        f"{label}: stream-start reset of _preempt_killed is missing")
    # 2. The preempt entrypoint sets it to True before killing.
    assert "self._preempt_killed = True" in src, (
        f"{label}: send_user_message preempt path must set "
        f"_preempt_killed before tearing down the CLI")
    # 3. The post-loop check guards the LLMClientError raise.
    pattern = re.compile(
        r"if self\._preempt_killed and proc\.returncode"
        r"\s+and\s+proc\.returncode != 0:")
    assert pattern.search(src), (
        f"{label}: missing `_preempt_killed` short-circuit before "
        f"the LLMClientError raise on non-zero exit")



def test_gemini_preempt_kill_does_not_raise():
    _has_preempt_killed_branch(_GEMINI, "gemini")


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
