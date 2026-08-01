"""Exclusive ownership of a CC interactive session's event stream.

``queue.Queue`` hands each event to exactly ONE getter. Two live turn
coordinators on the same session therefore SPLIT the SSE stream between
them: text deltas arrive halved (an answer that starts mid-sentence), and a
tool_use whose ``input_json_delta`` chunks landed in the other reader emits
with empty args — which makes an MCP wrapper fall back to its raw
``use_tool`` name. These tests pin the claim/evict protocol that prevents it.
"""

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
    state = _codex_session(svc)
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

    # A claim so fresh that every liveness guess says a reader is coming.
    svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
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

    svc.claim_consumer("sess")
    svc.publish_event("sess", dict(_CODEX_REQUEST_START))
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
