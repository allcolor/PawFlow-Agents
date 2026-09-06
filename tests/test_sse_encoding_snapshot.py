"""Wire snapshot regressions for shared conversation SSE events."""

import json
import threading

import pytest

from core.conversation_event_bus import ConversationEventBus
from core.sse_writer import SSEEvent


class StringPayload:
    def __str__(self):
        return "custom payload"

    def __deepcopy__(self, memo):
        raise TypeError("String-compatible payloads need not support deepcopy")


@pytest.mark.parametrize("data,event_id,expected", [
    ({"text": "é", "items": [1, None]}, "42",
     'id: 42\nevent: custom\ndata: {"text": "é", "items": [1, null]}\n\n'.encode()),
    ([1, "é"], None, 'event: custom\ndata: [1, "é"]\n\n'.encode()),
    ("first\nsecond\n", "", b"event: custom\ndata: first\ndata: second\ndata: \n\n"),
    ("", None, b"event: custom\ndata: \n\n"),
    (None, None, b"event: custom\ndata: None\n\n"),
    (False, None, b"event: custom\ndata: False\n\n"),
    (StringPayload(), None, b"event: custom\ndata: custom payload\n\n"),
])
def test_shared_snapshot_has_exact_wire_parity(data, event_id, expected, monkeypatch):
    bus = ConversationEventBus()
    try:
        writers = [bus.subscribe("conv") for _ in range(4)]
        calls = []
        dumps = json.dumps

        def counted(*args, **kwargs):
            calls.append(1)
            return dumps(*args, **kwargs)

        monkeypatch.setattr("core.sse_writer.json.dumps", counted)
        event = SSEEvent("custom", data, event_id)
        bus.publish("conv", event)
        event.event = "changed"
        event.id = "changed"
        if isinstance(data, dict):
            data["items"].append("changed")
        chunks = [writer.drain_nowait()[0] for writer in writers]
        assert chunks == [expected] * 4
        assert all(chunk is chunks[0] for chunk in chunks)
        assert len(calls) == int(isinstance(data, (dict, list)))
    finally:
        bus._cleanup_all()


@pytest.mark.parametrize("replay", [False, True])
def test_listener_mutation_cannot_change_live_or_replayed_snapshot(replay):
    bus = ConversationEventBus()
    mutated = threading.Event()
    release = threading.Event()
    payload = {"nested": {"items": [1]}, "ts": 123}
    expected = SSEEvent("custom", payload).encode()

    def listener(cid, kind, data):
        assert (cid, kind) == ("conv", "custom")
        assert isinstance(data, dict)
        data["nested"]["items"].append(2)
        mutated.set()
        release.wait(2)

    try:
        bus.add_listener(listener)
        writer = None if replay else bus.subscribe("conv")
        bus.publish_event("conv", "custom", payload)
        assert mutated.wait(2)
        if replay:
            writer = bus.subscribe("conv")
        bus.publish("conv", SSEEvent("custom", "tail", "next"))
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert chunks == [expected, b"id: next\nevent: custom\ndata: tail\n\n"]
    finally:
        release.set()
        bus._cleanup_all()
        bus._listener_dispatcher._pool.shutdown(wait=True)


def test_overflow_reconnect_retains_the_original_wire_snapshot():
    bus = ConversationEventBus()
    try:
        writer = bus.subscribe("conv")
        writer.close()
        payload = {"nested": [1]}
        bus.publish("conv", SSEEvent("custom", payload, "original"))
        payload["nested"].append(2)
        replay = bus.subscribe("conv", client_id="reconnect")
        assert replay.drain_nowait() == [
            b'id: original\nevent: custom\ndata: {"nested": [1]}\n\n']
    finally:
        bus._cleanup_all()


def test_source_and_event_classification_are_captured_before_buffering():
    bus = ConversationEventBus()
    try:
        payload = {"source": {"type": "agent", "name": "assistant"}}
        event = SSEEvent("done", payload, "original")
        bus.publish("conv", event)
        event.event = "error_event"
        event.id = "changed"
        payload["source"]["type"] = "user"
        payload["source"]["name"] = "changed"
        snapshot = bus._buffer["conv"][0][1]
        assert snapshot.event == "done"
        assert snapshot.id == "original"
        assert snapshot.data["source"] == {"type": "agent", "name": "assistant"}
        assert snapshot.encode() == (
            b'id: original\nevent: done\ndata: {"source": '
            b'{"type": "agent", "name": "assistant"}}\n\n')
    finally:
        bus._cleanup_all()
