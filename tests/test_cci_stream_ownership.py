"""Exclusive ownership of a CC interactive session's event stream.

``queue.Queue`` hands each event to exactly ONE getter. Two live turn
coordinators on the same session therefore SPLIT the SSE stream between
them: text deltas arrive halved (an answer that starts mid-sentence), and a
tool_use whose ``input_json_delta`` chunks landed in the other reader emits
with empty args — which makes an MCP wrapper fall back to its raw
``use_tool`` name. These tests pin the claim/evict protocol that prevents it.
"""

import threading
import time

import pytest

from core.llm_providers.claude_code_interactive import _CCITurnCoordinator
from services.cc_interactive_event_service import (
    CCIConsumerEvicted, CCInteractiveEventService)


def _service() -> CCInteractiveEventService:
    return CCInteractiveEventService({"token": "tok", "_service_id": "events"})


def test_request_claim_evicts_previous_consumer():
    svc = _service()
    svc.register_session("sess")
    first = svc.claim_consumer("sess")
    second = svc.claim_consumer("sess")
    assert first and second and second != first
    with pytest.raises(CCIConsumerEvicted):
        svc.wait_event("sess", timeout=0, epoch=first)
    assert svc.wait_event("sess", timeout=0, epoch=second) == {}


def test_capture_claim_refused_while_request_polls():
    # The orphan-turn safety net must never take the stream away from the
    # real turn the user is waiting on.
    svc = _service()
    svc.register_session("sess")
    epoch = svc.claim_consumer("sess")
    svc.wait_event("sess", timeout=0, epoch=epoch)
    assert svc.claim_consumer("sess", kind="capture") == 0
    assert svc.wait_event("sess", timeout=0, epoch=epoch) == {}


def test_capture_claim_granted_without_listener():
    svc = _service()
    state = svc.register_session("sess")
    state.last_wait_at = time.time() - 3600
    assert svc.claim_consumer("sess", kind="capture") > 0


# ── A claim that has not polled yet still owns the stream ────────────
#
# The provider claims BEFORE it sends: the send blocks on TUI readiness (up
# to 45s on a cold TUI), paste, double Enter and submit verification, and
# run() only starts polling afterwards. A request_start arriving inside that
# window looked like a turn nobody was reading -- the orphan net captured it,
# the capture claim bumped the epoch, and the real coordinator died with
# CCIConsumerEvicted on its very first wait. That is how a cold Codex TUI
# launch failed: claim at T, orphan capture at T+13s, evicted at T+22s.


def _codex_session(svc):
    return svc.register_session(
        "sess", user_id="u1", conversation_id="c1", agent_name="assistant",
        provider="codex-interactive")


_CODEX_REQUEST_START = {
    "type": "request_start", "request_id": "r1",
    "path": "/backend-api/codex/responses",
}


def test_orphan_capture_refused_while_a_claim_has_not_polled_yet(monkeypatch):
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    epoch = svc.claim_consumer("sess")
    assert state.last_wait_at == 0.0  # the coordinator is still in send_text
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))

    assert captured == []
    # And the coordinator that owns the turn still owns it.
    assert svc.wait_event("sess", timeout=0, epoch=epoch)["request_id"] == "r1"


def test_orphan_capture_granted_once_the_claim_grace_expired(monkeypatch):
    # The grace is a ceiling, not an amnesty: a coordinator that claimed and
    # never came back must not disable the safety net for good.
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    svc.claim_consumer("sess")
    state.last_request_claim_at = (
        time.time() - CCInteractiveEventService._REQUEST_CLAIM_GRACE_SECONDS - 1)
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))

    assert captured == ["sess"]


def test_a_failed_send_releases_the_stream_for_the_orphan_net(monkeypatch):
    """The user's repro: the paste failed, they pressed Enter themselves, and
    the webchat stayed empty for the whole turn.

    The claim is taken before the send and suppresses the net for two
    minutes. A send that fails builds no coordinator, so nothing ever came
    to read the stream -- and nothing withdrew the claim either. The turn the
    user started by hand 30s later was streamed by the proxy to a stream
    marked as owned by a coordinator that did not exist.
    """
    svc = _service()
    _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    svc.remember_injected_prompt("sess", "the prompt PawFlow pasted")
    epoch = svc.claim_consumer("sess")
    # ... send_text returns False, the provider raises, and releases.
    svc.release_consumer("sess", epoch)
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))

    assert captured == ["sess"]


