"""WorkflowRun Kanban projection and command-planning contracts."""

import uuid

import pytest

from core.workflow_agent_contracts import WORKFLOW_TERMINAL_STATUSES
from core.workflow_kanban import (
    RUN_STATUS_LANES,
    plan_workflow_kanban_command,
    project_task_cards,
    run_lane,
    validate_command_id,
    workflow_kanban_snapshot,
)
from tasks.ai.actions._agentres_k8 import _kanban_require_generation


def _run(status="running", **overrides):
    value = {
        "run_id": "wr-1",
        "conversation_id": "conv-1",
        "agent_name": "Demo",
        "run_generation": 2,
        "root_turn_id": "turn-1",
        "status": status,
        "flow_ref": {"name": "demo-flow", "scope": "global", "version": "1"},
        "usage": {},
        "claimed_ids": [],
        "created_at": 100.0,
        "updated_at": 200.0,
        "terminal_at": None,
        "recovery_count": 0,
        "reason": None,
        "error": None,
        "resume_task_id": "",
        "resume_flowfile_json": None,
        "invocation_mode": "conversation",
        "permission_mode": "read_only",
        "message_committed": False,
        "inbox_acknowledged": False,
        "outbox_enqueued": False,
    }
    value.update(overrides)
    return value


def _graph():
    return {
        "tasks": [
            {"id": "root", "label": "Root", "type": "start"},
            {"id": "left", "label": "Left", "type": "agentLoop"},
            {"id": "right", "label": "Right", "type": "agentLoop"},
            {"id": "join", "label": "Join", "type": "mergeContent"},
        ],
        "relations": [
            {"from": "root", "to": "left", "type": "success"},
            {"from": "root", "to": "right", "type": "success"},
            {"from": "left", "to": "join", "type": "success"},
            {"from": "right", "to": "join", "type": "success"},
        ],
    }


def _event(sequence, event_type, task_id="", **data):
    return {
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": f"2026-08-27T00:00:0{sequence}+00:00",
        "data": {"task_id": task_id, **data},
    }


def _wait(task_id="left"):
    return {
        "wait_id": "wait-1",
        "signal_id": "interaction:req-1",
        "instance_id": "workflow:wr-1",
        "task_id": task_id,
        "created_at": 123.0,
        "status": "waiting",
        "kind": "signal",
    }


def test_every_canonical_run_status_maps_to_one_lane():
    expected = {
        "accepted": "queued",
        "running": "running",
        "waiting": "waiting",
        "retryable_failed": "attention",
        "cancelling": "running",
        "committing": "running",
        "completed": "done",
        "cancelled": "done",
        "superseded": "done",
        "failed": "failed",
        "timed_out": "failed",
        "budget_exceeded": "failed",
        "force_stopped": "done",
        "recovery_failed": "failed",
    }
    assert RUN_STATUS_LANES == expected
    assert {status: run_lane(status) for status in expected} == expected
    assert WORKFLOW_TERMINAL_STATUSES <= set(expected)


def test_task_projection_retains_branches_joins_waits_comments_and_assignment():
    events = [
        _event(1, "progress", "root", stage="task_completed"),
        _event(
            2,
            "kanban_assignment",
            "",
            assignment_id=str(uuid.uuid4()),
            assignee="Ops",
            created_at="2026-08-27T00:00:02+00:00",
        ),
        _event(
            3,
            "kanban_assignment",
            "left",
            assignment_id=str(uuid.uuid4()),
            assignee="Quentin",
            created_at="2026-08-27T00:00:03+00:00",
        ),
        _event(
            4,
            "kanban_comment",
            "left",
            comment_id=str(uuid.uuid4()),
            author_label="Quentin",
            body="<b>check</b> api_key=secret",
            created_at="2026-08-27T00:00:04+00:00",
        ),
    ]

    cards = project_task_cards(_run(), _graph(), events, waits=[_wait()], live=True)
    by_id = {card["task_id"]: card for card in cards}

    assert by_id["root"]["lane"] == "done"
    assert by_id["left"]["lane"] == "waiting"
    assert by_id["right"]["lane"] == "ready"
    assert by_id["join"]["lane"] == "not_started"
    assert by_id["left"]["assignee"] == "Quentin"
    assert by_id["right"]["assignee"] == "Ops"
    assert by_id["left"]["comments_count"] == 1
    assert by_id["left"]["comments"][0]["body"] == ("<b>check</b> api_key=[redacted]")
    assert by_id["root"]["relations"]["children"] == ["left", "right"]
    assert by_id["join"]["relations"]["parents"] == ["left", "right"]
    assert {badge["id"] for badge in by_id["root"]["badges"]} >= {"branch"}
    assert {badge["id"] for badge in by_id["join"]["badges"]} >= {"join"}


