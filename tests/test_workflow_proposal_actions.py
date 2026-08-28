import json
from types import SimpleNamespace

import pytest

from core import FlowFile
from core.authorization_context import AuthorizationContextStore
from core.deployment_registry import DeploymentRegistry
from core.flow_authoring import FlowAuthoringService
from core.flow_run_store import FlowRunStore
from core.service_registry import ServiceRegistry
from core.workflow_proposal_store import WorkflowProposalStore
from tasks import register_all_tasks
from tasks.ai.actions.workflow_proposals import _handle_workflow_proposals


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    register_all_tasks()
    events = []
    monkeypatch.setattr(
        "core.workflow_proposal_notifications.resolve_planner_target",
        lambda _cid, planner, _uid: planner)
    monkeypatch.setattr(
        "core.workflow_proposal_notifications.queue_planner_event",
        lambda proposal, **kwargs: events.append((proposal, kwargs)))
    FlowAuthoringService.reset()
    AuthorizationContextStore._instance = None
    DeploymentRegistry.reset()
    FlowRunStore.reset()
    ServiceRegistry.reset()
    WorkflowProposalStore.reset()
    yield events
    FlowAuthoringService.reset()
    AuthorizationContextStore._instance = None
    DeploymentRegistry.reset()
    FlowRunStore.reset()
    ServiceRegistry.reset()
    WorkflowProposalStore.reset()


def _call(action, body, user_id="alice"):
    flowfile = FlowFile()
    result = _handle_workflow_proposals(
        None, action, body, None, user_id, flowfile)
    assert result is not None
    return (
        json.loads(flowfile.get_content().decode()),
        flowfile.get_attribute("http.response.status") or "200",
    )


def test_list_returns_renderer_independent_surfaces():
    draft = _draft()
    created, status = _call("workflow_proposal_create", {
        "conversation_id": "conv", "draft_id": draft["draft_id"],
        "planner_id": "Planner", "title": "Release",
    })
    assert status == "200"
    assert created["surface"]["producer"]["kind"] == "workflow_proposal"
    listed, status = _call(
        "workflow_proposal_list", {"conversation_id": "conv"})
    assert status == "200"
    assert listed["surfaces"][0]["surface_id"] == created["surface"]["surface_id"]


def _draft():
    return FlowAuthoringService.instance().new(
        "plans", "release", "1.0.0", "conv", "alice",
        conv_id="conv")


def _durable_draft():
    return FlowAuthoringService.instance().new_from_definition(
        "plans", "durable_release", "1.0.0", "conv", "alice", {
            "name": "Durable release",
            "execution_mode": "durable_one_shot",
            "run_contract": {"mode": "durable_one_shot"},
            "tasks": {
                "done": {"type": "completeFlowRun", "parameters": {}},
            },
            "services": {}, "groups": {}, "relations": [],
            "entries": ["done"], "exits": ["done"],
        }, conv_id="conv")


def _accept_durable_proposal():
    draft = _durable_draft()
    proposal = _call("workflow_proposal_create", {
        "conversation_id": "conv", "draft_id": draft["draft_id"],
        "title": "Durable release", "planner_id": "assistant",
    })[0]["proposal"]
    accepted = _call("workflow_proposal_accept", {
        "conversation_id": "conv", "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
    })[0]["proposal"]
    return draft, accepted


def _stub_run_start(monkeypatch):
    deployment = SimpleNamespace(
        max_workers=1,
    )
    monkeypatch.setattr(
        "core.deployment_registry.DeploymentRegistry.deploy",
        lambda self, *args, **kwargs: kwargs["instance_id"])
    monkeypatch.setattr(
        "core.deployment_registry.DeploymentRegistry.update_flow_version",
        lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        "core.deployment_registry.DeploymentRegistry.get",
        lambda self, _instance_id: deployment)

    def attach(self, run_id, _executor, **_kwargs):
        self.store.transition(run_id, "starting")
        self.store.transition(run_id, "running")

    monkeypatch.setattr(
        "core.flow_run_coordinator.FlowRunCoordinator.attach_and_start", attach)


def test_actions_are_registered_and_always_available():
    from tasks.ai import agent_actions
    assert _handle_workflow_proposals in agent_actions._ACTION_HANDLERS
    data, status = _call(
        "workflow_proposal_list", {"conversation_id": "conv"})
    assert status == "200"
    assert data == {"proposals": [], "surfaces": []}