def test_release_leaves_a_newer_claim_alone(monkeypatch):
    """A failed send releasing after the next turn already claimed would
    hand that live turn to the orphan net."""
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    stale = svc.claim_consumer("sess")
    svc.claim_consumer("sess")
    svc.release_consumer("sess", stale)
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))

    assert captured == []
    assert state.last_request_claim_at > 0.0


# ── the rule: what crosses the wire reaches the webchat ────────────────────
#
# Every other test above arbitrates between readers. This one is about there
# being no reader at all: the proxy publishes a real turn, nothing takes it out
# of the queue, and the webchat shows nothing while the tmux visibly works.
# Releasing the claim of a failed send closed one way of getting there. The
# queue itself is the only fact that closes all of them.


def _age_pending(svc, state, seconds):
    state.oldest_pending_at = time.time() - seconds


def test_events_nobody_takes_out_of_the_queue_are_adopted(monkeypatch):
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    # A reader that started reading and then stopped, leaving the rest of the
    # stream in the queue. Its claim is older than its last poll, so nothing
    # says it is still coming -- the case the rule exists for.
    #
    # A claim NEWER than the last poll is deliberately not this case: that
    # coordinator is inside its send, and `claim_consumer` refuses the capture
    # on that ground. Adopting anyway spawned a capture per sweep tick that was
    # refused, consumed nothing and left the queue stale for the next one.
    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.publish_event("sess", dict(_CODEX_REQUEST_START, request_id="r2"))
    svc.wait_event("sess", timeout=0, epoch=epoch)
    assert captured == []

    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"]


def test_a_reader_that_drains_is_never_adopted_from_under(monkeypatch):
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    assert svc.wait_event("sess", timeout=0, epoch=epoch)["request_id"] == "r1"
    # Nothing is waiting, so there is nothing to declare unread.
    assert state.oldest_pending_at == 0.0
    svc._adopt_if_undelivered(state)
    assert captured == []


def test_taking_one_event_does_not_clear_the_rest(monkeypatch):
    """A consumer that takes one event and dies leaves the rest waiting, and
    the rest is what the rule is about."""
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.publish_event("sess", {"type": "sse", "payload": {"type": "x"}})
    svc.wait_event("sess", timeout=0, epoch=epoch)
    assert state.oldest_pending_at > 0.0

    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"]


def test_the_sweeper_notices_a_burst_that_went_quiet(monkeypatch):
    """The case checking on publish alone cannot see: a turn that streams in
    five seconds and then stops. No further event will ever come to notice
    that none of them were delivered."""
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.publish_event("sess", dict(_CODEX_REQUEST_START, request_id="r2"))
    # It read the burst's first event and went quiet, so the claim is no
    # longer newer than the last poll: nobody is coming back for the rest.
    svc.wait_event("sess", timeout=0, epoch=epoch)
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)
    # One sweep pass, run directly rather than waiting on its thread.
    for st in list(svc._sessions.values()):
        svc._adopt_if_undelivered(st)
    assert captured == ["sess"]


def test_a_drain_clears_the_undelivered_clock():
    """drain_session discards the pre-turn backlog on purpose; those events are
    not owed to anyone and must not trip the rule."""
    svc = _service()
    state = _codex_session(svc)
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    assert state.oldest_pending_at > 0.0
    svc.drain_session("sess")
    assert state.oldest_pending_at == 0.0


def test_capture_claim_does_not_stamp_the_request_claim_clock():
    # Only a request claim marks the stream as owned by a turn; a capture
    # claim recording it would make the net immune to its own adoption.
    svc = _service()
    state = svc.register_session("sess")
    state.last_wait_at = time.time() - 3600
    assert svc.claim_consumer("sess", kind="capture") > 0
    assert state.last_request_claim_at == 0.0


