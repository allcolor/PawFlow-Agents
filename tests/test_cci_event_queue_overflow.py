"""A full event queue must never stop messages from reaching a CLI.

Incident 2026-10-06 (GameDev6, Codex): after its last Stop the idle TUI
polled /backend-api/wham/usage every few seconds, four events a poll, into a
queue nobody reads between turns. 4096 slots filled in 91 minutes, the session
was declared dead, and every later message -- pasted, submitted, and worked on
by the CLI -- failed its turn with "CC interactive event queue overflow".
"""

import pytest

from services.cc_interactive_event_service import CCInteractiveEventService


def _service(max_queue=4) -> CCInteractiveEventService:
    return CCInteractiveEventService(
        {"token": "tok", "_service_id": "events", "max_queue": max_queue})


def _usage_poll(index):
    return {"type": "request_start", "request_id": f"r{index}",
            "method": "GET", "path": "/backend-api/wham/usage"}


def test_idle_noise_between_turns_keeps_the_newest_events():
    svc = _service()
    state = svc.register_session("sess")
    state.turn_over = True

    for index in range(10):
        svc.publish_event("sess", _usage_poll(index), block=True)

    assert state.unreliable is False
    kept = [state.events.get_nowait()["request_id"] for _ in range(4)]
    assert kept == ["r6", "r7", "r8", "r9"]


def test_overflow_during_a_claimed_turn_still_fails_that_turn():
    svc = _service()
    state = svc.register_session("sess")
    state.turn_over = True
    epoch = svc.claim_consumer("sess")

    for index in range(4):
        svc.publish_event("sess", _usage_poll(index), block=False)
    with pytest.raises(RuntimeError, match="queue overflow"):
        svc.publish_event("sess", _usage_poll(4), block=False)
    with pytest.raises(RuntimeError, match="queue overflow"):
        svc.wait_event("sess", timeout=0, epoch=epoch)


def test_next_turn_recovers_a_session_after_overflow():
    svc = _service()
    state = svc.register_session("sess")
    svc.claim_consumer("sess")
    for index in range(4):
        svc.publish_event("sess", _usage_poll(index), block=False)
    with pytest.raises(RuntimeError):
        svc.publish_event("sess", _usage_poll(4), block=False)
    assert state.unreliable is True

    # What every provider does before pasting the next message.
    epoch = svc.claim_consumer("sess")
    assert svc.drain_session("sess") == 4

    assert state.unreliable is False
    assert state.error == ""
    svc.publish_event("sess", {"type": "hook",
                               "hook_event_name": "UserPromptSubmit"})
    event = svc.wait_event("sess", timeout=0, epoch=epoch)
    assert event["hook_event_name"] == "UserPromptSubmit"
