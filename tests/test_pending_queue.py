"""Tests for core.pending_queue — per-(conv, agent) queue on the inbox store."""

import json
import threading
from unittest.mock import patch

import pytest

from core.agent_inbox_store import AgentInboxStore
from core.pending_queue import PendingQueue


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    store = AgentInboxStore(tmp_path / "inbox.sqlite3")
    monkeypatch.setattr(AgentInboxStore, "_instance", store)
    return store


@pytest.fixture
def fake_store(tmp_path, inbox):
    """Fake ConversationStore that returns tmp_path/convs/u/{cid} as the conv dir."""
    class _FakeStore:
        _store_dir = tmp_path / "convs"
        extras = {}

        def _conv_dir(self, cid, user_id=""):
            d = self._store_dir / "u" / cid
            d.mkdir(parents=True, exist_ok=True)
            return d

        def get_extra(self, cid, key, default=None):
            return self.extras.get((cid, key), default)

    store_root = tmp_path / "convs" / "u"
    store_root.mkdir(parents=True, exist_ok=True)
    _FakeStore.extras = {}
    with patch("core.conversation_store.ConversationStore.instance",
               return_value=_FakeStore()):
        PendingQueue.drop_cache()
        yield _FakeStore
    PendingQueue.drop_cache()


def _msg(content: str, msg_id: str, seq: int, ts: float = 1234.5):
    return {"role": "user", "content": content,
            "msg_id": msg_id, "seq": seq, "ts": ts}


def _legacy_file(tmp_path, cid, agent, rows):
    path = tmp_path / "convs" / "u" / cid / agent / "pending.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")
    return path


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


def test_drain_and_peek_exclude_inbox_items(fake_store, inbox):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("user", "m1", 1), source="http")
    q.enqueue(_msg("delegate", "m2", 2), source="inbox:delegate_message")
    q.enqueue(_msg("later", "m3", 3), source="http")

    assert q.peek_count() == 2
    assert [m["msg_id"] for m in q.peek()] == ["m1", "m3"]
    assert [m["msg_id"] for m in q.drain()] == ["m1", "m3"]
    assert q.peek_count() == 0
    remaining = inbox.list_pending("c1", "claude")
    assert [item.msg_id for item in remaining] == ["m2"]
    assert (("c1", "claude", 1) in PendingQueue.all_nonempty())


def test_clear_drops_pending_without_replay(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("stop me", "m1", 1), source="http")
    q.enqueue(_msg("also stop", "m2", 2), source="http")

    assert q.clear("force_stop") == 2
    assert q.peek_count() == 0
    assert q.drain() == []


def test_clear_discards_inbox_items_too(fake_store, inbox):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("user", "m1", 1), source="http")
    q.enqueue(_msg("delegate", "m2", 2), source="inbox:delegate_message")

    assert q.clear("force_stop") == 2
    assert inbox.list_pending("c1", "claude") == ()
    assert {item.state for item in inbox.list_items("c1", "claude")} == {
        "discarded"}


def test_peek_lists_waiting_messages_without_consuming(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    assert q.peek() == []
    q.enqueue(_msg("first", "m1", 1), source="delegate_reply")
    q.enqueue(_msg("second", "m2", 2), source="http")

    waiting = q.peek()
    assert [m["msg_id"] for m in waiting] == ["m1", "m2"]
    assert waiting[0]["_pending_source"] == "delegate_reply"
    assert q.peek_count() == 2
    assert [m["msg_id"] for m in q.drain()] == ["m1", "m2"]
    assert q.peek() == []


@pytest.mark.parametrize("cutoff_key", [
    "last_force_stop_at",
    "last_force_stop_at:claude",
])
def test_enqueue_rejects_messages_created_before_force_stop(
        fake_store, cutoff_key):
    """A blocked producer cannot recreate work after force-stop cleanup."""
    fake_store.extras[("c1", cutoff_key)] = 20.0
    q = PendingQueue.for_agent("c1", "claude")

    assert q.enqueue(
        _msg("stale rescue", "m-stale", 1, ts=10.0),
        source="preempt_rescue") is False
    assert q.enqueue(
        _msg("stale delegate", "m-stale-inbox", 1, ts=10.0),
        source="inbox:delegate_message") is False
    assert q.peek_count() == 0

    assert q.enqueue(
        _msg("new message", "m-new", 2, ts=21.0),
        source="http") is True
    assert [msg["msg_id"] for msg in q.drain()] == ["m-new"]


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


def test_reenqueue_same_message_is_idempotent(fake_store):
    """A second enqueue only differs by _pending_* stamps: one entry."""
    q = PendingQueue.for_agent("c1", "claude")
    assert q.enqueue(_msg("hello", "m1", 1), source="http") is True
    assert q.enqueue(_msg("hello", "m1", 1), source="preempt_rescue") is True
    assert q.peek_count() == 1
    with pytest.raises(ValueError, match="different inbox payload"):
        q.enqueue(_msg("changed", "m1", 1), source="http")


def test_restart_recovery(fake_store):
    """Queue survives process restart (simulated by dropping singleton cache)."""
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("survives reboot", "m1", 1))
    assert q.peek_count() == 1
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