def test_the_net_may_not_evict_a_coordinator_still_inside_its_send():
    """Observed on codex-interactive, 16:36 -> 16:37 in production.

    The coordinator claimed at 16:36:26 and was still inside its send at
    16:36:58 -- the TUI was slow to come up ("prompt not detected ready",
    submitted best-effort at 16:37:17), so it had not polled once while the
    proxy's events piled up. The undelivered backstop declared the stream
    unread after 25s and the capture took it; the real coordinator then died
    on its very first read:

        LLM call failed (iter 1): CCIConsumerEvicted:
        CC interactive session taken over by a newer consumer

    The tmux kept working and the capture kept writing rows, so the webchat
    showed the whole turn while active-agents and the gauge were dead for it.
    A claim newer than the last poll is a coordinator that has not started
    reading, not one that is gone.
    """
    svc = _service()
    state = _codex_session(svc)

    epoch = svc.claim_consumer("sess")
    # Still sending: it has claimed and never polled, and its events wait.
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    state.last_wait_at = 0.0
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)

    assert svc.claim_consumer("sess", kind="capture") == 0
    assert state.consumer_epoch == epoch
    # And the coordinator that finally polls still owns the stream.
    assert svc.wait_event("sess", timeout=0, epoch=epoch)["request_id"] == "r1"


def test_the_undelivered_rule_does_not_storm_a_coordinator_still_sending(
        monkeypatch):
    """Production trace, codex conversation starting up:

        19:46:07  events undelivered; capturing orphan turn
        19:46:07  [codex-interactive] session=... already has an event consumer
        19:46:07  captured turn streamed: chars=0
        19:46:07  publish thinking / publish active_released
        19:46:12  ... the same, and again at :17, :22, :27
        19:46:25  [cci] TUI prompt not detected ready before first send

    The coordinator claimed at 19:45:4x and was still inside its send -- the
    TUI took forty-five seconds to accept the prompt. Every sweep tick forced
    an adoption past that grace, `claim_consumer` refused it on exactly that
    ground, and the refused capture consumed nothing -- so the queue stayed
    stale and the next tick repeated it. Each round raised and dropped the
    active-agent marker: the flicker the user sees.
    """
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    svc.claim_consumer("sess")          # claimed, and still inside send_text
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    assert state.last_wait_at == 0.0
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)

    for _ in range(5):                  # five sweep ticks
        svc._adopt_if_undelivered(state)
    assert captured == [], "the stream is owned; there is nothing to adopt"

    # The grace is still a ceiling, not an amnesty.
    state.last_request_claim_at = (
        time.time() - CCInteractiveEventService._REQUEST_CLAIM_GRACE_SECONDS - 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"]


def test_a_refused_capture_never_raises_the_active_agent_marker(monkeypatch):
    """The marker was raised before the claim was even attempted, so a capture
    that yields still blinked active-agents on and off."""
    svc = _service()
    state = _codex_session(svc)
    marks = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_publish_capture_active",
        lambda self, st, *, active: marks.append(active))

    # A live request consumer owns the stream: the capture claim is refused.
    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.wait_event("sess", timeout=0, epoch=epoch)

    svc._run_manual_capture("sess")

    assert marks == [], "a capture that never owned the stream says nothing"
    assert state.consumer_epoch == epoch, "and it does not evict the real turn"


def test_a_finished_turn_is_not_adopted_by_the_undelivered_rule(monkeypatch):
    """Production trace, 20:00:31 -> 20:01:07, one conversation:

        20:00:31  CC interactive hook event: hook=Stop
        20:00:34  CC interactive captured turn streamed: chars=3566
        20:00:34  publish active_released
        20:01:07  turn with no listening request (events undelivered);
                  capturing orphan turn

    The turn ended and released its marker; thirty-three seconds later the
    undelivered rule adopted it again and active-agents came back up for good.
    Nothing drains a session's queue at the END of a turn -- ``drain_session``
    runs when the NEXT turn claims -- so the post-Stop tail sat there and the
    rule read it as a stream nobody is reading. A capture then waited for a
    Stop that had already happened.

    The rule itself arrived in beta.77, which is why this became systematic.
    """
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    # A turn runs, watched by its coordinator, and ends on a Stop.
    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.wait_event("sess", timeout=0, epoch=epoch)
    svc.publish_event("sess", {"type": "hook", "hook_event_name": "Stop"})
    assert captured == []

    # Its post-Stop tail is still in the queue and nobody polls any more --
    # nothing drains until the next turn claims.
    state.last_wait_at = 0.0
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 10)

    svc._adopt_if_undelivered(state)
    assert captured == [], "a turn that already ended must not be re-adopted"

    # The net is armed again the moment a real turn starts -- here one nobody
    # is reading: the coordinator that ran the last turn is gone and its claim
    # withdrawn, so nothing owns the stream.
    svc.release_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START, request_id="r2"))
    captured.clear()  # that publish adopts on its own path; this tests the rule
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"], "a genuine unread turn is still adopted"


