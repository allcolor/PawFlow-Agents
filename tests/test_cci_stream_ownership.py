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