def test_user_edits_round_trip_to_planner_then_accepts(isolated):
    draft = _draft()
    data, status = _call("workflow_proposal_create", {
        "conversation_id": "conv", "draft_id": draft["draft_id"],
        "title": "Release", "summary": "Review and publish",
        "planner_id": "assistant",
    })
    assert status == "200"
    proposal = data["proposal"]

    definition = draft["definition"]
    definition["description"] = "User edit"
    saved = FlowAuthoringService.instance().save_draft(
        draft["draft_id"], "alice", definition, 0)
    data, status = _call("workflow_proposal_get", {
        "conversation_id": "conv",
        "proposal_id": proposal["proposal_id"],
    })
    assert status == "200"
    assert data["proposal"]["can_accept"] is False

    data, status = _call("workflow_proposal_accept", {
        "conversation_id": "conv",
        "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
    })
    assert status == "409"
    assert data["error"] == "draft_requires_planner_review"
    proposal = data["proposal"]

    data, status = _call("workflow_proposal_submit_to_planner", {
        "conversation_id": "conv",
        "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
        "comment": "Please review my edit.",
    })
    assert status == "200"
    submitted = data["proposal"]
    assert submitted["status"] == "planner_review"
    assert submitted["draft_revision"] == saved["revision"]

    reviewed = WorkflowProposalStore.instance().planner_review(
        proposal["proposal_id"],
        expected_state_revision=submitted["state_revision"],
        draft_revision=submitted["draft_revision"],
        digest=submitted["definition_digest"], actor_id="assistant",
        decision="accept", comment="Looks good.",
    )
    data, status = _call("workflow_proposal_accept", {
        "conversation_id": "conv",
        "proposal_id": proposal["proposal_id"],
        "state_revision": reviewed["state_revision"],
    })
    assert status == "200"
    assert data["proposal"]["status"] == "accepted"
    assert [event[1]["action"] for event in isolated] == [
        "submitted_to_planner", "accepted"]
    assert isolated[0][1]["comment"] == "Please review my edit."


def test_foreign_user_and_stale_state_fail_closed():
    draft = _draft()
    proposal = _call("workflow_proposal_create", {
        "conversation_id": "conv", "draft_id": draft["draft_id"],
        "title": "Release", "planner_id": "assistant",
    })[0]["proposal"]
    assert _call("workflow_proposal_get", {
        "conversation_id": "conv", "proposal_id": proposal["proposal_id"],
    }, user_id="mallory")[1] == "404"
    _call("workflow_proposal_cancel", {
        "conversation_id": "conv", "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
    })
    data, status = _call("workflow_proposal_cancel", {
        "conversation_id": "conv", "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
    })
    assert status == "409"
    assert data["error"] == "workflow_proposal_conflict"


def test_approve_publishes_exact_revision_and_starts_one_authorized_run(
        monkeypatch):
    _stub_run_start(monkeypatch)
    _draft_row, accepted = _accept_durable_proposal()

    data, status = _call("workflow_proposal_approve", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": accepted["state_revision"],
    })

    assert status == "200"
    assert data["proposal"]["status"] == "running"
    assert data["proposal"]["published_flow_ref"]["content_digest"] == (
        data["run"]["flow_ref"]["content_digest"])
    authority = data["run"]["authorization_ref"]
    assert AuthorizationContextStore.instance().snapshot(
        "alice", "conv", authority["context_id"], authority["revision"])
    execution_authority = data["run"]["execution_authority"]
    assert execution_authority == {
        "agent_name": "assistant",
        "permission_mode": "default",
        "allowed_effects": ["resource.write"],
        "service_snapshot": {"bindings": {}, "services": {}},
    }
    assert len(FlowRunStore.instance().list("conv")) == 1