def test_task_projection_agrees_with_inspector_graph_derivation():
    graph = {
        "tasks": [
            {"id": "first", "label": "First", "status": "completed"},
            {"id": "second", "label": "Second", "status": "running"},
        ],
        "relations": [{"from": "first", "to": "second", "type": "success"}],
    }
    events = [
        _event(1, "authorization", "first", decision="execute"),
        _event(2, "authorization", "second", decision="execute"),
    ]

    cards = project_task_cards(_run(), graph, events)

    assert {card["task_id"]: card["lane"] for card in cards} == {
        "first": "done",
        "second": "running",
    }


def test_command_planner_uses_runtime_semantics_and_rejects_direct_state_writes():
    retryable = _run(
        "retryable_failed", resume_task_id="left", error={"task_id": "left", "retryable": True}
    )
    retry = plan_workflow_kanban_command(retryable, "", "running", [], live=False, safe_retry=True)
    assert retry.executable is True
    assert retry.command == "retry"
    assert retry.requires_confirmation is True

    terminal = plan_workflow_kanban_command(_run("completed"), "", "running", [], live=False)
    assert terminal.executable is False
    assert terminal.code == "terminal_immutable"

    cancel = plan_workflow_kanban_command(_run("running"), "", "done", [], live=True)
    assert cancel.command == "cancel"
    assert "does not mark" in cancel.message

    forbidden = plan_workflow_kanban_command(_run("running"), "", "failed", [], live=True)
    assert forbidden.code == "direct_status_forbidden"


def test_waiting_plan_returns_exact_existing_interaction():
    plan = plan_workflow_kanban_command(_run("waiting"), "", "running", [], waits=[_wait()])
    assert plan.command == "open_interaction"
    assert plan.interaction == {
        "wait_id": "wait-1",
        "request_id": "req-1",
        "signal_id": "interaction:req-1",
        "task_id": "left",
        "created_at": 123.0,
    }


def test_task_planner_rejects_unmet_parents_and_unknown_tasks():
    blocked = plan_workflow_kanban_command(_run(), "join", "running", [], graph=_graph())
    assert blocked.code == "blocked_by_dependencies"
    assert blocked.blocking_parents == ("left", "right")

    missing = plan_workflow_kanban_command(_run(), "missing", "running", [], graph=_graph())
    assert missing.code == "unknown_task"


def test_command_idempotency_key_must_be_a_uuid():
    key = str(uuid.uuid4())
    assert validate_command_id(key) == key
    with pytest.raises(ValueError, match="must be a UUID"):
        validate_command_id("not-a-uuid")


class _FakeStore:
    def __init__(self):
        self.rows = [
            _run(run_id="wr-3", created_at=300.0, updated_at=300.0),
            _run(run_id="wr-2", created_at=200.0, updated_at=200.0),
            _run(run_id="wr-1", created_at=100.0, updated_at=100.0),
        ]

    def list_runs(self, conversation_id, agent_name="", limit=50, offset=0):
        assert conversation_id == "conv-1"
        return tuple(self.rows[offset : offset + limit])

    def list_events(self, run_id):
        return ()

    def is_current_generation(self, run_id):
        return False


def test_run_snapshot_pages_without_hiding_remaining_runs():
    snapshot = workflow_kanban_snapshot("conv-1", limit=2, store=_FakeStore(), waits=[])

    assert [card["run_id"] for card in snapshot["cards"]] == ["wr-3", "wr-2"]
    assert snapshot["cursor"] == "2"
    assert snapshot["mode"] == "runs"

    next_page = workflow_kanban_snapshot(
        "conv-1", limit=2, cursor=snapshot["cursor"], store=_FakeStore(), waits=[]
    )
    assert [card["run_id"] for card in next_page["cards"]] == ["wr-1"]
    assert next_page["cursor"] is None


