"""Tests for core.pending_queue — persistent per-(conv, agent) ingress queue."""

import json
import threading
from unittest.mock import patch

import pytest

from core.pending_queue import PendingQueue


@pytest.fixture
def fake_store(tmp_path):
    """Fake ConversationStore that returns tmp_path/convs/{cid} as the conv dir."""
    class _FakeStore:
        _store_dir = tmp_path / "convs"

        def _conv_dir(self, cid, user_id=""):
            d = self._store_dir / "u" / cid
            d.mkdir(parents=True, exist_ok=True)
            return d

    store_root = tmp_path / "convs" / "u"
    store_root.mkdir(parents=True, exist_ok=True)
    with patch("core.conversation_store.ConversationStore.instance",
                return_value=_FakeStore()):
        PendingQueue.drop_cache()
        yield _FakeStore
    PendingQueue.drop_cache()


def _msg(content: str, msg_id: str, seq: int, ts: float = 1234.5):
    return {"role": "user", "content": content,
            "msg_id": msg_id, "seq": seq, "ts": ts}


def test_enqueue_drain_roundtrip(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    assert q.peek_count() == 0
    q.enqueue(_msg("hello", "m1", 1), source="http")
    q.enqueue(_msg("follow-up", "m2", 2), source="http")
    assert q.peek_count() == 2
    drained = q.drain()
    assert len(drained) == 2
    assert drained[0]["msg_id"] == "m1"
    assert drained[1]["msg_id"] == "m2"
    assert drained[0]["_pending_source"] == "http"
    # Drain is destructive
    assert q.peek_count() == 0
    assert q.drain() == []


def test_clear_drops_pending_without_replay(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("stop me", "m1", 1), source="http")
    q.enqueue(_msg("also stop", "m2", 2), source="http")

    assert q.clear("force_stop") == 2
    assert q.peek_count() == 0
    assert q.drain() == []


def test_unstamped_message_rejected(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    # Missing msg_id → rejected
    with pytest.raises(ValueError, match="must be stamped"):
        q.enqueue({"role": "user", "content": "no id"})
    # Missing ts → rejected
    with pytest.raises(ValueError, match="must be stamped"):
        q.enqueue({"role": "user", "content": "no ts",
                   "msg_id": "x"})
    # seq absent is fine — it's assigned by _stamp_line at write time
    q.enqueue({"role": "user", "content": "no seq required",
               "msg_id": "y", "ts": 1.0})


def test_restart_recovery(fake_store):
    """Queue survives process restart (simulated by dropping singleton cache)."""
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("survives reboot", "m1", 1))
    assert q.peek_count() == 1
    # Simulate process death + restart: new instance, same path
    PendingQueue.drop_cache()
    q2 = PendingQueue.for_agent("c1", "claude")
    assert q2 is not q
    assert q2.peek_count() == 1
    drained = q2.drain()
    assert len(drained) == 1
    assert drained[0]["content"] == "survives reboot"


def test_per_conv_and_agent_isolation(fake_store):
    a_claude = PendingQueue.for_agent("c1", "claude")
    a_qwen = PendingQueue.for_agent("c1", "qwen")
    b_claude = PendingQueue.for_agent("c2", "claude")
    a_claude.enqueue(_msg("for claude in c1", "m1", 1))
    a_qwen.enqueue(_msg("for qwen in c1", "m2", 2))
    b_claude.enqueue(_msg("for claude in c2", "m3", 3))
    assert a_claude.drain()[0]["msg_id"] == "m1"
    assert a_qwen.drain()[0]["msg_id"] == "m2"
    assert b_claude.drain()[0]["msg_id"] == "m3"


def test_corrupt_line_skipped(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("first", "m1", 1))
    # Write a corrupt line manually
    with open(q._path, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
    q.enqueue(_msg("third", "m3", 3))
    drained = q.drain()
    assert len(drained) == 2  # corrupt line skipped, valid ones kept
    assert {m["msg_id"] for m in drained} == {"m1", "m3"}


def test_concurrent_enqueue(fake_store):
    q = PendingQueue.for_agent("c1", "claude")

    def _enq(idx):
        for j in range(10):
            q.enqueue(_msg(f"msg-{idx}-{j}", f"m_{idx}_{j}", idx * 100 + j + 1))

    threads = [threading.Thread(target=_enq, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    drained = q.drain()
    assert len(drained) == 50
    ids = {m["msg_id"] for m in drained}
    assert len(ids) == 50


def test_discard_msg_ids_removes_only_matching_entries(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("already compacted", "m1", 1), source="preempt_rescue")
    q.enqueue(_msg("still pending", "m2", 2), source="http")

    assert q.discard_msg_ids({"m1"}) == 1
    drained = q.drain()
    assert [m["msg_id"] for m in drained] == ["m2"]


def test_discard_msg_ids_can_filter_by_source(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("rescue", "m1", 1), source="preempt_rescue")
    q.enqueue(_msg("normal", "m2", 2), source="http")

    assert q.discard_msg_ids({"m1", "m2"}, sources={"preempt_rescue"}) == 1
    drained = q.drain()
    assert [m["msg_id"] for m in drained] == ["m2"]


def test_all_nonempty_scan(fake_store):
    PendingQueue.for_agent("c1", "claude").enqueue(_msg("a", "m1", 1))
    PendingQueue.for_agent("c1", "qwen").enqueue(_msg("b", "m2", 2))
    PendingQueue.for_agent("c2", "claude").enqueue(_msg("c", "m3", 3))
    # c3 has no pending — should not appear
    found = PendingQueue.all_nonempty()
    found_norm = {(cid, agent, n) for cid, agent, n in found}
    assert ("c1", "claude", 1) in found_norm
    assert ("c1", "qwen", 1) in found_norm
    assert ("c2", "claude", 1) in found_norm


def test_singleton_per_key(fake_store):
    a = PendingQueue.for_agent("c1", "claude")
    b = PendingQueue.for_agent("c1", "claude")
    c = PendingQueue.for_agent("c1", "Claude")  # case-insensitive
    d = PendingQueue.for_agent("c1", "qwen")
    assert a is b
    assert a is c
    assert a is not d