def test_drain_returns_more_than_one_batch_in_order(fake_store):
    q = PendingQueue.for_agent("c1", "claude")
    ids = [f"m{number:04d}" for number in range(1203)]
    for number, msg_id in enumerate(ids):
        q.enqueue(_msg("x", msg_id, number + 1), source="http")
    assert [m["msg_id"] for m in q.drain()] == ids
    assert q.peek_count() == 0


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
    found = set(PendingQueue.all_nonempty())
    assert found == {("c1", "claude", 1), ("c1", "qwen", 1),
                     ("c2", "claude", 1)}


def test_singleton_per_key(fake_store):
    a = PendingQueue.for_agent("c1", "claude")
    b = PendingQueue.for_agent("c1", "claude")
    c = PendingQueue.for_agent("c1", "Claude")  # case-insensitive
    d = PendingQueue.for_agent("c1", "qwen")
    assert a is b
    assert a is c
    assert a is not d


def test_legacy_jsonl_is_migrated_on_first_use(fake_store, inbox, tmp_path):
    legacy = _legacy_file(tmp_path, "c1", "claude", [
        dict(_msg("legacy", "m1", 1), _pending_source="http"),
        dict(_msg("legacy inbox", "m2", 2),
             _pending_source="inbox:delegate_message"),
    ])
    q = PendingQueue.for_agent("c1", "claude")

    assert q.peek_count() == 1
    assert not legacy.exists()
    assert legacy.with_suffix(".jsonl.migrated").exists()
    assert inbox.migration_status("c1", "claude")["item_count"] == 2
    assert [item.msg_id for item in inbox.list_pending(
        "c1", "claude", source_prefix="inbox:")] == ["m2"]

    q.enqueue(_msg("new", "m3", 3), source="http")
    assert [m["msg_id"] for m in q.drain()] == ["m1", "m3"]
    assert PendingQueue.migrate_agent_to_inbox(
        "c1", "claude")["migrated"] is False


def test_all_nonempty_migrates_legacy_files(fake_store, inbox, tmp_path):
    legacy = _legacy_file(tmp_path, "c9", "qwen", [
        dict(_msg("a", "m1", 1), _pending_source="http"),
        dict(_msg("b", "m2", 2), _pending_source="http")])

    assert PendingQueue.all_nonempty() == [("c9", "qwen", 2)]
    assert not legacy.exists()
    assert inbox.migration_status("c9", "qwen") is not None
    assert [m["msg_id"] for m in PendingQueue.for_agent(
        "c9", "qwen").drain()] == ["m1", "m2"]


def test_enqueue_returns_false_for_durable_store_rejection_after_restart(fake_store, inbox, monkeypatch):
    q = PendingQueue.for_agent("c1", "claude")
    q.enqueue(_msg("old", "old", 1, ts=10), source="http")
    with patch("core.pending_queue.time.time", return_value=20):
        assert q.clear("force_stop") == 1
    PendingQueue.drop_cache()
    reopened = AgentInboxStore(inbox.database_path)
    monkeypatch.setattr(AgentInboxStore, "_instance", reopened)
    q = PendingQueue.for_agent("c1", "claude")

    assert q.enqueue(_msg("delayed", "delayed", 2, ts=10),
                     source="inbox:delegate_message") is False
    assert q.enqueue(_msg("old retry", "boundary", 3, ts=20), source="http") is False
    assert q.enqueue(_msg("fresh", "fresh", 4, ts=21), source="http") is True
    assert [item.msg_id for item in reopened.list_pending("c1", "claude")] == ["fresh"]
    assert [item.state for item in reopened.list_items("c1", "claude")][:3] == [
        "discarded", "discarded", "discarded"]


