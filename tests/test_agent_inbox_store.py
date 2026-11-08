import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import FlowFile
from core.agent_contracts import AuthorizationRefContract
from core.agent_inbox_store import AgentInboxStore
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import WorkflowLimits, WorkflowRunContext
from tasks.ai.workflow.turn_tasks import ReceiveAgentMessagesTask


def _message(msg_id, content="hello", attachments=None,
             ts="2026-08-24T00:00:00+00:00"):
    row = {
        "role": "user",
        "content": content,
        "msg_id": msg_id,
        "ts": ts,
    }
    if attachments is not None:
        row["attachments"] = attachments
    return row


@pytest.fixture
def inbox(tmp_path):
    return AgentInboxStore(tmp_path / "inbox.sqlite3")


def test_duplicate_message_is_one_stable_work_item(inbox):
    first = inbox.enqueue("c1", "Claude", _message("m1"), "web", now=10)
    second = inbox.enqueue("c1", "claude", _message("m1"), "web", now=11)

    assert first.sequence == second.sequence
    assert inbox.pending_count("c1", "CLAUDE") == 1


def test_concurrent_duplicate_ingress_partial_ack_and_reclaim_loses_nothing(inbox):
    message_ids = [f"m{number:03d}" for number in range(200)]
    deliveries = message_ids + list(reversed(message_ids))

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(
            lambda msg_id: inbox.enqueue(
                "c1", "agent", _message(msg_id), "stress"),
            deliveries,
        ))

    assert inbox.pending_count("c1", "agent") == len(message_ids)
    first_claim, first_items = inbox.claim(
        "c1", "agent", "run-1", "receive", max_messages=75)
    assert first_claim is not None
    answered = [item.msg_id for item in first_items[:50]]
    assert inbox.acknowledge(
        "c1", "agent", "run-1", answered) == len(answered)
    assert inbox.release("c1", "agent", "run-1") == 25

    second_claim, second_items = inbox.claim(
        "c1", "agent", "run-2", "receive", max_messages=500)
    assert second_claim is not None
    remaining = [item.msg_id for item in second_items]
    assert len(remaining) == 150
    assert inbox.acknowledge(
        "c1", "agent", "run-2", remaining) == len(remaining)

    final = inbox.list_items("c1", "agent")
    assert {item.msg_id for item in final} == set(message_ids)
    assert len(final) == len(message_ids)
    assert {item.state for item in final} == {"acknowledged"}


def test_claim_preserves_order_payload_and_is_idempotent(inbox):
    attachment = {"name": "paw.png", "url": "fs://paw.png"}
    inbox.enqueue("c1", "agent", _message("m1", "first", [attachment]), now=1)
    inbox.enqueue("c1", "agent", _message("m2", "second"), now=2)

    claim, items = inbox.claim("c1", "agent", "run-1", "receive", now=3)
    retry, retried = inbox.claim("c1", "agent", "run-1", "receive", now=4)

    assert claim is not None and retry is not None
    assert retry.claim_id == claim.claim_id
    assert [item.msg_id for item in items] == ["m1", "m2"]
    assert [item.msg_id for item in retried] == ["m1", "m2"]
    assert items[0].payload["attachments"] == [attachment]


def test_zero_max_messages_claims_every_pending_message(inbox):
    for number in range(25):
        inbox.enqueue("c1", "agent", _message(f"m{number:02d}"), now=number)

    claim, items = inbox.claim(
        "c1", "agent", "run-unbounded", "receive", max_messages=0, now=30)

    assert claim is not None
    assert [item.msg_id for item in items] == [f"m{number:02d}" for number in range(25)]


def test_queue_visibility_cutoff_excludes_later_arrivals(inbox):
    inbox.enqueue("c1", "agent", _message("m1"), now=1)
    cutoff = inbox.latest_sequence("c1", "agent")
    inbox.enqueue("c1", "agent", _message("m2"), now=2)

    _claim, items = inbox.claim(
        "c1", "agent", "run-1", "receive",
        max_sequence=cutoff, now=3)

    assert [item.msg_id for item in items] == ["m1"]
    assert inbox.pending_count("c1", "agent") == 1


