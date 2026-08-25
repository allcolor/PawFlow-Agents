"""WP9 active legacy verification checkpoint transfer."""

import time

import pytest

from core.confirmation_store import ConfirmationStore
from core.flow_run_store import FlowRunStore
from core.plan_migration_active import LegacyActiveCheckpointImporter
from core.workflow_proposal_store import WorkflowProposalStore


def _flow_ref():
    return {
        "schema_version": 1,
        "resource_type": "flow",
        "name": "legacy_plans.plan_cccccccccccccccc:1.0.0",
        "scope": "conversation",
        "owner_id": "user-a",
        "package_id": None,
        "package_version": None,
        "version": "1.0.0",
        "content_digest": "d" * 64,
        "source_id": (
            "repository:conversation:"
            "legacy_plans.plan_cccccccccccccccc:1.0.0"),
    }


def _agent_ref(name, digest):
    return {
        "schema_version": 1,
        "resource_type": "agent",
        "name": name,
        "scope": "conversation",
        "owner_id": "conv-1",
        "package_id": None,
        "package_version": None,
        "version": None,
        "content_digest": digest * 64,
        "source_id": f"repository:conversation:{name}",
    }


def _conversion(deadline):
    digest = "c" * 64
    return {
        "schema_version": 1,
        "mode": "resume",
        "source_digest": digest,
        "flow": {
            "fqn": "legacy_plans.plan_cccccccccccccccc:1.0.0",
            "scope": "conversation",
            "owner_id": "conv-1",
            "replayable": True,
        },
        "proposal": {
            "proposal_id": f"wp_legacy_{digest[:20]}",
            "status": "running",
        },
        "run": {
            "run_id": f"fr_legacy_{digest[:20]}",
            "status": "waiting",
            "checkpoint": {
                "schema_version": 1,
                "kind": "legacy_plan_verification",
                "plan_id": "p_1234",
                "step": 1,
                "executor": "builder",
                "verifier": "reviewer",
                "schedule": {
                    "conversation_id": "conv-1",
                    "key": "conv-1::plan::p_1234::verify1::reviewer",
                    "recheck_at": deadline,
                    "user_id": "user-a",
                    "reason": "[plan_verify:p_1234:1:builder] (reviewer)",
                    "created_at": deadline - 10,
                },
            },
        },
        "steps": [{
            "index": 1,
            "description": "Build",
            "status": "pending_verification",
            "paused": False,
            "note": "ready",
            "legacy_task_id": "",
            "executor": "builder",
            "executor_adapter": {
                "adapter": "invokeWorkflowAgent",
                "agent_ref": _agent_ref("builder", "a"),
            },
            "verifier": "reviewer",
            "verifier_adapter": {
                "adapter": "invokeWorkflowAgent",
                "agent_ref": _agent_ref("reviewer", "b"),
            },
        }],
        "imported_plan": {
            "source_id": "p_1234",
            "source_path": "user-a/conv-1/p_1234.json",
            "user_id": "user-a",
            "conversation_id": "conv-1",
            "title": "Imported active plan",
            "created_by": "planner",
            "created_at": deadline - 20,
            "updated_at": deadline - 10,
            "classification": "waiting_verification",
        },
    }


class Scheduler:
    def __init__(self, key, *, fail=False):
        self.keys = {key}
        self.fail = fail
        self.cancelled = []

    def cancel(self, key):
        if self.fail:
            raise RuntimeError("scheduler write failed")
        self.cancelled.append(key)
        if key not in self.keys:
            return False
        self.keys.remove(key)
        return True


@pytest.fixture
def environment(tmp_path, monkeypatch):
    waits = ConfirmationStore(tmp_path / "waits.sqlite3")
    monkeypatch.setattr(waits, "ensure_sweeper", lambda: None)
    return (
        FlowRunStore(tmp_path / "runs.sqlite3"),
        WorkflowProposalStore(tmp_path / "proposals.sqlite3"),
        waits,
    )


def _importer(environment, scheduler):
    runs, proposals, waits = environment
    return LegacyActiveCheckpointImporter(
        flow_runs=runs,
        proposals=proposals,
        waits=waits,
        scheduler=scheduler,
    )


