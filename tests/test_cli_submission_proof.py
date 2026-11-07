"""User interrupts keep journal proof; inbox notices only use transport."""
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


def test_inbox_notice_does_not_wait_for_journal_or_model_proof(tmp_path, monkeypatch):
    state = _state(tmp_path, "codex-interactive")
    pool, pastes = _codex_pool(monkeypatch, state, lambda text: None)
    notice = "[PawFlow inbox: ACTION REQUIRED] Call read_inbox() NOW."

    def unexpected(*args, **kwargs):
        pytest.fail("inbox notices must not wait for submission proof")

    monkeypatch.setattr(pool, "_journal_mark", unexpected)
    monkeypatch.setattr(pool, "_verify_submitted", unexpected)

    assert pool.send_queued(state, notice) is True
    assert pastes == [notice]


@pytest.mark.parametrize("failed_step", ["paste", "enter"])
def test_notice_transport_failure_is_returned(tmp_path, monkeypatch, failed_step):
    state = _state(tmp_path, "codex-interactive")
    pool, _ = _codex_pool(monkeypatch, state, lambda text: None)
    if failed_step == "paste":
        monkeypatch.setattr(pool, "_paste_text", lambda *args: False)
    else:
        monkeypatch.setattr(pool, "send_keys", lambda *args: False)

    assert pool.send_queued(state, "Call read_inbox() NOW.") is False


def test_user_interrupt_is_proven_by_its_journal(tmp_path, monkeypatch):
    state = _state(tmp_path, "codex-interactive")
    history = tmp_path / ".codex" / "history.jsonl"
    pool, pastes = _codex_pool(monkeypatch, state, lambda text: _append(
        history, {"session_id": "s", "ts": 1, "text": text}))
    monkeypatch.setattr(pool, "_prepare_prompt_input", lambda state: True)
    monkeypatch.setattr(pool, "_wait_for_prompt_ready", lambda name: True)
    monkeypatch.setattr(pool, "_submit_delay_seconds", lambda: 0)

    assert pool.send_interrupt(state, "change the task") is True
    assert pastes == ["change the task"]


def test_an_unproven_user_interrupt_is_refused(tmp_path, monkeypatch):
    state = _state(tmp_path, "codex-interactive")
    pool, _ = _codex_pool(monkeypatch, state, lambda text: None)
    monkeypatch.setattr(pool, "_prepare_prompt_input", lambda state: True)
    monkeypatch.setattr(pool, "_wait_for_prompt_ready", lambda name: True)
    monkeypatch.setattr(pool, "_submit_delay_seconds", lambda: 0)
    monkeypatch.setattr(pool, "_verify_submitted", lambda *args, **kwargs: False)

    assert pool.send_interrupt(state, "change the task") is False


@pytest.mark.parametrize("provider,pool_class,lookup", [
    ("claude-code-interactive", InteractiveClaudeCodePool, "_cci_session_state"),
    ("codex-interactive", CodexInteractivePool, "_codex_interactive_session_state"),
])
@pytest.mark.parametrize("had_preempts", [False, True])
@pytest.mark.parametrize("send_ok", [False, True])
def test_inbox_notice_preserves_user_preempt_state(
        monkeypatch, provider, pool_class, lookup, had_preempts, send_ok):
    from core.llm_client import LLMClient

    client = LLMClient(provider)
    state = object()
    sent = []
    pool = types.SimpleNamespace(
        send_queued=lambda item, text: sent.append((item, text)) or send_ok)
    monkeypatch.setattr(pool_class, "instance", lambda: pool)
    monkeypatch.setattr(client, lookup, lambda **kwargs: state)
    client._had_preempts_this_turn = had_preempts
    notice = "[PawFlow inbox: ACTION REQUIRED] Call read_inbox() NOW."

    assert client.send_queued_message(notice) is send_ok
    assert sent == [(state, notice)]
    assert client._had_preempts_this_turn is had_preempts


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


# -- turn completion follows the wire, not inbox read status -----------------

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


def test_stop_finishes_after_the_wire_response(monkeypatch):
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._POST_STOP_IDLE_DRAIN_SECONDS", 0.05)
    coord = _CCITurnCoordinator(_Events(_finished_turn_events()), "sess")

    result = _run(coord)

    assert "error" not in result, result.get("error")
    assert result["response"].content == "Done."


def _msg(msg_id):
    return types.SimpleNamespace(msg_id=msg_id)


def test_providers_without_journals_keep_the_preempt_flag():
    client = types.SimpleNamespace()
    assert _preempt_handled_verdict(client, _msg("m"), True) is True
    assert _preempt_handled_verdict(client, _msg("m"), False) is False


def test_an_empty_delta_does_not_forget_the_live_session():
    """"nothing to submit" is raised before any paste: the CLI session is
    intact, so its claude_session marker must survive the failed turn."""
    from pathlib import Path

    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("st._is_transport_kill = (")[1].split("return _ALC_BREAK")[0]

    assert '"nothing to submit" in st.err_str' in body
    assert "if not st._keep_session:" in body
