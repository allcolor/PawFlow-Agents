"""Regression tests for off-thread listener dispatch in ConversationEventBus.

A slow in-process listener (e.g. the Telegram bridge doing a blocking HTTP
send) must not stall SSE event publishing for its own or any other
conversation, and per-conversation ordering must be preserved.
"""
import threading
import time

from core.conversation_event_bus import ConversationEventBus


def _new_bus():
    # Fresh instance so test listeners don't leak across the singleton.
    ConversationEventBus.reset()
    return ConversationEventBus.instance()


def test_slow_listener_does_not_block_publish_or_other_conversations():
    bus = _new_bus()
    try:
        release_a = threading.Event()
        a_started = threading.Event()
        seen = {"A": [], "B": []}

        def listener(cid, event_type, payload):
            if cid == "A" and payload == "0":
                a_started.set()
                # Simulate a hung Telegram send on conversation A.
                assert release_a.wait(5), "release never signalled"
            seen[cid].append(payload)

        bus.add_listener(listener)

        t0 = time.monotonic()
        for i in range(5):
            bus.publish_event("A", "new_message", str(i))
        # publish_event must return immediately even though A's listener blocks.
        assert time.monotonic() - t0 < 1.0
        assert a_started.wait(2), "dispatcher did not start draining A"

        for i in range(3):
            bus.publish_event("B", "new_message", str(i))

        # B must drain fully while A is still blocked.
        deadline = time.monotonic() + 3
        while seen["B"] != ["0", "1", "2"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert seen["B"] == ["0", "1", "2"], f"B blocked by slow A: {seen['B']}"
        assert seen["A"] == [], "A should still be blocked"

        # Release A; its events must arrive in order.
        release_a.set()
        deadline = time.monotonic() + 3
        while seen["A"] != ["0", "1", "2", "3", "4"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert seen["A"] == ["0", "1", "2", "3", "4"], f"A out of order: {seen['A']}"
    finally:
        ConversationEventBus.reset()
