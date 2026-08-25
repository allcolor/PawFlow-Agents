"""WP9 inactive legacy PlanStore state imports."""

import pytest

from core.flow_run_store import FlowRunStore
from core.plan_migration_inactive import LegacyInactivePlanImporter
from core.workflow_proposal_store import (
    ProposalConflict,
    WorkflowProposalStore,
)


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


def _conversion(mode):
    digest = "c" * 64
    review = mode == "review"
    return {
        "schema_version": 1,
        "mode": mode,
        "source_digest": digest,
        "flow": {
            "fqn": "legacy_plans.plan_cccccccccccccccc:1.0.0",
            "scope": "conversation",
            "owner_id": "conv-1",
            "replayable": True,
        },
        "proposal": {
            "proposal_id": f"wp_legacy_{digest[:20]}",
            "status": "user_review" if review else "accepted",
        },
        "run": None if review else {
            "run_id": f"fr_legacy_{digest[:20]}",
            "status": "created",
            "checkpoint": None,
            "terminal": None,
        },
        "steps": [{
            "index": 1,
            "description": "Build",
            "status": "pending",
            "paused": False,
            "note": "",
            "legacy_task_id": "",
            "executor": "builder",
            "executor_adapter": {"adapter": "invokeWorkflowAgent"},
            "verifier": "",
            "verifier_adapter": None,
        }],
        "imported_plan": {
            "source_id": "p_1234",
            "source_path": "user-a/conv-1/p_1234.json",
            "user_id": "user-a",
            "conversation_id": "conv-1",
            "title": "Imported inactive plan",
            "created_by": "planner",
            "created_at": 10.0,
            "updated_at": 20.0,
            "classification": (
                "pending_approval" if review else "approved_not_started"),
        },
    }


@pytest.fixture
def environment(tmp_path):
    return (
        FlowRunStore(tmp_path / "runs.sqlite3"),
        WorkflowProposalStore(tmp_path / "proposals.sqlite3"),
    )


def _importer(environment):
    runs, proposals = environment
    return LegacyInactivePlanImporter(flow_runs=runs, proposals=proposals)


def test_pending_approval_imports_user_review_without_a_run(environment):
    importer = _importer(environment)
    conversion = _conversion("review")

    first = importer.import_state(conversion, _flow_ref())
    second = importer.import_state(conversion, _flow_ref())

    runs, proposals = environment
    assert first == second
    assert first["run"] is None
    assert first["proposal"]["status"] == "user_review"
    assert first["proposal"]["run_ids"] == []
    assert first["artifacts"] == [{
        "kind": "workflow_proposal",
        "id": conversion["proposal"]["proposal_id"],
    }]
    assert runs.list("conv-1") == []
    assert len(proposals.list(
        user_id="user-a", conversation_id="conv-1")) == 1


def test_approved_not_started_imports_created_run_without_starting(environment):
    importer = _importer(environment)
    conversion = _conversion("resume")
    authorization = {
        "context_id": "migration-authority",
        "revision": 1,
        "root_turn_id": "migration-turn",
    }

    first = importer.import_state(
        conversion, _flow_ref(), authorization_ref=authorization)
    second = importer.import_state(
        conversion, _flow_ref(), authorization_ref=authorization)

    runs, _proposals = environment
    assert first == second
    assert first["run"]["status"] == "created"
    assert first["proposal"]["status"] == "accepted"
    assert first["proposal"]["run_ids"] == [first["run"]["run_id"]]
    assert runs.pending_events() == []


def test_approved_not_started_compensates_only_its_new_run(
        environment, monkeypatch):
    conversion = _conversion("resume")
    runs, proposals = environment

    def conflict(**_arguments):
        raise ProposalConflict("projection unavailable")

    monkeypatch.setattr(proposals, "import_inactive", conflict)
    with pytest.raises(ProposalConflict, match="projection unavailable"):
        _importer(environment).import_state(
            conversion,
            _flow_ref(),
            authorization_ref={
                "context_id": "migration-authority",
                "revision": 1,
                "root_turn_id": "migration-turn",
            },
        )

    assert runs.get(conversion["run"]["run_id"]) is None