def test_crash_after_claim_releases_expired_lease(inbox):
    inbox.enqueue("c1", "agent", _message("m1"), now=1)
    inbox.claim(
        "c1", "agent", "dead-run", "receive", lease_seconds=5, now=2)

    assert inbox.recover_expired_leases(now=8) == 1
    claim, items = inbox.claim("c1", "agent", "new-run", "receive", now=9)
    assert claim is not None
    assert [item.msg_id for item in items] == ["m1"]


def test_boot_releases_only_orphaned_workflow_claims(inbox):
    for number in range(1, 4):
        inbox.enqueue("c1", "agent", _message(f"m{number}"), now=number)
    inbox.claim(
        "c1", "agent", "wr_live", "receive", max_messages=1,
        lease_seconds=300, now=4)
    inbox.claim(
        "c1", "agent", "wr_missing", "receive", max_messages=1,
        lease_seconds=300, now=5)
    inbox.claim(
        "c1", "agent", "pending-drain:live", "legacy_drain",
        max_messages=1, lease_seconds=300, now=6)

    assert inbox.recover_orphaned_workflow_claims(
        ["wr_live"], now=7) == 1
    states = {item.msg_id: (item.state, item.owner_run_id)
              for item in inbox.list_items("c1", "agent")}

    assert states == {
        "m1": ("claimed", "wr_live"),
        "m2": ("pending", None),
        "m3": ("claimed", "pending-drain:live"),
    }


def test_receipt_repairs_crash_after_transcript_persistence(inbox):
    row = _message("m1")
    inbox.prepare_receipt("c1", "agent", row, "web", now=1)

    result = inbox.reconcile_receipts(
        lambda conversation_id, msg_id: (conversation_id, msg_id) == ("c1", "m1"))

    assert result == {"repaired": 1, "awaiting_transcript": 0}
    assert [item.msg_id for item in inbox.list_items("c1", "agent")] == ["m1"]
    assert inbox.reconcile_receipts(lambda *_: True)["repaired"] == 0


def test_receipt_repairs_crash_before_transcript_persistence(inbox):
    row = _message("m-before")
    inbox.prepare_receipt("c1", "agent", row, "web", now=1)
    appended = []

    result = inbox.reconcile_receipts(
        lambda *_: False,
        lambda conversation_id, agent_name, payload: appended.append(
            (conversation_id, agent_name, payload["msg_id"])))

    assert result == {"repaired": 1, "awaiting_transcript": 0}
    assert appended == [("c1", "agent", "m-before")]
    assert inbox.pending_count("c1", "agent") == 1


def test_final_acknowledges_only_messages_seen_by_run(inbox):
    for number in range(1, 4):
        inbox.enqueue("c1", "agent", _message(f"m{number}"), now=number)
    _, seen = inbox.claim(
        "c1", "agent", "run-1", "receive", max_messages=2, now=4)

    assert inbox.acknowledge(
        "c1", "agent", "run-1", [seen[0].msg_id]) == 1
    states = {item.msg_id: item.state
              for item in inbox.list_items("c1", "agent")}
    assert states == {"m1": "acknowledged", "m2": "claimed", "m3": "pending"}


def test_release_transfer_and_force_stop_cutoff(inbox):
    inbox.enqueue("c1", "agent", _message("old", ts=10), now=10)
    inbox.claim("c1", "agent", "old-run", "receive", now=11)
    assert inbox.transfer(
        "c1", "agent", "old-run", "new-run", now=12) == 1
    assert inbox.release("c1", "agent", "new-run", now=13) == 1
    inbox.enqueue("c1", "agent", _message("new", ts=20), now=20)

    assert inbox.discard_through("c1", "agent", cutoff=15, now=21) == 1
    states = {item.msg_id: item.state
              for item in inbox.list_items("c1", "agent")}
    assert states == {"old": "discarded", "new": "pending"}


