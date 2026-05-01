"""Tests for ConversationWriter.shutdown_all draining semantics.

Rationale: the writer runs on a daemon thread and enqueue() is
non-blocking. On process exit (os._exit from signal handler), the
daemon thread is killed instantly - any item still in the queue is
lost. shutdown_all() MUST drain every queue before letting callers
proceed to os._exit, otherwise we lose messages. This is not a
best-effort behavior; it is required for correctness.
"""

import threading
import time

import pytest

from core.conversation_writer import ConversationWriter


def _msg(role="assistant", content="x"):
    return {
        "role": role,
        "content": content,
        "ts": time.time(),
        "seq": int(time.time() * 1000),
        "msg_id": f"m-{time.time_ns()}",
    }


@pytest.fixture(autouse=True)
def _clear_instances():
    """Reset singleton state between tests so each test starts clean."""
    with ConversationWriter._global_lock:
        for w in ConversationWriter._instances.values():
            w._stop = True
        ConversationWriter._instances.clear()
    yield
    with ConversationWriter._global_lock:
        for w in ConversationWriter._instances.values():
            w._stop = True
        ConversationWriter._instances.clear()


class _FakeStore:
    def __init__(self):
        self.routed = []
        self.lock = threading.Lock()

    def append_message(self, cid, msg, agent_name="", user_id="", ttl=0):
        with self.lock:
            self.routed.append((cid, agent_name, dict(msg)))

    def instance(self):
        return self


@pytest.fixture
def fake_store(monkeypatch):
    fs = _FakeStore()
    from core import conversation_store as _cs
    monkeypatch.setattr(_cs.ConversationStore, "instance",
                        classmethod(lambda _c: fs))
    return fs


def test_shutdown_all_drains_pending_writes(fake_store):
    """Messages enqueued before shutdown_all must reach the store.
    Regression test for the shutdown data-loss bug: previously
    shutdown_all set _stop=True and cleared instances, which aborted
    the writer loop with items still in its queue."""
    cid = "conv-drain-1"
    w = ConversationWriter.for_conversation(cid)
    for i in range(20):
        w.enqueue_message(_msg(content=f"m{i}"))
    # Immediately request shutdown - there is no reason for the test to
    # wait for the writer to catch up naturally.
    ok = ConversationWriter.shutdown_all(wait_timeout=10.0)
    assert ok, "shutdown_all must return True when drain completes"
    assert len(fake_store.routed) == 20, (
        f"expected all 20 enqueues persisted, got "
        f"{len(fake_store.routed)}")


def test_shutdown_all_returns_false_on_timeout(fake_store, monkeypatch):
    """If drain budget is exhausted, shutdown_all returns False so the
    caller (cli._shutdown) can log data loss loudly instead of silently
    proceeding to os._exit."""
    slow = threading.Event()

    def _slow_append(cid, msg, agent_name="", user_id="", ttl=0):
        slow.wait(timeout=5.0)

    monkeypatch.setattr(fake_store, "append_message", _slow_append)
    cid = "conv-drain-3"
    w = ConversationWriter.for_conversation(cid)
    w.enqueue_message(_msg())
    ok = ConversationWriter.shutdown_all(wait_timeout=0.2)
    slow.set()
    assert ok is False, "must report drain failure when over budget"


def test_shutdown_all_handles_multiple_conversations(fake_store):
    """Each conversation has its own writer thread; drain must cover
    every one of them."""
    for i in range(5):
        w = ConversationWriter.for_conversation(f"conv-multi-{i}")
        w.enqueue_message(_msg(content=f"c{i}"))
    ok = ConversationWriter.shutdown_all(wait_timeout=5.0)
    assert ok
    assert len(fake_store.routed) == 5


def test_enqueue_message_routes_through_append_message(fake_store):
    """enqueue_message dispatches to store.append_message with agent_name,
    user_id, ttl forwarded verbatim. One call = one routed message."""
    cid = "conv-route-1"
    w = ConversationWriter.for_conversation(cid)
    m = _msg(role="user", content="hi")
    w.enqueue_message(m, agent_name="bot", user_id="u1", ttl=42)
    ok = ConversationWriter.shutdown_all(wait_timeout=5.0)
    assert ok
    assert len(fake_store.routed) == 1
    rcid, ragent, rmsg = fake_store.routed[0]
    assert rcid == cid
    assert ragent == "bot"
    assert rmsg["content"] == "hi"


def test_enqueue_message_requires_ts_and_seq(fake_store):
    """Missing ts/seq on a routed message is a producer bug -- must raise
    at enqueue time, not silently enqueue a corrupt record."""
    cid = "conv-route-2"
    w = ConversationWriter.for_conversation(cid)
    bad = {"role": "user", "content": "x", "msg_id": "m1"}
    with pytest.raises(ValueError):
        w.enqueue_message(bad)


def test_for_conversation_replaces_dead_writer(fake_store):
    cid = "conv-dead-writer"
    w = ConversationWriter.for_conversation(cid)
    w._alive = False
    w._stop = True

    replacement = ConversationWriter.for_conversation(cid)

    assert replacement is not w
    replacement.enqueue_message(_msg(content="after restart"))
    assert ConversationWriter.shutdown_all(wait_timeout=5.0)
    assert [row[2]["content"] for row in fake_store.routed] == ["after restart"]


def test_dead_writer_refuses_new_messages(fake_store):
    cid = "conv-dead-refuse"
    w = ConversationWriter.for_conversation(cid)
    w._alive = False
    w._stop = True

    with pytest.raises(RuntimeError):
        w.enqueue_message(_msg())


def test_enqueue_message_sse_fires_after_persist(fake_store):
    """SSE events must fire only AFTER append_message returns (visible
    implies persisted). Verify by ordering: routed entry exists before
    the SSE publish could observe it."""
    import core.conversation_event_bus as _ceb
    published = []

    class _FakeBus:
        def publish_event(self, cid, typ, data=None):
            # When SSE fires, the store must already have the message.
            published.append((cid, typ, len(fake_store.routed)))

        @classmethod
        def instance(cls):
            return cls()

    # Patch the singleton accessor.
    orig = _ceb.ConversationEventBus.instance
    _ceb.ConversationEventBus.instance = classmethod(
        lambda cls: _FakeBus())
    try:
        cid = "conv-route-3"
        w = ConversationWriter.for_conversation(cid)
        m = _msg(content="hey")
        w.enqueue_message(m, sse_events=[{"type": "new_message",
                                         "data": {"id": "x"}}])
        ok = ConversationWriter.shutdown_all(wait_timeout=5.0)
        assert ok
        assert len(published) == 1
        # At the moment of publish, the routed list already contained
        # the message (length >= 1).
        assert published[0][2] >= 1
    finally:
        _ceb.ConversationEventBus.instance = orig