def test_a_late_stop_does_not_close_the_turn_that_started_after_it(monkeypatch):
    """The two kinds of boundary event do not share a route.

    The proxy holds ONE persistent event socket for a session; the hook opens
    its own connection per invocation, sends a single frame and closes it. A
    Stop can therefore be published after the next turn's request_start, and
    applying the events in arrival order marked the new turn as already over.
    That disarms the backstop for the whole turn: the answer sits in the queue
    with nobody reading it and no capture is ever spawned -- the webchat stays
    silent while the tmux works, which is the exact failure the rule exists to
    prevent.

    Timestamps say what arrival order cannot: the Stop belongs to the turn
    before.
    """
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    now = time.time()
    # A new turn starts, and nobody is reading its stream.
    svc.publish_event("sess", dict(_CODEX_REQUEST_START, timestamp=now))
    captured.clear()  # that publish adopts on its own path; this tests the rule
    # The previous turn's Stop, delayed on its own connection, lands after it.
    svc.publish_event("sess", {"type": "hook", "hook_event_name": "Stop",
                               "timestamp": now - 100})
    assert not state.turn_over, "a Stop older than the running turn is history"

    # The answer to the new turn is streamed and nobody takes it out.
    svc.publish_event("sess", {"type": "assistant_text", "text": "hi",
                               "timestamp": now})
    captured.clear()
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)
    svc._adopt_if_undelivered(state)
    assert captured == ["sess"], "the running turn must still be adoptable"


def test_boundary_comparison_and_update_are_atomic_across_event_sockets():
    """A hook handler must not pass the timestamp check while the proxy handler
    is committing a newer boundary on the same session."""
    svc = _service()
    state = _codex_session(svc)
    now = time.time()
    old_stop_finished = threading.Event()

    def publish_old_stop():
        svc._track_turn_boundary(state, {
            "type": "hook",
            "hook_event_name": "Stop",
            "timestamp": now - 100,
        })
        old_stop_finished.set()

    # Both boundary handlers must use this same lock. Hold it while the stale
    # hook starts, then commit the newer request_start re-entrantly. Once the
    # hook is allowed through it must see the newer timestamp and do nothing.
    with state.stream_condition:
        thread = threading.Thread(target=publish_old_stop)
        thread.start()
        assert not old_stop_finished.wait(0.2), (
            "a boundary update escaped the session's critical section")
        svc._track_turn_boundary(
            state, dict(_CODEX_REQUEST_START, timestamp=now))

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert not state.turn_over
    assert state.turn_boundary_at == now


def test_the_stop_of_the_running_turn_still_closes_it(monkeypatch):
    """The ordering guard must not make Stop unable to end anything: a Stop
    newer than the request_start it follows is that turn's own end."""
    svc = _service()
    state = _codex_session(svc)
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: None)

    now = time.time()
    svc.publish_event("sess", dict(_CODEX_REQUEST_START, timestamp=now - 10))
    svc.publish_event("sess", {"type": "hook", "hook_event_name": "Stop",
                               "timestamp": now})
    assert state.turn_over


def test_a_tmux_prompt_after_a_stop_rearms_the_undelivered_rule(monkeypatch):
    """A human typing in the tmux starts a turn no coordinator is watching --
    the case the net exists for. The Stop before it must not mute that."""
    svc = _service()
    state = _codex_session(svc)
    captured = []
    monkeypatch.setattr(
        CCInteractiveEventService, "_start_manual_capture",
        lambda self, st: captured.append(st.session_token))

    svc.publish_event("sess", {"type": "hook", "hook_event_name": "Stop"})
    svc.publish_event("sess", {"type": "hook",
                               "hook_event_name": "UserPromptSubmit",
                               "input": {"pawflow_managed_prompt": True}})
    state.last_wait_at = 0.0
    _age_pending(svc, state,
                 CCInteractiveEventService._UNDELIVERED_ADOPT_SECONDS + 1)

    svc._adopt_if_undelivered(state)
    assert captured == ["sess"]


