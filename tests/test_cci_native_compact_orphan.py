"""A native compaction hook with nobody listening must still be preempted.

Replays the 2026-09-08 incident: a Codex session emitted ``PreCompact`` five
times while no coordinator and no capture were reading its stream. The hook
sat in the queue, ``turn_over`` was still True from the previous Stop, so the
undelivered rule filed it as a post-Stop straggler and the next turn drained
it. PawFlow never compacted; Codex did.

Two layers close that gap in ``CCInteractiveEventService``: the hook adopts a
capture immediately when no listener is recent (``_maybe_adopt_orphan_turn``),
and it re-arms the undelivered backstop (``_track_turn_boundary``) so a hook
that arrived while a listener looked recent is still adopted later. The
capture's coordinator then raises ``CCCompactDetected`` on the queued hook
and hands the compaction to PawFlow.
"""

import time

import pytest

from services.cc_interactive_event_service import CCInteractiveEventService


def _service() -> CCInteractiveEventService:
    return CCInteractiveEventService({"token": "tok", "_service_id": "events"})


def _session(svc, provider="codex-interactive"):
    return svc.register_session(
        "sess", user_id="u1", conversation_id="c1", agent_name="assistant",
        provider=provider)


def _capture_sink(monkeypatch):
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))
    return captured


def _hook(name, **extra):
    return {"type": "hook", "hook_event_name": name, **extra}


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("provider", ["codex-interactive", "claude-code-interactive"])
def test_native_compaction_with_no_listener_adopts_a_capture_at_once(
        monkeypatch, hook, provider):
    svc = _service()
    state = _session(svc, provider)
    captured = _capture_sink(monkeypatch)

    # Previous turn ended; nobody claims, nobody polls, no paste in flight.
    svc.publish_event("sess", _hook("Stop"))
    assert state.turn_over
    assert captured == []
    # As the next worker would: the finished turn's tail is discarded.
    svc.drain_session("sess")

    svc.publish_event("sess", _hook(hook))

    assert captured == ["sess"], "the compaction hook must be handed to a capture"
    assert not state.turn_over, "a compacting CLI is mid-turn, not between turns"
    # The hook itself stays in the stream for the capture's coordinator, which
    # raises CCCompactDetected on it and hands the compaction to PawFlow.
    queued = svc.wait_event("sess", timeout=0)
    assert queued["type"] == "hook"
    assert queued["hook_event_name"] == hook


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("provider", ["codex-interactive", "claude-code-interactive"])
@pytest.mark.parametrize("later_stop", [False, True])
def test_native_compaction_rearms_the_undelivered_rule(monkeypatch, hook, provider, later_stop):
    """A listener looked recent when the hook landed, then vanished: the
    backstop must still adopt the hook instead of filing it post-Stop."""
    svc = _service()
    state = _session(svc, provider)
    captured = _capture_sink(monkeypatch)

    svc.publish_event("sess", _hook("Stop"))
    state.last_wait_at = time.time()  # a poll just happened: no immediate adoption
    svc.publish_event("sess", _hook(hook))
    assert captured == []
    assert not state.turn_over

    if later_stop:
        svc.publish_event("sess", _hook("Stop"))
        assert state.turn_over

    state.last_wait_at = 0.0
    state.oldest_pending_at = (
        time.time() - CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS - 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"]


@pytest.mark.parametrize("drain", [False, True])
def test_consumed_compaction_does_not_reopen_post_stop_tail(monkeypatch, drain):
    svc = _service()
    state = _session(svc)
    captured = _capture_sink(monkeypatch)
    state.last_wait_at = time.time()
    svc.publish_event("sess", _hook("PreCompact"))
    if drain:
        svc.drain_session("sess")
    else:
        assert svc.wait_event("sess", timeout=0)["hook_event_name"] == "PreCompact"
    svc.publish_event("sess", _hook("Stop"))
    state.last_wait_at = 0
    state.oldest_pending_at = time.time() - svc._UNDELIVERED_ADOPT_SECONDS - 1
    svc._adopt_if_undelivered(state)
    assert captured == []


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
def test_native_compaction_never_steals_a_live_request(monkeypatch, hook):
    """The request coordinator owns the hook: it raises CCCompactDetected
    itself. A capture spawned here would split the stream."""
    svc = _service()
    _session(svc)
    captured = _capture_sink(monkeypatch)

    epoch = svc.claim_consumer("sess")
    svc.wait_event("sess", timeout=0, epoch=epoch)
    svc.publish_event("sess", _hook(hook))
    assert captured == []


def test_native_compaction_during_a_send_is_left_to_the_send_path(monkeypatch):
    """While a paste is in flight the pool's submission waiter preempts on the
    latched hook; the orphan net must stay quiet for the injection grace."""
    svc = _service()
    _session(svc)
    captured = _capture_sink(monkeypatch)

    svc.remember_injected_prompt("sess", "Continue the pending work.")
    svc.publish_event("sess", _hook("PreCompact"))
    assert captured == []


def test_a_late_compaction_hook_does_not_reopen_a_turn_that_ended_after_it(
        monkeypatch):
    """Hooks arrive on their own connections: an old PreCompact published after
    the Stop that followed it describes history, not a running turn."""
    svc = _service()
    state = _session(svc)
    captured = _capture_sink(monkeypatch)

    now = time.time()
    svc.publish_event("sess", _hook("Stop", timestamp=now))
    svc.publish_event("sess", _hook("PreCompact", timestamp=now - 100))
    assert state.turn_over
    assert captured == [], "a historical compaction hook must not spawn a capture"
    # The Codex latch is a separate contract: the session is still invalid
    # for the next PawFlow send even though no capture was reopened.
    assert state.native_compact_hook == "PreCompact"


def test_other_hooks_still_do_not_touch_the_turn_boundary():
    svc = _service()
    state = _session(svc)
    svc.publish_event("sess", _hook("Stop"))
    boundary = state.turn_boundary_at
    svc._track_turn_boundary(state, _hook("SessionEnd", timestamp=time.time()))
    assert state.turn_over
    assert state.turn_boundary_at == boundary