def test_restart_transfer_does_not_shadow_new_root_claim(inbox):
    inbox.enqueue("c1", "agent", _message("old"), now=1)
    inbox.claim(
        "c1", "agent", "old-run", "__workflow_input__", now=2)
    inbox.enqueue("c1", "agent", _message("new"), now=3)

    assert inbox.transfer(
        "c1", "agent", "old-run", "new-run", now=4) == 1
    claim, items = inbox.claim(
        "c1", "agent", "new-run", "__workflow_input__",
        include_msg_ids=["new"], max_messages=1, now=5)

    assert claim is not None
    assert [item.msg_id for item in items] == ["new"]


def test_legacy_jsonl_migration_is_validated_and_one_shot(inbox, tmp_path):
    source = tmp_path / "pending.jsonl"
    rows = [dict(_message("m1"), _pending_source="http"),
            dict(_message("m2"), _pending_source="http")]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    first = inbox.migrate_pending_jsonl("c1", "agent", source)
    second = inbox.migrate_pending_jsonl("c1", "agent", source)

    assert first["migrated"] is True
    assert second["migrated"] is False
    assert inbox.pending_count("c1", "agent") == 2


def test_enqueue_idempotency_ignores_pending_bookkeeping(inbox):
    first = inbox.enqueue("c1", "agent", dict(
        _message("m1"), _pending_source="http", _pending_enqueued_at=1.0))
    again = inbox.enqueue("c1", "agent", dict(
        _message("m1"), _pending_source="rescue", _pending_enqueued_at=2.0))

    assert again.sequence == first.sequence
    assert again.payload["_pending_enqueued_at"] == 1.0
    with pytest.raises(ValueError, match="different inbox payload"):
        inbox.enqueue("c1", "agent", dict(
            _message("m1", "changed"), _pending_enqueued_at=3.0))


def test_list_pending_source_prefix_filters(inbox):
    inbox.enqueue("c1", "agent", _message("m1"), "http", now=1)
    inbox.enqueue("c1", "agent", _message("m2"), "inbox:delegate_message",
                  now=2)
    inbox.enqueue("c1", "agent", _message("m3"), "inbox:catch_up", now=3)
    inbox.enqueue("c1", "agent", _message("m4"), "inbox_like", now=4)
    inbox.enqueue("c1", "other", _message("m5"), "inbox:catch_up", now=5)
    inbox.mark_notified("c1", "agent", 2, now=6)

    def ids(**kwargs):
        return [item.msg_id for item in inbox.list_pending(
            "c1", "AGENT", **kwargs)]

    assert ids() == ["m1", "m2", "m3", "m4"]
    assert ids(source_prefix="inbox:") == ["m2", "m3"]
    assert ids(exclude_source_prefix="inbox:") == ["m1", "m4"]
    assert ids(limit=1) == ["m1"]


def test_notice_state_upsert_changes_only_given_fields(inbox):
    assert inbox.notice_state("c1", "agent") == (0, 0)
    inbox.set_notice_state("c1", "Agent", notified_sequence=7)
    assert inbox.notice_state("c1", "agent") == (7, 0)
    inbox.set_notice_state("c1", "agent", unread_turns=2)
    assert inbox.notice_state("c1", "agent") == (7, 2)
    inbox.set_notice_state("c1", "agent", notified_sequence=9, unread_turns=0)
    assert inbox.notice_state("c1", "agent") == (9, 0)
    with pytest.raises(ValueError):
        inbox.set_notice_state("c1", "agent")