class _FakeFileStore:
    def get_metadata_required(self, file_id, user_id, conversation_id):
        assert user_id == "user-1"
        assert conversation_id == "conv-1"
        if file_id != "owned-file":
            raise FileNotFoundError(file_id)
        return {
            "filename": "evidence.txt",
            "content_type": "text/plain",
            "size": 12,
        }


def test_attachment_projection_rechecks_conversation_authorization():
    events = [
        _event(
            1,
            "kanban_attachment_added",
            "left",
            attachment_id=str(uuid.uuid4()),
            file_id="owned-file",
            label="Evidence",
            added_by_user_id="user-1",
        ),
        _event(
            2,
            "kanban_attachment_added",
            "left",
            attachment_id=str(uuid.uuid4()),
            file_id="denied-file",
            label="Must stay hidden",
            added_by_user_id="user-2",
        ),
    ]

    cards = project_task_cards(
        _run(),
        _graph(),
        events,
        file_store=_FakeFileStore(),
        user_id="user-1",
    )
    left = {card["task_id"]: card for card in cards}["left"]

    assert left["attachments_count"] == 1
    assert left["attachments"] == [
        {
            "attachment_id": events[0]["data"]["attachment_id"],
            "file_id": "owned-file",
            "filename": "evidence.txt",
            "content_type": "text/plain",
            "size": 12,
            "label": "Evidence",
            "added_by_user_id": "user-1",
            "created_at": events[0]["timestamp"],
            "url": "/files/owned-file",
        }
    ]


def test_review_reopen_is_visible_without_rewriting_runtime_dependencies():
    events = [
        _event(1, "progress", "left", stage="task_completed"),
        _event(2, "progress", "right", stage="task_completed"),
        _event(
            3,
            "kanban_review",
            "left",
            review_id=str(uuid.uuid4()),
            decision="approved",
            reviewer_user_id="user-1",
        ),
        _event(
            4,
            "kanban_review",
            "left",
            review_id=str(uuid.uuid4()),
            decision="reopened",
            reviewer_user_id="user-1",
            comment="Recheck the output",
        ),
    ]

    by_id = {
        card["task_id"]: card
        for card in project_task_cards(_run(), _graph(), events)
    }

    assert by_id["left"]["lane"] == "done"
    assert by_id["left"]["review"]["decision"] == "reopened"
    assert len(by_id["left"]["review_history"]) == 2
    assert by_id["join"]["lane"] == "ready"
    assert by_id["join"]["summary"]["review_dependency_warnings"] == ["left"]


def test_snapshot_projects_worker_diagnostics_and_stale_generation_are_derived():
    snapshot = workflow_kanban_snapshot(
        "conv-1",
        limit=1,
        store=_FakeStore(),
        waits=[],
        live_run_ids={"wr-3"},
        workers=[
            {
                "agent_name": "Demo",
                "turn_id": "turn-1",
                "workflow_run_id": "wr-3",
                "status": "tool call",
                "duration_s": 4.5,
                "runtime_kind": "workflow",
                "message_preview": "must not be projected",
            }
        ],
    )

    assert snapshot["version"] == 2
    assert snapshot["projects"] == [
        {"id": "global:demo-flow", "label": "demo-flow", "scope": "global"}
    ]
    diagnostics = snapshot["cards"][0]["summary"]["diagnostics"]
    assert diagnostics["stale_generation"] is True
    assert diagnostics["worker"]["status"] == "tool call"
    assert "message_preview" not in diagnostics["worker"]
    assert diagnostics["worker"]["termination_command"] == "force_stop"


def test_mutations_require_the_snapshot_generation():
    assert _kanban_require_generation({"expected_generation": 2}, _run()) == 2
    with pytest.raises(ValueError, match="expected_generation is required"):
        _kanban_require_generation({}, _run())
    with pytest.raises(RuntimeError, match="generation changed"):
        _kanban_require_generation({"expected_generation": 1}, _run())
