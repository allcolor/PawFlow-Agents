import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import FlowFile
from core.agent_contracts import AuthorizationRefContract
from core.agent_inbox_store import AgentInboxStore
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import WorkflowLimits, WorkflowRunContext
from tasks.ai.workflow.turn_tasks import ReceiveAgentMessagesTask


def _message(msg_id, content="hello", attachments=None):
    row = {
        "role": "user",
        "content": content,
        "msg_id": msg_id,
        "ts": "2026-08-24T00:00:00+00:00",
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
    inbox.enqueue("c1", "agent", _message("old"), now=10)
    inbox.claim("c1", "agent", "old-run", "receive", now=11)
    assert inbox.transfer(
        "c1", "agent", "old-run", "new-run", now=12) == 1
    assert inbox.release("c1", "agent", "new-run", now=13) == 1
    inbox.enqueue("c1", "agent", _message("new"), now=20)

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
    rows = [_message("m1"), _message("m2")]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    first = inbox.migrate_pending_jsonl("c1", "agent", source)
    second = inbox.migrate_pending_jsonl("c1", "agent", source)

    assert first["migrated"] is True
    assert second["migrated"] is False
    assert inbox.pending_count("c1", "agent") == 2


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
    task = ReceiveAgentMessagesTask({"max_messages": 20})
    task.set_workflow_run_context(context, inbox_store=inbox)

    output = task.execute(FlowFile(content=b"original"))[0]
    body = json.loads(output.get_content())

    assert output.get_attribute("route.relationship") == "messages"
    assert body["messages"][0]["msg_id"] == "m1"
    assert body["messages"][0]["payload"]["attachments"] == [
        {"name": "paw.png"}]
