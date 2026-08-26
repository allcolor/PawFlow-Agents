import json

import pytest

from core.flow_authoring import FlowAuthoringService
from core.handlers.workflow_proposals import (
    GetWorkflowProposalHandler,
    ProposeWorkflowHandler,
    ReviewWorkflowProposalHandler,
)
from core.workflow_proposal_store import WorkflowProposalStore
from tasks import register_all_tasks


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    register_all_tasks()
    FlowAuthoringService.reset()
    WorkflowProposalStore.reset()
    yield
    FlowAuthoringService.reset()
    WorkflowProposalStore.reset()


def _configured(handler):
    handler.set_user_id("alice")
    handler.set_conversation_id("conv")
    handler.set_agent_name("planner")
    return handler


def _definition():
    return {
        "name": "Release", "tasks": {
            "step": {"type": "log", "parameters": {"message": "test"}},
        },
        "services": {}, "groups": {}, "relations": [],
        "entries": ["step"], "exits": ["step"],
    }


def test_planner_can_propose_get_and_review_user_revision():
    created = json.loads(_configured(ProposeWorkflowHandler()).execute({
        "package": "plans", "name": "release", "version": "1.0.0",
        "title": "Prepare release", "summary": "Test and publish",
        "definition": _definition(),
    }))
    proposal = created["proposal"]
    assert proposal["status"] == "user_review"
    loaded = json.loads(_configured(GetWorkflowProposalHandler()).execute({
        "proposal_id": proposal["proposal_id"],
    }))
    assert loaded["proposal"]["proposal_id"] == proposal["proposal_id"]

    draft = FlowAuthoringService.instance().load_draft(
        proposal["draft_id"], "alice")
    definition = draft["definition"]
    definition["description"] = "User revised"
    saved = FlowAuthoringService.instance().save_draft(
        proposal["draft_id"], "alice", definition, draft["revision"])
    from core.workflow_proposal_store import definition_digest
    submitted = WorkflowProposalStore.instance().note_draft_changed(
        draft_id=proposal["draft_id"], draft_revision=saved["revision"],
        digest=definition_digest(saved["definition"]), actor_id="alice")
    submitted = WorkflowProposalStore.instance().submit_to_planner(
        proposal["proposal_id"],
        expected_state_revision=submitted["state_revision"],
        draft_revision=saved["revision"],
        digest=definition_digest(saved["definition"]), actor_id="alice")

    reviewed = json.loads(_configured(ReviewWorkflowProposalHandler()).execute({
        "proposal_id": proposal["proposal_id"],
        "state_revision": submitted["state_revision"],
        "decision": "accept", "comment": "Revision is valid.",
    }))
    assert reviewed["proposal"]["status"] == "user_review"
    assert reviewed["proposal"]["planner_reviewed_revision"] == saved["revision"]


def test_planner_refuses_a_draft_changed_after_exact_submission():
    created = json.loads(_configured(ProposeWorkflowHandler()).execute({
        "package": "plans", "name": "release", "version": "1.0.0",
        "title": "Prepare release", "definition": _definition(),
    }))
    proposal = created["proposal"]
    submitted = WorkflowProposalStore.instance().submit_to_planner(
        proposal["proposal_id"],
        expected_state_revision=proposal["state_revision"],
        draft_revision=proposal["draft_revision"],
        digest=proposal["definition_digest"], actor_id="alice")
    draft = FlowAuthoringService.instance().load_draft(
        proposal["draft_id"], "alice")
    definition = draft["definition"]
    definition["description"] = "Changed after submission"
    FlowAuthoringService.instance().save_draft(
        proposal["draft_id"], "alice", definition, draft["revision"])

    result = json.loads(_configured(ReviewWorkflowProposalHandler()).execute({
        "proposal_id": proposal["proposal_id"],
        "state_revision": submitted["state_revision"],
        "decision": "accept",
    }))
    assert result["error"] == "draft_changed_after_submission"
    assert result["proposal"]["status"] == "user_review"
    assert result["proposal"]["review_history"][-1]["action"] == (
        "planner_review_invalidated")


def test_planner_tool_is_always_available():
    result = _configured(ProposeWorkflowHandler()).execute({
        "package": "plans", "name": "release", "version": "1.0.0",
        "title": "Prepare release", "definition": _definition(),
    })
    assert json.loads(result)["proposal"]["status"] == "user_review"