def test_an_evicted_coordinator_does_not_refresh_the_freshness_clock():
    """The way the grace above was still bypassed in production.

    Reported after a compact on codex-interactive: the block and active-agents
    opened, closed ten seconds later, then flickered while the webchat kept
    filling in -- the turn read "Completed" with the tmux visibly working.

    ``wait_event`` stamped ``last_wait_at`` before checking the epoch, so a
    coordinator that had ALREADY been evicted refreshed the clock on its way
    to its own exception. That stamp sits newer than the incoming claim, which
    is precisely the shape ``claim_consumer`` reads as "has polled since
    claiming" -- so the claim grace stopped applying, and once the freshness
    window expired the net was granted a capture against a live turn. The new
    owner then died on its first read, and the capture kept the rows flowing
    without the active-agent and gauge that belong to a coordinator.
    """
    svc = _service()
    state = _codex_session(svc)

    first = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.wait_event("sess", timeout=0, epoch=first)

    # A newer turn takes the stream and is still inside its own send.
    second = svc.claim_consumer("sess")
    assert second != first
    stamped = state.last_wait_at

    with pytest.raises(CCIConsumerEvicted):
        svc.wait_event("sess", timeout=0, epoch=first)
    assert state.last_wait_at == stamped, (
        "an evicted consumer must not refresh the freshness clock")

    # Age both clocks past the freshness window, their order intact: the new
    # owner has claimed, has not polled, and is well inside its claim grace.
    shift = CCInteractiveEventService._LISTENER_FRESH_SECONDS + 1
    state.last_wait_at -= shift
    state.last_request_claim_at -= shift
    assert state.last_request_claim_at > state.last_wait_at

    assert svc.claim_consumer("sess", kind="capture") == 0
    assert state.consumer_epoch == second
    assert svc.wait_event("sess", timeout=0, epoch=second) == {}


def test_a_send_that_never_polls_still_loses_the_stream_eventually():
    """The grace defers the net, it does not disable it: a coordinator that
    dies inside its send without releasing must not mute the net forever, or
    the turn it was holding stays invisible."""
    svc = _service()
    state = _codex_session(svc)

    svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    state.last_wait_at = 0.0
    state.last_request_claim_at = (
        time.time() - CCInteractiveEventService._REQUEST_CLAIM_GRACE_SECONDS - 1)

    assert svc.claim_consumer("sess", kind="capture") > 0


def test_a_coordinator_that_has_polled_is_governed_by_freshness_alone():
    """Once it reads, the claim stops shielding it: a reader that then goes
    quiet past _LISTENER_FRESH_SECONDS is the case the net was built for."""
    svc = _service()
    state = _codex_session(svc)

    epoch = svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
    svc.wait_event("sess", timeout=0, epoch=epoch)
    # It polled after claiming -- the ordering that says the send is over --
    # and has since gone quiet past the freshness window.
    now = time.time()
    state.last_request_claim_at = now - 10
    state.last_wait_at = (
        now - CCInteractiveEventService._LISTENER_FRESH_SECONDS - 1)
    assert state.last_wait_at > state.last_request_claim_at

    assert svc.claim_consumer("sess", kind="capture") > 0


def test_coordinator_yields_when_claim_refused():
    class _Refusing:
        def claim_consumer(self, session_token, kind="request"):
            return 0

        def wait_event(self, session_token, timeout=None, epoch=0):
            raise AssertionError("a refused consumer must not read the queue")

    resp = _CCITurnCoordinator(_Refusing(), "sess", consumer_kind="capture").run()
    assert resp.content == ""


def test_coordinator_stops_when_evicted_mid_turn():
    # Losing the stream must surface as an error, never as a silently
    # truncated answer assembled from the half of the events we still won.
    svc = _service()
    svc.register_session("sess")
    coord = _CCITurnCoordinator(svc, "sess")
    assert coord._consumer_epoch
    svc.claim_consumer("sess")
    with pytest.raises(CCIConsumerEvicted):
        coord.run()


def test_coordinator_reuses_epoch_claimed_by_caller():
    # The provider claims BEFORE draining so a stale reader is evicted first;
    # the coordinator must reuse that epoch rather than claiming a new one.
    svc = _service()
    svc.register_session("sess")
    epoch = svc.claim_consumer("sess")
    coord = _CCITurnCoordinator(svc, "sess", consumer_epoch=epoch)
    assert coord._consumer_epoch == epoch
    assert svc.session_state("sess").consumer_epoch == epoch