def test_legacy_metadata_fence_import_handles_iso_creation_time(fake_store, inbox):
    fake_store.extras[("c1", "last_force_stop_at")] = 20
    q = PendingQueue.for_agent("c1", "claude")
    assert q.enqueue(_msg("old", "old", 1, ts="1970-01-01T00:00:10Z"),
                     source="http") is False
    assert inbox.enqueue("c1", "claude", _msg("direct", "direct", 2, ts=10),
                         "http").state == "discarded"


def test_notice_then_paged_read_preserves_unread_until_completion(fake_store, inbox):
    from core import agent_inbox
    q = PendingQueue.for_agent("c1", "claude")
    text = "x" * 5000
    q.enqueue(_msg(text, "large", 1), source="inbox:delegate_message")
    assert agent_inbox.take_notice("c1", "claude")
    (item,) = inbox.list_pending("c1", "claude")
    assert item.state == "notified" and item.notified_at
    assert PendingQueue.all_nonempty() == [("c1", "claude", 1)]
    assert q.drain() == []
    page = agent_inbox.read_inbox("c1", "claude", max_chars=2000)
    assert len(page) <= 2000
    first_offset = inbox.read_offset("c1", "claude", "large")
    assert 0 < first_offset < 2000
    assert page.splitlines()[1] == text[:first_offset]
    assert inbox.list_pending("c1", "claude")[0].state == "notified"
    content = page.splitlines()[1]
    while inbox.pending_count("c1", "claude"):
        offset = inbox.read_offset("c1", "claude", "large")
        assert offset == len(content)
        page = agent_inbox.read_inbox(
            "c1", "claude", max_chars=2000, item="large", offset=offset)
        assert len(page) <= 2000
        content += page.splitlines()[1]
    assert content == text
    (item,) = inbox.list_items("c1", "claude")
    assert item.state == "read" and item.read_at
    assert inbox.read_offset("c1", "claude", "large") == 0
    assert PendingQueue.all_nonempty() == []


def test_boot_scan_invokes_thirty_day_retention_and_counts_notified(fake_store, inbox):
    now = 31 * 24 * 60 * 60
    for msg_id in ("old-read", "recent-read", "boundary", "notified"):
        inbox.enqueue("c1", "claude", _msg(msg_id, msg_id, 1, ts=1), "inbox:catch_up", now=1)
    inbox.mark_read("c1", "claude", ["old-read"], now=1)
    inbox.mark_read("c1", "claude", ["recent-read"], now=now - 10)
    inbox.mark_read("c1", "claude", ["boundary"], now=24 * 60 * 60)
    inbox.mark_notified("c1", "claude", inbox.latest_sequence("c1", "claude"), now=1)
    inbox.set_read_offset("c1", "claude", "notified", 5)
    inbox.enqueue("c1", "claude", _msg("user", "user", 2, ts=1), "http", now=1)
    inbox.enqueue("c1", "claude", _msg("ack", "ack", 3, ts=1), "http", now=1)
    inbox.claim("c1", "claude", "run", "receive", include_msg_ids=["ack"], now=1)
    inbox.acknowledge("c1", "claude", "run", ["ack"], now=2)
    inbox.enqueue("c1", "claude", _msg("discard", "discard", 4, ts=1), "http", now=1)
    inbox.discard_msg_ids("c1", "claude", ["discard"], now=2)

    with patch("core.agent_inbox_store.time.time", return_value=now):
        assert PendingQueue.all_nonempty() == [("c1", "claude", 2)]
    assert {item.msg_id for item in inbox.list_items("c1", "claude")} == {
        "recent-read", "boundary", "notified", "user"}
    assert inbox.read_offset("c1", "claude", "notified") == 5


@pytest.mark.parametrize("conv,agent", [("", "claude"), ("c1", ""), ("c1", " ")])
def test_queue_requires_explicit_identity(fake_store, conv, agent):
    with pytest.raises(ValueError, match="required"):
        PendingQueue.for_agent(conv, agent)
