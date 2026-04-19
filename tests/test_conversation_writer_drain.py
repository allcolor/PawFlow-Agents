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
        self.appended = []
        self.flushed = []
        self.lock = threading.Lock()

    def append_messages(self, cid, msgs, user_id="", status=""):
        with self.lock:
            self.appended.append((cid, list(msgs)))

    def agent_flush(self, cid, agent, public_messages, private_messages,
                    user_id="", ttl=0):
        with self.lock:
            self.flushed.append((cid, agent,
                                 list(public_messages),
                                 list(private_messages)))

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
    This is the regression test for the shutdown data-loss bug:
    previously shutdown_all set _stop=True and cleared instances, which
    aborted the writer loop with items still in its queue."""
    cid = "conv-drain-1"
    w = ConversationWriter.for_conversation(cid)
    for i in range(20):
        w.enqueue([_msg(content=f"m{i}")])
    # Immediately request shutdown - there is no reason for the test to
    # wait for the writer to catch up naturally.
    ok = ConversationWriter.shutdown_all(wait_timeout=10.0)
    assert ok, "shutdown_all must return True when drain completes"
    assert len(fake_store.appended) == 20, (
        f"expected all 20 enqueues persisted, got "
        f"{len(fake_store.appended)}")


def test_shutdown_all_drains_agent_flush(fake_store):
    """agent_flush ops must drain too - same FIFO queue."""
    cid = "conv-drain-2"
    w = ConversationWriter.for_conversation(cid)
    w.enqueue_agent_flush("bot",
                          public_messages=[_msg(content="hi")],
                          private_messages=[])
    ok = ConversationWriter.shutdown_all(wait_timeout=5.0)
    assert ok
    assert len(fake_store.flushed) == 1


def test_shutdown_all_returns_false_on_timeout(fake_store, monkeypatch):
    """If drain budget is exhausted, shutdown_all returns False so the
    caller (cli._shutdown) can log data loss loudly instead of silently
    proceeding to os._exit."""
    slow = threading.Event()

    def _slow_append(cid, msgs, user_id="", status=""):
        slow.wait(timeout=5.0)

    monkeypatch.setattr(fake_store, "append_messages", _slow_append)
    cid = "conv-drain-3"
    w = ConversationWriter.for_conversation(cid)
    w.enqueue([_msg()])
    ok = ConversationWriter.shutdown_all(wait_timeout=0.2)
    slow.set()
    assert ok is False, "must report drain failure when over budget"


def test_shutdown_all_handles_multiple_conversations(fake_store):
    """Each conversation has its own writer thread; drain must cover
    every one of them."""
    for i in range(5):
        w = ConversationWriter.for_conversation(f"conv-multi-{i}")
        w.enqueue([_msg(content=f"c{i}")])
    ok = ConversationWriter.shutdown_all(wait_timeout=5.0)
    assert ok
    assert len(fake_store.appended) == 5