# ── Eviction while already parked in get() ───────────────────────────
#
# The epoch was checked only on the way in. A coordinator already blocked in
# `events.get()` when a newer consumer claimed the stream was still the one
# queue.Queue woke, so it took the first event of the NEW owner's turn and
# dropped it on its way out -- truncated text, or a tool call severed from
# its arguments.


def test_an_event_delivered_to_an_evicted_consumer_is_not_lost():
    import threading

    svc = _service()
    svc.register_session("sess")
    first = svc.claim_consumer("sess")

    outcome = {}

    def _old_consumer():
        try:
            outcome["event"] = svc.wait_event("sess", timeout=5, epoch=first)
        except CCIConsumerEvicted:
            outcome["evicted"] = True

    parked = threading.Thread(target=_old_consumer, daemon=True)
    parked.start()
    # Let it reach the blocking get() before anything else happens.
    deadline = time.time() + 2
    while (svc.session_state("sess").last_wait_at == 0.0
           and time.time() < deadline):
        time.sleep(0.005)

    second = svc.claim_consumer("sess")
    assert second != first
    svc.publish_event("sess", {"type": "first-of-the-new-turn"})
    parked.join(timeout=5)

    assert outcome.get("evicted"), "the old consumer must not be served"
    assert "event" not in outcome
    # The whole point: the new owner still gets it.
    assert svc.wait_event("sess", timeout=2,
                          epoch=second)["type"] == "first-of-the-new-turn"


def test_claim_wakes_old_waiter_and_replacement_keeps_event_order():
    import threading

    svc = _service()
    svc.register_session("sess")
    first = svc.claim_consumer("sess")
    old_evicted = threading.Event()

    def _old_consumer():
        with pytest.raises(CCIConsumerEvicted):
            svc.wait_event("sess", timeout=5, epoch=first)
        old_evicted.set()

    old = threading.Thread(target=_old_consumer, daemon=True)
    old.start()
    deadline = time.time() + 2
    while (svc.session_state("sess").last_wait_at == 0.0
           and time.time() < deadline):
        time.sleep(0.005)

    second = svc.claim_consumer("sess")
    assert old_evicted.wait(1), "the claim itself must wake the evicted waiter"

    received = []

    def _replacement():
        received.append(svc.wait_event("sess", timeout=2, epoch=second)["seq"])
        received.append(svc.wait_event("sess", timeout=2, epoch=second)["seq"])

    replacement = threading.Thread(target=_replacement, daemon=True)
    replacement.start()
    svc.publish_event("sess", {"seq": 1})
    svc.publish_event("sess", {"seq": 2})
    replacement.join(timeout=3)

    assert received == [1, 2]


def test_a_handed_back_event_is_taken_before_the_queue():
    svc = _service()
    svc.register_session("sess")
    state = svc.session_state("sess")
    epoch = svc.claim_consumer("sess")

    state.pushback.append({"type": "handed-back"})
    svc.publish_event("sess", {"type": "queued-after"})

    assert svc.wait_event("sess", timeout=1, epoch=epoch)["type"] == "handed-back"
    assert svc.wait_event("sess", timeout=1, epoch=epoch)["type"] == "queued-after"


def test_a_drain_takes_the_handed_back_event_too():
    svc = _service()
    svc.register_session("sess")
    state = svc.session_state("sess")
    epoch = svc.claim_consumer("sess")

    state.pushback.append({"type": "stale"})
    svc.publish_event("sess", {"type": "also-stale"})

    assert svc.drain_session("sess") == 2
    assert svc.wait_event("sess", timeout=0, epoch=epoch) == {}


def test_service_without_arbitration_is_unenforced():
    # Test doubles (and any event service that does not arbitrate) keep the
    # original wait_event signature: no epoch is sent.
    seen = []

    class _Plain:
        def wait_event(self, session_token, timeout=None):
            seen.append(timeout)
            return {"type": "hook", "hook_event_name": "Stop",
                    "input": {"hook_event_name": "Stop"}}

    coord = _CCITurnCoordinator(_Plain(), "sess")
    assert coord._consumer_epoch == 0
    assert coord._consumer_refused is False
    coord._wait_event(0.25)
    assert seen == [0.25]