def test_approval_rejects_undeclared_task_before_creating_run(monkeypatch):
    from core import TaskFactory
    from core.base_task import BaseTask

    class UnsafeTask(BaseTask):
        TYPE = "testUndeclaredWorkflowTask"

        def execute(self, flowfile):
            return [flowfile]

    TaskFactory.register(UnsafeTask)
    draft = FlowAuthoringService.instance().new_from_definition(
        "plans", "unsafe_release", "1.0.0", "conv", "alice", {
            "name": "Unsafe release",
            "execution_mode": "durable_one_shot",
            "run_contract": {"mode": "durable_one_shot"},
            "tasks": {
                "unsafe": {"type": UnsafeTask.TYPE, "parameters": {}},
                "done": {"type": "completeFlowRun", "parameters": {}},
            },
            "services": {}, "groups": {},
            "relations": [{"from": "unsafe", "to": "done", "type": "success"}],
            "entries": ["unsafe"], "exits": ["done"],
        }, conv_id="conv")
    proposal = _call("workflow_proposal_create", {
        "conversation_id": "conv", "draft_id": draft["draft_id"],
        "title": "Unsafe release", "planner_id": "assistant",
    })[0]["proposal"]
    accepted = _call("workflow_proposal_accept", {
        "conversation_id": "conv", "proposal_id": proposal["proposal_id"],
        "state_revision": proposal["state_revision"],
    })[0]["proposal"]
    _stub_run_start(monkeypatch)

    data, status = _call("workflow_proposal_approve", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": accepted["state_revision"],
    })

    assert status == "400"
    assert "not workflow-safe" in data["error"]
    assert FlowRunStore.instance().list("conv") == []


def test_terminal_outbox_projects_once_and_replay_gets_new_identity(
        monkeypatch):
    _stub_run_start(monkeypatch)
    _draft_row, accepted = _accept_durable_proposal()
    approved = _call("workflow_proposal_approve", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": accepted["state_revision"],
    })[0]
    run = approved["run"]
    terminal = {"schema_version": 1, "summary": "done", "artifacts": []}
    runs = FlowRunStore.instance()
    runs.stage_terminal(run["run_id"], terminal)
    runs.commit(run["run_id"])

    projected, status = _call("workflow_proposal_get", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
    })
    assert status == "200"
    assert projected["proposal"]["status"] == "completed"
    assert runs.pending_events() == []
    listed = _call(
        "workflow_proposal_list", {"conversation_id": "conv"})[0]
    terminal_actions = {
        row["id"] for row in listed["surfaces"][0]["semantic"]["actions"]}
    assert {"inspect_run", "replay"}.issubset(terminal_actions)

    replayed, status = _call("workflow_proposal_replay", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": projected["proposal"]["state_revision"],
        "run_id": run["run_id"],
    })
    assert status == "200"
    assert replayed["run"]["run_id"] != run["run_id"]
    assert replayed["run"]["replay_of"] == run["run_id"]
    assert replayed["run"]["authorization_ref"] != run["authorization_ref"]
    assert replayed["proposal"]["run_ids"] == [
        run["run_id"], replayed["run"]["run_id"]]


def test_run_inspection_is_scoped_to_proposal_and_user(monkeypatch):
    _stub_run_start(monkeypatch)
    _draft_row, accepted = _accept_durable_proposal()
    approved = _call("workflow_proposal_approve", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": accepted["state_revision"],
    })[0]

    inspected, status = _call("workflow_proposal_inspect_run", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "run_id": approved["run"]["run_id"],
    })
    assert status == "200"
    assert inspected["run"]["run_id"] == approved["run"]["run_id"]
    assert "authorization_ref" not in inspected["run"]
    assert inspected["surface"]["producer"] == {
        "kind": "flow_run", "id": approved["run"]["run_id"]}
    assert _call("workflow_proposal_inspect_run", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "run_id": approved["run"]["run_id"],
    }, user_id="mallory")[1] == "404"


def test_start_failure_is_durable_and_does_not_leave_proposal_approved(
        monkeypatch):
    _draft_row, accepted = _accept_durable_proposal()
    monkeypatch.setattr(
        "tasks.ai.actions.workflow_proposals._start_flow_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("start failed")))

    data, status = _call("workflow_proposal_approve", {
        "conversation_id": "conv", "proposal_id": accepted["proposal_id"],
        "state_revision": accepted["state_revision"],
    })

    assert status == "500"
    assert data["error"] == "start failed"
    proposal = WorkflowProposalStore.instance().get(accepted["proposal_id"])
    assert proposal["status"] == "failed"
    run = FlowRunStore.instance().get(proposal["run_ids"][0])
    assert run["status"] == "failed"
    assert run["error"] == "start failed"
    assert FlowRunStore.instance().pending_events() == []
