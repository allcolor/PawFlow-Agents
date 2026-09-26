"""A message pasted into a live CLI is proven by the CLI's own journals.

Incident 2026-09-26 (GameDev7, Codex): a message queued behind a blocking
tool produces no hook until the model reads it, two minutes later. The pool
waited 45 s for a receipt, logged "not confirmed" and -- on the live-submit
path -- still reported success, so the turn's final drain dropped the rescue
copy of a message it never proved was read. After an event queue overflow the
session rejected every later event, and every later turn failed.
"""
import json
import threading
import types

import pytest

from core._cci_pool_spawn import InteractiveContainer
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.codex_interactive_pool import CodexInteractivePool
from core.llm_providers._cci_turn import _CCITurnCoordinator
from tasks.ai._alc_base import _preempt_handled_verdict

RUNNING_PANE = "• Calling pawflow.use_tool(...)\n◦ Working (1m 24s • esc to interrupt)\n"


def _append(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _state(tmp_path, provider):
    return InteractiveContainer(
        key=("u", "c", "a", "s"), name="pf-test", workdir=str(tmp_path),
        container_workdir="/cc", session_token="tok", event_service_id="e",
        internal_token="i", provider=provider, prompt_ready=True)


class _SilentReceipts:
    """An event service that never sees a hook or a model request."""

    def submission_marker(self, token):
        return (0, 0)

    def wait_for_prompt_submission(self, token, text, **kw):
        return ""


def _codex_pool(monkeypatch, state, on_enter):
    pool = CodexInteractivePool.__new__(CodexInteractivePool)
    pastes = []
    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    monkeypatch.setattr(pool, "_cancel_copy_mode", lambda s: None)
    monkeypatch.setattr(pool, "_remember_injected_prompt", lambda s, t: None)
    monkeypatch.setattr(pool, "_remember_injected_prompt_for_event_service",
                        lambda s, t: _SilentReceipts())
    monkeypatch.setattr(pool, "_paste_text",
                        lambda s, t: pastes.append(t) or True)
    monkeypatch.setattr(pool, "_paste_settle_seconds", lambda: 0)
    monkeypatch.setattr(pool, "_check_native_compaction", lambda s: None)
    monkeypatch.setattr(pool, "_leave_backtrack_overlay", lambda s: True)
    monkeypatch.setattr(pool, "_event_session", lambda s: None)
    monkeypatch.setattr(pool, "_pane_text", lambda name: RUNNING_PANE)

    def send_keys(_state, keys):
        if keys == ["Enter"]:
            on_enter(pastes[-1])
        return True
    monkeypatch.setattr(pool, "send_keys", send_keys)
    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_GRACE_SECONDS", "0")
    return pool, pastes


def test_codex_message_queued_behind_a_tool_is_accepted_from_its_journal(
        tmp_path, monkeypatch, caplog):
    state = _state(tmp_path, "codex-interactive")
    history = tmp_path / ".codex" / "history.jsonl"
    pool, pastes = _codex_pool(monkeypatch, state, lambda text: _append(
        history, {"session_id": "s", "ts": 1, "text": text}))

    assert pool.send_queued(state, "PROBE-Y marker", msg_id="m1") is True

    assert pastes == ["PROBE-Y marker"]
    assert "not confirmed" not in caplog.text
    assert [s.msg_id for s in state.pending_submissions] == ["m1"]
    assert pool.submission_processed(state, "m1") is False


def test_the_rollout_proves_the_model_read_the_message(tmp_path, monkeypatch):
    state = _state(tmp_path, "codex-interactive")
    home = tmp_path / ".codex"
    rollout = home / "sessions" / "2026" / "09" / "26" / "rollout-a.jsonl"
    _append(rollout, {"type": "session_meta"})
    pool, _ = _codex_pool(monkeypatch, state, lambda text: _append(
        home / "history.jsonl", {"ts": 1, "text": text}))
    pool.send_queued(state, "PROBE-Y marker", msg_id="m1")

    _append(rollout, {"type": "response_item", "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "PROBE-Y marker"}]}})

    assert pool.unprocessed_submissions(state) == []
    assert pool.submission_processed(state, "m1") is True
    assert pool.submission_processed(state, "never-pasted") is None


def test_a_message_the_cli_never_accepted_is_refused_and_not_tracked(
        tmp_path, monkeypatch):
    state = _state(tmp_path, "codex-interactive")
    pool, _ = _codex_pool(monkeypatch, state, lambda text: None)
    monkeypatch.setattr(pool, "_verify_submitted", lambda *a, **kw: False)

    assert pool.send_queued(state, "lost", msg_id="m1") is False
    assert state.pending_submissions == []


@pytest.mark.parametrize("method", ["send_queued", "send_interrupt"])
def test_nothing_is_pasted_into_a_session_whose_event_stream_died(
        tmp_path, monkeypatch, method):
    state = _state(tmp_path, "codex-interactive")
    pool, pastes = _codex_pool(monkeypatch, state, lambda text: None)
    monkeypatch.setattr(pool, "_event_session", lambda s: types.SimpleNamespace(
        unreliable=True, error="CC interactive event queue overflow"))

    assert getattr(pool, method)(state, "hello") is False
    assert pastes == []
    assert "event queue overflow" in state.last_error


def test_a_session_whose_event_stream_died_is_replaced_not_reused(
        tmp_path, monkeypatch):
    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    state = _state(tmp_path, "claude-code-interactive")
    pool._lock = threading.RLock()
    pool._sessions = {state.key: state}
    killed, unregistered = [], []
    monkeypatch.setattr(pool, "ensure_sweeper", lambda **kw: None)
    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    monkeypatch.setattr(pool, "_tmux_is_alive", lambda name: True)
    monkeypatch.setattr(pool, "_session_compatible", lambda s, c: True)
    monkeypatch.setattr(pool, "_event_session", lambda s: types.SimpleNamespace(
        unreliable=True, error="CC interactive event queue overflow"))
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda s: None)
    monkeypatch.setattr(pool, "_kill_container", killed.append)
    monkeypatch.setattr(pool, "_unregister_event_session", unregistered.append)

    class _Launch(Exception):
        pass

    def before_launch():
        raise _Launch()
    client = types.SimpleNamespace(_agent_service="s", idle_ttl_seconds=None)

    with pytest.raises(_Launch):
        pool.ensure_started(client, "m", "u", "c", "a",
                            before_launch=before_launch)

    assert killed == ["pf-test"]
    assert unregistered == [state]
    assert state.key not in pool._sessions


# -- the turn stays open while an accepted message is unread -----------------

class _Events:
    def __init__(self, events):
        self._events = list(events)

    def wait_event(self, session_token, timeout=None):
        return self._events.pop(0) if self._events else {}


def _finished_turn_events():
    return [
        {"type": "request_start", "request_id": "r1", "path": "/v1/messages"},
        {"type": "sse", "request_id": "r1", "event": "content_block_start",
         "payload": {"type": "content_block_start", "index": 0,
                     "content_block": {"type": "text", "text": ""}}},
        {"type": "sse", "request_id": "r1", "event": "content_block_delta",
         "payload": {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": "Done."}}},
        {"type": "sse", "request_id": "r1", "event": "content_block_stop",
         "payload": {"type": "content_block_stop", "index": 0}},
        {"type": "sse", "request_id": "r1", "event": "message_delta",
         "payload": {"type": "message_delta",
                     "delta": {"stop_reason": "end_turn"}}},
        {"type": "request_stop", "request_id": "r1"},
        {"type": "hook", "hook_event_name": "Stop"},
    ]


def _run(coord):
    result = {}

    def target():
        try:
            result["response"] = coord.run()
        except Exception as exc:
            result["error"] = exc
    runner = threading.Thread(target=target, daemon=True)
    runner.start()
    runner.join(20)
    assert not runner.is_alive(), "the coordinator never finished the turn"
    return result


def test_stop_waits_until_the_accepted_message_is_read(monkeypatch):
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._POST_STOP_IDLE_DRAIN_SECONDS", 0.05)
    unread = iter([1, 1, 0])
    calls = []

    def still_unread():
        calls.append(1)
        return next(unread, 0)
    coord = _CCITurnCoordinator(_Events(_finished_turn_events()), "sess",
                                submissions_callback=still_unread)
    coord._unread_checked_at = 0.0
    monkeypatch.setattr(coord, "_submissions_still_owed",
                        _fast_owed(coord))

    result = _run(coord)

    assert "error" not in result, result.get("error")
    assert result["response"].content == "Done."
    assert len(calls) == 3


def test_an_unread_message_holds_the_turn_only_until_the_cap(monkeypatch):
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._POST_STOP_IDLE_DRAIN_SECONDS", 0.05)
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._POST_STOP_UNREAD_SUBMISSION_CAP_SECONDS",
        0.3)
    coord = _CCITurnCoordinator(_Events(_finished_turn_events()), "sess",
                                submissions_callback=lambda: 1)

    result = _run(coord)

    assert "error" not in result, result.get("error")
    assert result["response"].content == "Done."


def _fast_owed(coord):
    """Re-read the callback on every poll instead of once a second."""
    original = type(coord)._submissions_still_owed

    def owed():
        coord._unread_checked_at = 0.0
        return original(coord)
    return owed


# -- the final drain decides per message ---------------------------------------

def _msg(msg_id):
    return types.SimpleNamespace(msg_id=msg_id)


def test_the_journal_verdict_overrides_the_turn_wide_preempt_flag():
    client = types.SimpleNamespace(
        cli_submission_processed=lambda mid: {"read": True, "unread": False}.get(mid))

    assert _preempt_handled_verdict(client, _msg("unread"), True) is False
    assert _preempt_handled_verdict(client, _msg("read"), False) is True
    # Untracked message: the provider's own flag still decides.
    assert _preempt_handled_verdict(client, _msg("other"), True) is True


def test_providers_without_journals_keep_the_preempt_flag():
    client = types.SimpleNamespace()
    assert _preempt_handled_verdict(client, _msg("m"), True) is True
    assert _preempt_handled_verdict(client, _msg("m"), False) is False


# -- an unread message is submitted again by the next turn ---------------------

def test_the_next_turn_resubmits_an_accepted_message_the_model_never_read(
        tmp_path):
    """GameDev2 2026-09-26 14:12Z: the final drain re-triggered with the
    unread messages, but they were still recorded as pasted, so the prompt
    skipped them and the turn failed with "nothing to submit"."""
    from core import cli_prompt_journal
    from core.llm_client import LLMMessage
    from core.llm_providers.claude_code_interactive import (
        LLMClaudeCodeInteractiveMixin)

    state = _state(tmp_path, "claude-code-interactive")
    transcript = tmp_path / "projects" / "-cc" / "s.jsonl"
    _append(transcript, {"type": "user", "message": {"content": "earlier"}})
    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    since = cli_prompt_journal.mark(state.provider, state.workdir)
    pool.track_submission(state, "read one", since, "m-read")
    pool.track_submission(state, "never read", since, "m-unread")
    state.submitted_msg_ids = {"m-read", "m-unread", "m-older"}
    _append(transcript, {"type": "attachment", "attachment": {
        "type": "queued_command", "prompt": "read one"}})
    assert pool.submission_processed(state, "m-unread") is False

    assert pool.reclaim_unread_submissions(state) == {"m-unread"}

    assert state.pending_submissions == []
    assert state.submitted_msg_ids == {"m-read", "m-older"}
    provider = LLMClaudeCodeInteractiveMixin.__new__(
        LLMClaudeCodeInteractiveMixin)
    text = provider._cci_live_text(
        [LLMMessage(role="user", content="read one", msg_id="m-read",
                    conversation_id="c"),
         LLMMessage(role="user", content="never read", msg_id="m-unread",
                    conversation_id="c")],
        state=state)
    assert text == "never read"


def test_nothing_is_reclaimed_when_every_message_was_read(tmp_path):
    from core import cli_prompt_journal

    state = _state(tmp_path, "claude-code-interactive")
    transcript = tmp_path / "projects" / "-cc" / "s.jsonl"
    _append(transcript, {"type": "user", "message": {"content": "earlier"}})
    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    since = cli_prompt_journal.mark(state.provider, state.workdir)
    pool.track_submission(state, "read one", since, "m-read")
    state.submitted_msg_ids = {"m-read"}
    _append(transcript, {"type": "user", "message": {"content": (
        '<pasted_content id="cc5d">\nread one\n</pasted_content id="cc5d">')}})

    assert pool.reclaim_unread_submissions(state) == set()
    assert state.submitted_msg_ids == {"m-read"}
    assert pool.submission_processed(state, "m-read") is True


def test_an_empty_delta_does_not_forget_the_live_session():
    """"nothing to submit" is raised before any paste: the CLI session is
    intact, so its claude_session marker must survive the failed turn."""
    from pathlib import Path

    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("st._is_transport_kill = (")[1].split("return _ALC_BREAK")[0]

    assert '"nothing to submit" in st.err_str' in body
    assert "if not st._keep_session:" in body
