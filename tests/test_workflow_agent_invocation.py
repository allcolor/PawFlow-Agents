"""WP6 durable Workflow Agent invocation from normal flows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core import FlowFile
from core.agent_contracts import AuthorizationRefContract
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    PreparedAgentTurn,
    WorkflowInstanceConfig,
)
from core.workflow_agent_invocation import (
    MAX_FLOW_INVOCATION_DEPTH,
    WorkflowParentInvocationStore,
    validate_flow_invocation_ancestry,
)
from core.workflow_agent_runtime import WorkflowAgentRuntime, _ActiveRun
from core.workflow_turn_coordinator import WorkflowTurnCoordinator
from tasks.control.invoke_workflow_agent import InvokeWorkflowAgentTask


def _flow_ref() -> ResourceRef:
    return ResourceRef(
        resource_type="flow",
        name="pawflow.agents.review:1.0.0",
        scope="global",
        version="1.0.0",
        content_digest="a" * 64,
        source_id="repository:global:pawflow.agents.review:1.0.0",
    )


def _agent_ref() -> ResourceRef:
    return ResourceRef(
        resource_type="agent",
        name="Reviewer",
        scope="global",
        version="1.0.0",
        content_digest="b" * 64,
        source_id="repository:global:Reviewer",
    )


def _binding() -> WorkflowInstanceConfig:
    return WorkflowInstanceConfig.from_dict({
        "flow_fqn": _flow_ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": ["resource.read"],
        "flow_ref": _flow_ref().to_dict(),
    })


def _turn(generation: int = 1) -> PreparedAgentTurn:
    turn_id = "web:root"
    authorization = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1, root_turn_id=turn_id)
    identity = AgentTurnIdentity(
        conversation_id="conv-1",
        root_conversation_id="conv-1",
        agent_instance="Reviewer",
        turn_id=turn_id,
        ingress_msg_id=turn_id,
        turn_epoch=generation,
        run_generation=generation,
        authorization_context_id=authorization.context_id,
        authorization_revision_at_start=authorization.revision,
        source_kind="task",
        source_id="invoke-1",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return PreparedAgentTurn(
        turn_identity=identity,
        conversation_id="conv-1",
        agent_name="Reviewer",
        user_id="alice",
        root_turn_id=turn_id,
        request_message_ids=(turn_id,),
        channel="flow",
        message="Review this",
        source={"type": "task", "authorization": authorization.to_dict()},
        permission_mode="read_only",
        authorization_ref=authorization,
    )


def _parent(flowfile: FlowFile) -> dict:
    return {
        "invocation_id": "wfi_parent",
        "instance_id": "deployment-1",
        "task_id": "review",
        "flowfile_process_id": flowfile.process_id,
        "parent_flow_run_id": "",
        "authorization_ref": _turn().authorization_ref.to_dict(),
        "invocation_depth": 1,
        "ancestor_agent_refs": [_agent_ref().to_dict()],
        "ancestor_flow_refs": [],
        "cancellation_policy": "propagate",
        "publish_to_conversation": False,
    }


def test_parent_invocation_is_idempotent_and_survives_reopen(tmp_path):
    database = tmp_path / "parent.sqlite3"
    store = WorkflowParentInvocationStore(database)
    flowfile = FlowFile(content=b"candidate", attributes={"kind": "draft"})
    parent = _parent(flowfile)

    first = store.create(parent=parent, flowfile=flowfile)
    second = store.create(parent=parent, flowfile=flowfile)

    assert first["invocation_id"] == second["invocation_id"] == "wfi_parent"
    assert first["state"] == "created"
    reopened = WorkflowParentInvocationStore(database)
    record = reopened.get("wfi_parent")
    assert record["flowfile_process_id"] == flowfile.process_id
    assert record["flowfile"].get_content() == b"candidate"

    changed = dict(parent, task_id="other")
    with pytest.raises(ValueError, match="different parent"):
        reopened.create(parent=changed, flowfile=flowfile)


def test_parent_terminal_delivery_projects_result_and_is_exactly_once(
        tmp_path, monkeypatch):
    store = WorkflowParentInvocationStore(tmp_path / "parent.sqlite3")
    flowfile = FlowFile(content=b"candidate")
    parent = _parent(flowfile)
    store.create(parent=parent, flowfile=flowfile)
    store.bind_child("wfi_parent", "wr_child")

    executor = MagicMock()
    executor.is_running = True
    executor.inject.return_value = True
    registry = MagicMock()
    registry.get.return_value = executor
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        classmethod(lambda cls: registry),
    )
    event = {
        "event_id": "event-1",
        "run_id": "wr_child",
        "status": "completed",
        "response": "Approved",
        "artifacts": [{"kind": "file", "id": "a1", "label": "Report"}],
        "metrics": {"llm_calls": 2},
        "answered_turn_ids": ["web:root"],
    }

    assert store.deliver_terminal("wfi_parent", event) is True
    assert store.deliver_terminal("wfi_parent", event) is True
    executor.inject.assert_called_once()
    resumed = executor.inject.call_args.args[0]
    assert resumed.process_id == flowfile.process_id
    assert resumed.get_attribute("workflow.agent.status") == "completed"
    assert resumed.get_attribute("workflow.agent.response") == "Approved"
    assert resumed.get_attribute("route.relationship") == "completed"
    assert executor.inject.call_args.kwargs == {"entry_task_id": "review"}


def test_parent_cancellation_returns_only_propagated_children(tmp_path):
    store = WorkflowParentInvocationStore(tmp_path / "parent.sqlite3")
    first_flowfile = FlowFile()
    first = _parent(first_flowfile)
    store.create(parent=first, flowfile=first_flowfile)
    store.bind_child(first["invocation_id"], "wr_propagate")

    second_flowfile = FlowFile()
    second = {
        **_parent(second_flowfile),
        "invocation_id": "wfi_detached",
        "cancellation_policy": "detach",
    }
    store.create(parent=second, flowfile=second_flowfile)
    store.bind_child(second["invocation_id"], "wr_detached")

    assert store.cancel_instance("deployment-1") == ("wr_propagate",)
    assert store.get("wfi_parent")["state"] == "cancelled"
    assert store.get("wfi_detached")["state"] == "cancelled"


def test_submit_flow_has_stable_run_id_while_queued_and_is_idempotent(monkeypatch):
    runtime = WorkflowAgentRuntime()
    binding = _binding()
    key = runtime._key("conv-1", "Reviewer")
    runtime._active[key] = _ActiveRun(request=_turn(1), run_id="wr_active")
    parent = _parent(FlowFile())
    started = []
    monkeypatch.setattr(
        runtime, "_start_worker",
        lambda worker_key, active: started.append((worker_key, active)),
    )

    first = runtime.submit_flow(
        _turn(2), binding, parent=parent, run_id="wr_flow_parent")
    second = runtime.submit_flow(
        _turn(2), binding, parent=parent, run_id="wr_flow_parent")

    assert first == second == {
        "status": "accepted",
        "queued": True,
        "checkpoint": False,
        "run_id": "wr_flow_parent",
    }
    assert len(runtime._pending[key]) == 1
    assert runtime._pending[key][0].run_id == "wr_flow_parent"
    assert started == []


def test_flow_invocation_context_carries_parent_and_is_not_user_visible():
    runtime = WorkflowAgentRuntime()
    flowfile = FlowFile()
    parent = _parent(flowfile)
    active = _ActiveRun(
        request=_turn(),
        run_id="wr_flow_parent",
        binding=_binding(),
        invocation_mode="flow",
        parent_invocation=parent,
    )

    context = runtime._context(active, _binding())

    assert context.invocation_mode == "flow"
    assert context.parent_invocation == parent
    assert context.invocation_depth == 1
    runtime._active[runtime._key("conv-1", "Reviewer")] = active
    assert runtime.active_snapshot("conv-1") == []


def test_flow_terminal_is_silent_and_delivered_to_parent(monkeypatch):
    run_store = MagicMock()
    run_store.get_run.side_effect = [
        {
            "status": "committing",
            "invocation_mode": "flow",
            "publish_to_conversation": False,
            "message_committed": False,
            "inbox_acknowledged": True,
            "parent_invocation": {"invocation_id": "wfi_parent"},
            "conversation_id": "conv-1",
            "agent_name": "Reviewer",
            "user_id": "alice",
        },
        {
            "status": "committing",
            "invocation_mode": "flow",
            "publish_to_conversation": False,
            "message_committed": True,
            "inbox_acknowledged": True,
            "parent_invocation": {"invocation_id": "wfi_parent"},
            "conversation_id": "conv-1",
            "agent_name": "Reviewer",
            "user_id": "alice",
        },
        {"terminal_event": {"event_id": "event-1"}},
    ]
    run_store.is_current_generation.return_value = True
    run_store.pending_outbox.return_value = [{
        "event_id": "event-1",
        "event": {"event_id": "event-1", "status": "completed"},
    }]
    run_store.complete.return_value = True
    parent_store = MagicMock()
    parent_store.deliver_terminal.return_value = True
    monkeypatch.setattr(
        "core.workflow_agent_invocation.WorkflowParentInvocationStore.instance",
        classmethod(lambda cls: parent_store),
    )
    writer = MagicMock()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda *_args, **_kwargs: writer,
    )
    coordinator = WorkflowTurnCoordinator(run_store, inbox_store=MagicMock())

    coordinator.commit("wr_flow_parent")

    writer.enqueue_message_if_absent.assert_not_called()
    parent_store.deliver_terminal.assert_called_once_with(
        "wfi_parent", {"event_id": "event-1", "status": "completed"})
    run_store.record_outbox_attempt.assert_called_once_with("event-1", True)


def test_recursion_guard_rejects_depth_and_repeated_exact_refs():
    with pytest.raises(ValueError, match="depth"):
        validate_flow_invocation_ancestry(
            agent_ref=_agent_ref(),
            flow_ref=_flow_ref(),
            invocation_depth=MAX_FLOW_INVOCATION_DEPTH + 1,
            ancestor_agent_refs=(),
            ancestor_flow_refs=(),
        )
    with pytest.raises(ValueError, match="ancestor agent"):
        validate_flow_invocation_ancestry(
            agent_ref=_agent_ref(),
            flow_ref=_flow_ref(),
            invocation_depth=1,
            ancestor_agent_refs=(_agent_ref(),),
            ancestor_flow_refs=(),
        )
    with pytest.raises(ValueError, match="ancestor flow"):
        validate_flow_invocation_ancestry(
            agent_ref=_agent_ref(),
            flow_ref=_flow_ref(),
            invocation_depth=1,
            ancestor_agent_refs=(),
            ancestor_flow_refs=(_flow_ref(),),
        )


def test_invoke_task_creates_parent_before_submit_and_parks(
        monkeypatch):
    flowfile = FlowFile(content=b"candidate")
    task = InvokeWorkflowAgentTask({
        "agent_ref": _agent_ref().to_dict(),
        "message": "Review this",
        "parameters": {},
        "await_terminal": True,
        "publish_to_conversation": False,
        "terminal_timeout": "15m",
        "cancellation_policy": "propagate",
    })
    task.set_runtime_context(
        user_id="alice", conversation_id="conv-1",
        scope="conversation", agent_name="Parent")
    parent_context = MagicMock()
    parent_context.run_id = "parent-run"
    parent_context.authorization_ref = _turn().authorization_ref
    parent_context.invocation_depth = 0
    parent_context.ancestor_agent_refs = ()
    parent_context.ancestor_flow_refs = ()
    parent_context.flow_ref = ResourceRef(
        resource_type="flow",
        name="pawflow.parent:1.0.0",
        scope="global",
        version="1.0.0",
        content_digest="c" * 64,
        source_id="repository:global:pawflow.parent:1.0.0",
    )
    task.set_workflow_run_context(parent_context)
    monkeypatch.setattr(
        "tasks.control.invoke_workflow_agent.find_own_flow_ids",
        lambda _task: {"instance_id": "deployment-1", "task_id": "review"},
    )
    monkeypatch.setattr(
        "tasks.control.invoke_workflow_agent.resolve_workflow_agent_binding",
        lambda **_kwargs: (_agent_ref(), _binding()),
    )
    prepared = _turn(2)
    monkeypatch.setattr(
        "tasks.control.invoke_workflow_agent.prepare_workflow_turn",
        lambda **_kwargs: prepared,
    )
    calls = []
    parent_store = MagicMock()
    parent_store.create.side_effect = lambda **kwargs: calls.append(
        ("create", kwargs)) or {"state": "created"}
    parent_store.bind_child.side_effect = lambda *args: calls.append(
        ("bind", args))
    monkeypatch.setattr(
        "tasks.control.invoke_workflow_agent.WorkflowParentInvocationStore.instance",
        classmethod(lambda cls: parent_store),
    )
    runtime = MagicMock()
    runtime.submit_flow.side_effect = lambda *args, **kwargs: calls.append(
        ("submit", (args, kwargs))) or {
            "status": "accepted", "queued": False, "run_id": kwargs["run_id"]}
    monkeypatch.setattr(
        "tasks.control.invoke_workflow_agent.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime),
    )

    assert task.execute(flowfile) == []

    assert [item[0] for item in calls] == ["create", "submit", "bind"]
    parent = calls[0][1]["parent"]
    assert parent["parent_flow_run_id"] == "parent-run"
    assert parent["invocation_depth"] == 1
    assert parent["publish_to_conversation"] is False
    assert flowfile.get_attribute("workflow.agent.invocation_id")
    assert flowfile.get_attribute("workflow.agent.run_id")


def test_invoke_task_passes_through_a_resumed_terminal():
    task = InvokeWorkflowAgentTask({
        "agent_ref": _agent_ref().to_dict(),
        "message": "Review this",
    })
    flowfile = FlowFile(attributes={
        "workflow.agent.status": "no_change",
        "route.relationship": "no_change",
    })

    assert task.execute(flowfile) == [flowfile]


def test_invoke_task_accepts_durable_flow_run_authority():
    task = InvokeWorkflowAgentTask({
        "agent_ref": _agent_ref().to_dict(),
        "message": "Review this",
    })
    authorization = _turn().authorization_ref.to_dict()

    task.set_flow_run_context({
        "run_id": "fr_parent",
        "flow_ref": {
            **_flow_ref().to_dict(),
            "name": "pawflow.parent:1.0.0",
        },
        "authorization_ref": authorization,
    })

    assert task._authorization_ref().to_dict() == authorization
