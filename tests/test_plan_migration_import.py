"""WP9 archived terminal PlanStore import saga."""

import pytest

from core.flow_run_store import FlowRunStore
from core.plan_migration import (
    build_legacy_conversion_plan,
    legacy_plan_digest,
)
from core.plan_migration_import import LegacyTerminalPlanImporter
from core.workflow_proposal_store import (
    ProposalConflict,
    WorkflowProposalStore,
)


def _flow_ref():
    return {
        "schema_version": 1,
        "resource_type": "flow",
        "name": "legacy_plans.plan_abc:1.0.0",
        "scope": "conversation",
        "owner_id": "conv-1",
        "version": "1.0.0",
        "content_digest": "c" * 64,
        "source_id": "repository:conversation:legacy_plans.plan_abc:1.0.0",
    }


def _conversion(status="completed"):
    plan = {
        "id": "p_1234",
        "conversation_id": "conv-1",
        "title": "Imported plan",
        "status": status,
        "created_by": "planner",
        "created_at": 10.0,
        "updated_at": 20.0,
        "assigned_to": ["builder"],
        "steps": [{
            "index": 1,
            "description": "Build",
            "status": "done" if status == "completed" else "error",
            "assigned_to": "builder",
            "note": "result",
        }],
    }
    classification = "completed" if status == "completed" else "failed"
    record = {
        "source_path": "user-a/conv-1/p_1234.json",
        "source_digest": legacy_plan_digest(plan),
        "user_id": "user-a",
        "conversation_id": "conv-1",
        "plan_id": "p_1234",
        "classification": classification,
        "agent_adapters": {},
        "checkpoint": None,
    }
    return build_legacy_conversion_plan(record, plan)


def test_terminal_import_saga_is_idempotent_and_emits_no_live_outbox(tmp_path):
    runs = FlowRunStore(tmp_path / "runs.sqlite3")
    proposals = WorkflowProposalStore(tmp_path / "proposals.sqlite3")
    importer = LegacyTerminalPlanImporter(
        flow_runs=runs, proposals=proposals)

    first = importer.import_history(_conversion(), _flow_ref())
    second = importer.import_history(_conversion(), _flow_ref())

    assert first == second
    assert first["run"]["status"] == "completed"
    assert first["proposal"]["status"] == "completed"
    assert first["artifacts"] == [
        {"kind": "flow_run", "id": first["run"]["run_id"]},
        {"kind": "workflow_proposal", "id": first["proposal"]["proposal_id"]},
    ]
    assert len(runs.list("conv-1")) == 1
    assert len(proposals.list(user_id="user-a", conversation_id="conv-1")) == 1
    assert runs.pending_events() == []


def test_terminal_import_compensates_only_new_run_on_proposal_conflict(tmp_path):
    runs = FlowRunStore(tmp_path / "runs.sqlite3")
    proposals = WorkflowProposalStore(tmp_path / "proposals.sqlite3")
    conversion = _conversion()
    proposal_id = conversion["proposal"]["proposal_id"]
    proposals.import_terminal(
        proposal_id=proposal_id,
        user_id="user-a",
        conversation_id="conv-1",
        title="Conflicting history",
        summary="different",
        draft_id=f"d_legacy_{conversion['source_digest'][:20]}",
        digest=_flow_ref()["content_digest"],
        created_by="legacy-plan-migration",
        published_flow_ref=_flow_ref(),
        run_id=conversion["run"]["run_id"],
        status="completed",
        import_metadata={
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": "different",
            "source_digest": "d" * 64,
        },
        created_at="1970-01-01T00:00:10+00:00",
        terminal_at="1970-01-01T00:00:20+00:00",
    )
    importer = LegacyTerminalPlanImporter(
        flow_runs=runs, proposals=proposals)

    with pytest.raises(ProposalConflict, match="different imported proposal"):
        importer.import_history(conversion, _flow_ref())

    assert runs.get(conversion["run"]["run_id"]) is None


def test_terminal_import_never_compensates_preexisting_idempotent_run(
        monkeypatch, tmp_path):
    runs = FlowRunStore(tmp_path / "runs.sqlite3")
    proposals = WorkflowProposalStore(tmp_path / "proposals.sqlite3")
    importer = LegacyTerminalPlanImporter(
        flow_runs=runs, proposals=proposals)
    conversion = _conversion()
    first = importer.import_history(conversion, _flow_ref())

    def fail_proposal(**_arguments):
        raise ProposalConflict("projection unavailable")

    monkeypatch.setattr(proposals, "import_terminal", fail_proposal)
    with pytest.raises(ProposalConflict, match="projection unavailable"):
        importer.import_history(conversion, _flow_ref())

    assert runs.get(first["run"]["run_id"]) == first["run"]


def test_import_compensation_requires_exact_source_provenance(tmp_path):
    runs = FlowRunStore(tmp_path / "runs.sqlite3")
    proposals = WorkflowProposalStore(tmp_path / "proposals.sqlite3")
    imported = LegacyTerminalPlanImporter(
        flow_runs=runs, proposals=proposals,
    ).import_history(_conversion(), _flow_ref())

    with pytest.raises(ValueError, match="provenance"):
        runs.delete_imported(
            imported["run"]["run_id"],
            import_metadata={
                **imported["run"]["import_metadata"],
                "source_id": "different",
            },
        )

    assert runs.get(imported["run"]["run_id"]) == imported["run"]