def test_read_offsets_and_mark_read(inbox):
    inbox.enqueue("c1", "agent", _message("m1"), "inbox:catch_up")
    inbox.enqueue("c1", "agent", _message("m2"), "inbox:catch_up")
    assert inbox.read_offset("c1", "agent", "m1") == 0
    inbox.set_read_offset("c1", "agent", "m1", 100)
    inbox.set_read_offset("c1", "agent", "m1", 250)
    assert inbox.read_offset("c1", "AGENT", "m1") == 250

    assert inbox.mark_read("c1", "agent", ["m1", "missing"]) == 1
    assert inbox.read_offset("c1", "agent", "m1") == 0
    states = {item.msg_id: item.state
              for item in inbox.list_items("c1", "agent")}
    assert states == {"m1": "read", "m2": "pending"}
    assert inbox.mark_read("c1", "agent", ["m1"]) == 0


def test_purge_acknowledged_keeps_recent_and_pending_rows(inbox):
    inbox.enqueue("c1", "agent", _message("old-read"), now=1)
    inbox.enqueue("c1", "agent", _message("old-discarded"), now=1)
    inbox.enqueue("c1", "agent", _message("pending"), now=1)
    inbox.enqueue("c1", "agent", _message("new-read"), now=1)
    inbox.claim("c1", "agent", "run-1", "receive",
                include_msg_ids=["old-read"], now=2)
    inbox.acknowledge("c1", "agent", "run-1", ["old-read"], now=10)
    inbox.discard_msg_ids("c1", "agent", ["old-discarded"], now=10)
    inbox.claim("c1", "agent", "run-2", "receive",
                include_msg_ids=["new-read"], now=2)
    inbox.acknowledge("c1", "agent", "run-2", ["new-read"], now=90)
    inbox.set_read_offset("c1", "agent", "pending", 5)
    inbox.set_read_offset("c1", "agent", "old-discarded", 5)

    assert inbox.purge_acknowledged(50, now=100) == 2
    assert {item.msg_id for item in inbox.list_items("c1", "agent")} == {
        "pending", "new-read"}
    assert inbox.read_offset("c1", "agent", "pending") == 5
    assert inbox.read_offset("c1", "agent", "old-discarded") == 0


def test_delete_conversation_clears_notice_and_offset_tables(inbox):
    inbox.enqueue("c1", "agent", _message("m1"))
    inbox.set_notice_state("c1", "agent", notified_sequence=1)
    inbox.set_read_offset("c1", "agent", "m1", 3)

    assert inbox.delete_conversation("c1") == 3
    assert inbox.notice_state("c1", "agent") == (0, 0)
    assert inbox.read_offset("c1", "agent", "m1") == 0


def test_notified_and_read_states_have_durable_first_delivery_audits(inbox):
    first = inbox.enqueue("c1", "agent", _message("m1"), "inbox:catch_up", now=1)
    second = inbox.enqueue("c1", "agent", _message("m2"), "inbox:catch_up", now=2)
    inbox.enqueue("c1", "agent", _message("user"), "http", now=3)
    inbox.enqueue("c1", "other", _message("other"), "inbox:catch_up", now=4)

    assert first.notified_at is None and first.read_at is None
    assert first.read_call_id == ""
    assert inbox.mark_notified("c1", "AGENT", first.sequence, now=5) == 1
    assert inbox.mark_notified("c1", "agent", first.sequence, now=6) == 0
    items = inbox.list_pending("c1", "agent")
    assert [(item.msg_id, item.state) for item in items] == [
        ("m1", "notified"), ("m2", "pending"), ("user", "pending")]
    assert items[0].notified_at == "1970-01-01T00:00:05+00:00"
    assert inbox.pending_count("c1", "agent") == 3
    assert inbox.list_ready_keys() == (("c1", "agent"), ("c1", "other"))
    assert inbox.list_agent_keys("c1") == ("agent", "other")

    assert inbox.mark_read("c1", "agent", ["m1", "m2", "user"], "read-call", now=7) == 2
    assert inbox.mark_read("c1", "agent", ["m1"], "retry-call", now=8) == 0
    assert inbox.mark_notified("c1", "agent", second.sequence, now=9) == 0
    reopened = AgentInboxStore(inbox.database_path)
    items = reopened.list_items("c1", "agent")
    assert [item.state for item in items] == ["read", "read", "pending"]
    assert items[0].notified_at == "1970-01-01T00:00:05+00:00"
    assert items[1].notified_at is None
    assert items[0].read_at == items[1].read_at == "1970-01-01T00:00:07+00:00"
    assert items[0].to_dict()["read_call_id"] == "read-call"
    assert reopened.pending_count("c1", "agent") == 1