def test_active_checkpoint_transfer_is_idempotent_and_removes_legacy_owner(
        environment):
    deadline = time.time() + 3600
    conversion = _conversion(deadline)
    key = conversion["run"]["checkpoint"]["schedule"]["key"]
    scheduler = Scheduler(key)
    importer = _importer(environment, scheduler)
    authorization = {
        "context_id": "migration-authority",
        "revision": 1,
        "root_turn_id": "migration-turn",
    }

    first = importer.import_checkpoint(
        conversion, _flow_ref(), authorization_ref=authorization)
    second = importer.import_checkpoint(
        conversion, _flow_ref(), authorization_ref=authorization)

    runs, proposals, waits = environment
    assert first == second
    assert first["run"]["status"] == "waiting"
    assert first["run"]["checkpoint"]["kind"] == "legacy_plan_verification"
    assert first["proposal"]["status"] == "running"
    assert first["wait"]["task_id"] == "step_1_verify"
    assert first["wait"]["instance_id"] == first["run"]["deployment_instance_id"]
    assert first["wait"]["expires_at"] == deadline
    assert first["flowfile_process_id"] == (
        "ff_legacy_" + conversion["source_digest"][:20])
    assert scheduler.keys == set()
    assert runs.pending_events() == []
    assert len(runs.list("conv-1")) == 1
    assert len(proposals.list(
        user_id="user-a", conversation_id="conv-1")) == 1
    assert len(waits.list_waits()) == 1


def test_active_checkpoint_transfer_compensates_all_new_artifacts(
        environment):
    deadline = time.time() + 3600
    conversion = _conversion(deadline)
    key = conversion["run"]["checkpoint"]["schedule"]["key"]
    importer = _importer(environment, Scheduler(key, fail=True))

    with pytest.raises(RuntimeError, match="scheduler write failed"):
        importer.import_checkpoint(
            conversion,
            _flow_ref(),
            authorization_ref={
                "context_id": "migration-authority",
                "revision": 1,
                "root_turn_id": "migration-turn",
            },
        )

    runs, proposals, waits = environment
    assert runs.get(conversion["run"]["run_id"]) is None
    assert proposals.get(conversion["proposal"]["proposal_id"]) is None
    assert waits.list_waits() == []


def test_active_checkpoint_retry_never_compensates_preexisting_artifacts(
        environment):
    deadline = time.time() + 3600
    conversion = _conversion(deadline)
    key = conversion["run"]["checkpoint"]["schedule"]["key"]
    authorization = {
        "context_id": "migration-authority",
        "revision": 1,
        "root_turn_id": "migration-turn",
    }
    first = _importer(
        environment, Scheduler(key),
    ).import_checkpoint(
        conversion, _flow_ref(), authorization_ref=authorization)

    with pytest.raises(RuntimeError, match="scheduler write failed"):
        _importer(
            environment, Scheduler(key, fail=True),
        ).import_checkpoint(
            conversion, _flow_ref(), authorization_ref=authorization)

    runs, proposals, waits = environment
    assert runs.get(first["run"]["run_id"]) == first["run"]
    assert proposals.get(first["proposal"]["proposal_id"]) == first["proposal"]
    assert waits.list_waits() == [{
        "wait_id": first["wait"]["wait_id"],
        "signal_id": first["wait"]["signal_id"],
        "instance_id": first["wait"]["instance_id"],
        "task_id": first["wait"]["task_id"],
        "created_at": first["wait"]["created_at"],
        "expires_at": first["wait"]["expires_at"],
        "status": "waiting",
        "kind": "timer",
    }]


def test_active_checkpoint_can_defer_legacy_cancellation(environment):
    deadline = time.time() + 3600
    conversion = _conversion(deadline)
    key = conversion["run"]["checkpoint"]["schedule"]["key"]
    scheduler = Scheduler(key)

    imported = _importer(environment, scheduler).import_checkpoint(
        conversion,
        _flow_ref(),
        authorization_ref={
            "context_id": "migration-authority",
            "revision": 1,
            "root_turn_id": "migration-turn",
        },
        cancel_legacy=False,
    )

    assert scheduler.keys == {key}
    assert imported["legacy_schedule"]["key"] == key