def test_claim_never_consumes_inbox_even_with_explicit_sources_and_ids(inbox):
    inbox.enqueue("c1", "agent", _message("pending"), "inbox:catch_up")
    notified = inbox.enqueue("c1", "agent", _message("notified"), "inbox:catch_up")
    inbox.mark_notified("c1", "agent", notified.sequence)
    inbox.enqueue("c1", "agent", _message("later"), "inbox:catch_up")
    inbox.enqueue("c1", "agent", _message("user"), "http")

    assert inbox.claim("c1", "agent", "run-inbox", "receive",
                       sources=["inbox:catch_up"],
                       include_msg_ids=["pending", "notified", "later"]) == (None, ())
    claim, items = inbox.claim("c1", "agent", "run-user", "receive")
    assert claim is not None
    assert [item.msg_id for item in items] == ["user"]
    from core.workflow_agent_contracts import AgentInboxItem
    assert AgentInboxItem.from_dict(items[0].to_dict()).state == "claimed"
    assert inbox.pending_count("c1", "agent") == 3


def test_force_stop_fence_survives_restart_and_a_delayed_direct_producer(inbox):
    ready = threading.Event()
    resume = threading.Event()
    producer_store = AgentInboxStore(inbox.database_path)

    def delayed_producer():
        payload = _message("delayed", ts="1970-01-01T00:00:10Z")
        ready.set()
        assert resume.wait(5)
        return producer_store.enqueue("c1", "AGENT", payload, "inbox:catch_up", now=30)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delayed_producer)
        try:
            assert ready.wait(5)
            assert inbox.discard_through("c1", "agent", 20, now=20) == 0
        finally:
            resume.set()
        rejected = future.result(timeout=5)
    assert rejected.state == "discarded"
    reopened = AgentInboxStore(inbox.database_path)
    assert reopened.discard_through("c1", "agent", 5, now=31) == 0
    assert reopened.enqueue("c1", "agent", _message("boundary", ts=20), now=32).state == "discarded"
    assert reopened.enqueue("c1", "agent", _message("fresh", ts=21), now=32).state == "pending"
    assert reopened.enqueue("c1", "other", _message("other", ts=1), now=32).state == "pending"
    assert reopened.enqueue("other", "agent", _message("other-conv", ts=1), now=32).state == "pending"
    assert reopened.mark_read("c1", "agent", ["delayed"]) == 0
    assert reopened.pending_count("c1", "agent") == 1


@pytest.mark.parametrize("transferred", [False, True])
def test_fence_uses_creation_time_and_preserves_newer_workflow_leases(inbox, transferred):
    old = inbox.enqueue("c1", "agent", _message("old", ts=10), "inbox:catch_up", now=30)
    inbox.mark_notified("c1", "agent", old.sequence, now=31)
    inbox.enqueue("c1", "agent", _message("new", ts=21), "http", now=32)
    claim, _ = inbox.claim("c1", "agent", "run-new", "receive", now=33)
    run_id, task_id = "run-new", "receive"
    if transferred:
        assert inbox.transfer("c1", "agent", "run-new", "replacement", now=33) == 1
        run_id, task_id = "replacement", "restart:run-new:receive"

    assert inbox.discard_through("c1", "agent", 20, now=34) == 1
    retry, items = inbox.claim("c1", "agent", run_id, task_id, now=35)
    assert retry.claim_id == claim.claim_id
    assert [item.msg_id for item in items] == ["new"]
    assert inbox.list_items("c1", "agent")[0].state == "discarded"


def test_receipt_repair_respects_fence_and_cannot_resurrect_old_work(inbox):
    inbox.prepare_receipt("c1", "agent", _message("old", ts=10), "http", now=10)
    inbox.discard_through("c1", "agent", 20, now=20)
    assert inbox.reconcile_receipts(lambda *_: True)["repaired"] == 1
    assert inbox.list_items("c1", "agent")[0].state == "discarded"
    assert inbox.pending_count("c1", "agent") == 0


@pytest.mark.parametrize("ts", [None, "", "invalid", "2026-11-07", True, float("nan"), float("inf")])
def test_enqueue_requires_a_valid_creation_timestamp(inbox, ts):
    with pytest.raises(ValueError, match="creation timestamp"):
        inbox.enqueue("c1", "agent", _message("bad", ts=ts), "inbox:catch_up")
    assert inbox.list_items("c1", "agent") == ()


def test_workflow_payload_without_timestamp_keeps_existing_lease_api(inbox):
    payload = {"msg_id": "workflow-root", "content": "user request"}
    item = inbox.enqueue("c1", "agent", payload, "user", now=10)
    assert item.payload == payload
    claim, items = inbox.claim("c1", "agent", "wr_live", "receive", now=11)
    assert claim is not None and items[0].msg_id == "workflow-root"
    assert inbox.acknowledge("c1", "agent", "wr_live", ["workflow-root"], now=12) == 1


_LEGACY_ITEMS_SCHEMA = """
    CREATE TABLE agent_inbox_items (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL, agent_key TEXT NOT NULL,
        msg_id TEXT NOT NULL, payload_json TEXT NOT NULL, source TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending','claimed','acknowledged','discarded')),
        owner_run_id TEXT, owner_task_id TEXT, lease_expires_at REAL,
        enqueued_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(conversation_id, agent_key, msg_id)
    );
"""


def _write_legacy_database(path, rows):
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_ITEMS_SCHEMA)
        for sequence, state, source, payload in rows:
            connection.execute(
                """INSERT INTO agent_inbox_items VALUES (
                       ?, 'c1', 'agent', ?, ?, ?, ?, ?, ?, ?, 1, 2)""",
                (sequence, payload["msg_id"], json.dumps(payload), source, state,
                 "wr_live" if state == "claimed" else None,
                 "receive" if state == "claimed" else None,
                 200 if state == "claimed" else None))


def test_sqlite_upgrade_preserves_workflow_states_order_and_sequence_watermark(tmp_path):
    path = tmp_path / "old.sqlite3"
    unstamped_workflow = {"msg_id": "claimed", "content": "workflow request"}
    rows = [
        (3, "pending", "http", _message("pending")),
        (5, "claimed", "user", unstamped_workflow),
        (7, "acknowledged", "http", _message("acknowledged")),
        (9, "acknowledged", "inbox:catch_up", _message("read")),
        (11, "claimed", "inbox:catch_up", _message("recovered")),
        (13, "discarded", "http", _message("discarded")),
        (100, "discarded", "http", _message("purged")),
    ]
    _write_legacy_database(path, rows)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM agent_inbox_items WHERE sequence=100")

    store = AgentInboxStore(path)
    items = store.list_items("c1", "agent")
    assert [item.sequence for item in items] == [3, 5, 7, 9, 11, 13]
    assert [item.state for item in items] == [
        "pending", "claimed", "acknowledged", "read", "discarded", "discarded"]
    assert items[1].owner_run_id == "wr_live"
    assert items[3].read_at == "1970-01-01T00:00:02+00:00"
    assert items[4].owner_run_id is None
    assert store.enqueue("c1", "agent", _message("new"), "inbox:background_result").sequence > 100
    assert store.acknowledge("c1", "agent", "wr_live", ["claimed"], now=3) == 1
    with sqlite3.connect(path) as connection:
        before = connection.execute("PRAGMA schema_version").fetchone()[0]
    reopened = AgentInboxStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA schema_version").fetchone()[0] == before
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert reopened.mark_notified("c1", "agent", reopened.latest_sequence("c1", "agent")) == 1


def test_sqlite_upgrade_preserves_notice_audit_and_partial_read_progress(tmp_path):
    path = tmp_path / "notified-old.sqlite3"
    _write_legacy_database(path, [
        (1, "pending", "inbox:background_result", _message("notified")),
        (2, "pending", "inbox:background_result", _message("later")),
    ])
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE agent_inbox_notices (
                conversation_id TEXT NOT NULL, agent_key TEXT NOT NULL,
                notified_sequence INTEGER NOT NULL DEFAULT 0,
                unread_turns INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                PRIMARY KEY(conversation_id, agent_key));
            INSERT INTO agent_inbox_notices VALUES ('c1', 'agent', 1, 2, 5);
            CREATE TABLE agent_inbox_read_offsets (
                conversation_id TEXT NOT NULL, agent_key TEXT NOT NULL,
                msg_id TEXT NOT NULL, read_offset INTEGER NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(conversation_id, agent_key, msg_id));
            INSERT INTO agent_inbox_read_offsets VALUES ('c1', 'agent', 'notified', 3, 6);
        """)
    store = AgentInboxStore(path)
    first, second = store.list_pending("c1", "agent")
    assert first.state == "notified" and first.notified_at == "1970-01-01T00:00:05+00:00"
    assert second.state == "pending" and second.notified_at is None
    assert store.notice_state("c1", "agent") == (1, 2)
    assert store.read_offset("c1", "agent", "notified") == 3


def test_sqlite_upgrade_preserves_watermark_when_all_rows_were_purged(tmp_path):
    path = tmp_path / "empty-old.sqlite3"
    _write_legacy_database(path, [(100, "discarded", "http", _message("purged"))])
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM agent_inbox_items")
    store = AgentInboxStore(path)
    assert store.enqueue("c1", "agent", _message("new")).sequence == 101


def test_sqlite_upgrade_rolls_back_without_replacing_invalid_evidence(tmp_path):
    path = tmp_path / "invalid-old.sqlite3"
    _write_legacy_database(path, [
        (1, "pending", "http", _message("good")),
        (2, "pending", "inbox:catch_up", _message("bad", ts=None)),
    ])
    with pytest.raises(ValueError, match="creation timestamp"):
        AgentInboxStore(path)
    with sqlite3.connect(path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_inbox_items'").fetchone()[0]
        assert "'notified'" not in schema
        assert connection.execute("SELECT count(*) FROM agent_inbox_items").fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='agent_inbox_items_upgrade'").fetchone()[0] == 0


@pytest.mark.parametrize("tag,origin,content,expected", [
    *[(tag, {}, "text", tag) for tag in (
        "http", "agent_msg", "resume_agent", "preempt_soft", "skill_run")],
    ("preempt_rescue", {"type": "user"}, "text", "preempt_rescue"),
    ("preempt_rescue", {"type": "agent_delegate", "kind": "request"},
     "text", "inbox:delegate_message"),
    ("preempt_rescue", {"type": "agent_delegate", "kind": "reply"},
     "text", "inbox:delegate_reply"),
    ("preempt_rescue", {"type": "user", "delegate": True},
     "text", "inbox:delegate_result"),
    ("preempt_rescue", {"type": "system", "name": "background"},
     "text", "inbox:background_result"),
    ("preempt_rescue", {"type": "scheduled_wakeup"},
     "[System: Continuation]", "inbox:continuation"),
    ("delegate_reply", {"kind": "request"}, "text", "inbox:delegate_message"),
    ("delegate_reply", {"kind": "reply"}, "text", "inbox:delegate_reply"),
    ("delegate_reply", {}, "text", "inbox:delegate_result"),
    ("bg_tool", {}, "text", "inbox:background_result"),
    ("scheduled_wakeup", {}, "text", "inbox:scheduled_wakeup"),
    ("scheduled_wakeup", {}, "[System: Continuation]", "inbox:continuation"),
    ("skill_assign", {}, "text", "inbox:skill_event"),
    ("skill_unassign", {}, "text", "inbox:skill_event"),
    ("skill_delete", {}, "text", "inbox:skill_event"),
    ("workflow_proposal", {}, "text", "inbox:workflow_proposal"),
    ("catch_up", {}, "text", "inbox:catch_up"),
    ("inbox:delegate_result", {}, "text", "inbox:delegate_result"),
])
def test_jsonl_migration_classifies_every_legacy_delivery(inbox, tmp_path, tag, origin, content, expected):
    path = tmp_path / "pending.jsonl"
    payload = dict(_message("legacy", content), _pending_source=tag, source=origin)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert inbox.migrate_pending_jsonl("c1", "agent", path)["migrated"]
    (item,) = inbox.list_items("c1", "agent")
    assert item.source == expected
    assert item.payload == payload
    if expected.startswith("inbox:"):
        assert inbox.claim("c1", "agent", "run", "receive") == (None, ())


@pytest.mark.parametrize("tag", [None, "", "unknown"])
def test_jsonl_migration_rejects_unclassified_rows_atomically(inbox, tmp_path, tag):
    path = tmp_path / "pending.jsonl"
    rows = [dict(_message("good"), _pending_source="http"),
            dict(_message("bad"), _pending_source=tag)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        inbox.migrate_pending_jsonl("c1", "agent", path)
    assert inbox.list_items("c1", "agent") == ()
    assert inbox.migration_status("c1", "agent") is None


def test_concurrent_jsonl_migration_is_one_shot(inbox, tmp_path):
    path = tmp_path / "pending.jsonl"
    path.write_text(json.dumps(dict(_message("m1"), _pending_source="http")) + "\n",
                    encoding="utf-8")
    other = AgentInboxStore(inbox.database_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.migrate_pending_jsonl("c1", "agent", path),
                                [inbox, other]))
    assert sorted(result["migrated"] for result in results) == [False, True]
    assert inbox.pending_count("c1", "agent") == 1
    path.write_text(json.dumps(dict(_message("m2"), _pending_source="http")) + "\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="changed after migration"):
        inbox.migrate_pending_jsonl("c1", "agent", path)


def test_receive_agent_messages_claims_and_routes(inbox):
    inbox.enqueue("c1", "agent", _message("m1", attachments=[
        {"name": "paw.png"}]))
    auth = AuthorizationRefContract(
        context_id="f03f8117-6610-49f2-9438-bd22c95047ee",
        revision=1, root_turn_id="m0")
    identity = AgentTurnIdentity(
        conversation_id="c1", root_conversation_id="c1",
        agent_instance="agent", turn_id="m0", ingress_msg_id="m0",
        turn_epoch=1, run_generation=1,
        authorization_context_id=auth.context_id,
        authorization_revision_at_start=1, source_kind="user",
        created_at="2026-08-24T00:00:00+00:00")
    context = WorkflowRunContext(
        run_id="run-1", turn_identity=identity, conversation_id="c1",
        agent_name="agent", user_id="u1", root_turn_id="m0",
        run_generation=1,
        flow_ref=ResourceRef(
            resource_type="flow", name="demo:1.0.0", scope="global",
            version="1.0.0", content_digest="a" * 64, source_id="test"),
        channel="web", invocation_mode="conversation",
        permission_mode="default", authorization_ref=auth,
        deadline_at="2026-08-24T00:01:00+00:00",
        limits=WorkflowLimits(
            max_duration_seconds=60, max_llm_calls=1,
            max_flowfiles=10, max_fanout=2),
        service_snapshot={}, cancel_token="cancel", event_sink="test")
    task = ReceiveAgentMessagesTask({"max_messages": 0})
    task.set_workflow_run_context(context, inbox_store=inbox)

    output = task.execute(FlowFile(content=b"original"))[0]
    body = json.loads(output.get_content())

    assert output.get_attribute("route.relationship") == "messages"
    assert body["messages"][0]["msg_id"] == "m1"
    assert body["messages"][0]["payload"]["attachments"] == [
        {"name": "paw.png"}]
