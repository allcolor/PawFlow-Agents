import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from core import FlowFile
from core.agent_contracts import AuthorizationRefContract
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    AgentWorkflowResult,
    WorkflowConversationRef,
    WorkflowInstanceConfig,
    WorkflowLimits,
    WorkflowRequestBody,
    WorkflowRunContext,
    WorkflowRunError,
    WorkflowTurnRef,
)
from core.workflow_run_store import (
    WorkflowBudgetExceeded,
    WorkflowRunStore,
    new_terminal_identities,
)
from core.workflow_turn_coordinator import WorkflowTurnCoordinator


def _ref():
    return ResourceRef(
        resource_type="flow", name="demo:1.0.0", scope="global",
        version="1.0.0", content_digest="a" * 64, source_id="test")


def _context(run_id="wr_test", generation=1, permission_mode="default",
             invocation_mode="conversation", channel="web"):
    auth = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1, root_turn_id="m1")
    identity = AgentTurnIdentity(
        conversation_id="c1", root_conversation_id="c1",
        agent_instance="Demo", turn_id="m1", ingress_msg_id="m1",
        turn_epoch=1, run_generation=generation,
        authorization_context_id=auth.context_id,
        authorization_revision_at_start=1, source_kind="user",
        created_at=datetime.now(timezone.utc).isoformat())
    return WorkflowRunContext(
        run_id=run_id, turn_identity=identity, conversation_id="c1",
        agent_name="Demo", user_id="alice", root_turn_id="m1",
        run_generation=generation, flow_ref=_ref(), channel=channel,
        invocation_mode=invocation_mode, permission_mode=permission_mode,
        authorization_ref=auth,
        deadline_at=(datetime.now(timezone.utc)
                     + timedelta(minutes=5)).isoformat(),
        limits=WorkflowLimits(
            max_duration_seconds=300, max_llm_calls=1,
            max_flowfiles=20, max_fanout=2),
        service_snapshot={}, cancel_token="cancel", event_sink="test")


def _request():
    return AgentWorkflowRequest(
        request=WorkflowRequestBody(message="hello"),
        conversation=WorkflowConversationRef(id="c1", agent="Demo"),
        turn=WorkflowTurnRef(
            root_turn_id="m1", request_message_ids=("m1",)),
        parameters={})


@pytest.fixture
def store(tmp_path):
    return WorkflowRunStore(tmp_path / "runs.sqlite3")


def _create_running(store, run_id="wr_test", generation=1,
                    invocation_mode="conversation", channel="web"):
    context = _context(
        run_id, generation, invocation_mode=invocation_mode, channel=channel)
    store.create_run(
        context=context, request=_request(), parameters={},
        lease_seconds=300)
    assert store.transition(run_id, "accepted", "running")
    return context


def test_generation_and_active_run_are_durable(store):
    assert store.reserve_generation("c1", "Demo") == 1
    assert store.reserve_generation("c1", "demo") == 2
    context = _create_running(store, generation=2)

    assert store.is_current_generation(context.run_id)
    assert store.get_record(context.run_id).status == "running"


def test_run_snapshots_exact_workflow_binding(store):
    context = _context(permission_mode="read_only")
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": context.flow_ref.name,
        "flow_scope": context.flow_ref.scope,
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "checkpoint",
        "allowed_effects": ["resource.read"],
        "parameters": {"tone": "brief"},
        "limits": context.limits.to_dict(),
        "flow_ref": context.flow_ref.to_dict(),
    })

    store.create_run(
        context=context, request=_request(), parameters=binding.parameters,
        lease_seconds=300, binding=binding.to_dict())

    reopened = WorkflowRunStore(store.database_path)
    assert reopened.get_run(context.run_id)["binding"] == binding.to_dict()
    assert reopened.get_run(context.run_id)["permission_mode"] == "read_only"


def test_run_snapshots_and_recovers_delegate_ingress_source(store):
    from core.workflow_agent_runtime import WorkflowAgentRuntime

    context = _context()
    source = {
        "type": "agent_delegate", "from": "Planner", "to": "Demo",
        "task_id": "delegate-1",
    }
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=300,
        ingress_source=source)

    reopened = WorkflowRunStore(store.database_path)
    run = reopened.get_run(context.run_id)
    assert run["ingress_source"] == source
    recovered = WorkflowAgentRuntime._prepared_from_run(run)
    assert recovered.source == source
    assert recovered.turn_identity.source_kind == "delegate"
    assert recovered.turn_identity.source_id == "Planner"


def test_legacy_run_permission_migration_fails_closed(store):
    context = _context(permission_mode="default")
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=300)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "ALTER TABLE workflow_runs DROP COLUMN permission_mode")

    reopened = WorkflowRunStore(store.database_path)

    assert reopened.get_run(context.run_id)["permission_mode"] == "read_only"


def test_service_snapshot_is_set_once_and_immutable(store):
    context = _context()
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=300)
    snapshot = {
        "bindings": {"writer": "Writer"},
        "services": {"Writer": {"definition_revision": "a" * 64}},
    }

    assert store.set_service_snapshot(
        context.run_id, snapshot)["service_snapshot"] == snapshot
    assert store.set_service_snapshot(
        context.run_id, snapshot)["service_snapshot"] == snapshot
    with pytest.raises(RuntimeError, match="immutable"):
        store.set_service_snapshot(context.run_id, {"services": {}})


def test_terminal_saga_requires_every_durable_boundary(store):
    context = _create_running(store)
    message_id, event_id = new_terminal_identities(context.run_id)
    result = AgentWorkflowResult(
        status="completed", response="done", answered_turn_ids=("m1",))
    message = {
        "role": "assistant", "content": "done", "msg_id": message_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    event = {
        "event_id": event_id, "turn_id": "m1", "run_id": context.run_id,
        "answered_turn_ids": ["m1"], "response": "done",
    }

    staged = store.stage_terminal(
        context.run_id, result=result, assistant_payload=message,
        terminal_event=event)
    retry = store.stage_terminal(
        context.run_id, result=result, assistant_payload=message,
        terminal_event=event)

    assert staged["status"] == retry["status"] == "committing"
    assert store.complete(context.run_id) is False
    assert store.mark_message_committed(context.run_id)
    assert store.complete(context.run_id) is False
    assert store.mark_inbox_acknowledged(context.run_id)
    outbox = store.pending_outbox(context.run_id)
    assert len(outbox) == 1
    assert outbox[0]["event"]["event_id"] == event_id
    assert store.complete(context.run_id) is False
    assert store.record_outbox_attempt(event_id, delivered=True)
    assert store.complete(context.run_id)

    record = store.get_record(context.run_id)
    assert record.status == "completed"
    assert record.assistant_msg_id == message_id
    assert record.terminal_event_id == event_id
    assert record.answered_turn_ids == ("m1",)


def test_terminal_saga_commits_the_typed_failure_status(store):
    context = _create_running(store)
    message_id, event_id = new_terminal_identities(context.run_id)
    result = SimpleNamespace(
        status="failed",
        answered_turn_ids=("m1",),
        to_dict=lambda: {
            "status": "failed", "response": "boom",
            "artifacts": [], "metrics": {},
            "answered_turn_ids": ["m1"],
        },
    )
    store.stage_terminal(
        context.run_id,
        result=result,
        assistant_payload={
            "role": "assistant", "content": "boom", "msg_id": message_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        terminal_event={
            "event_id": event_id,
            "run_id": context.run_id,
            "status": "failed",
            "response": "boom",
            "answered_turn_ids": ["m1"],
        },
    )
    assert store.mark_message_committed(context.run_id)
    assert store.mark_inbox_acknowledged(context.run_id)
    assert store.record_outbox_attempt(event_id, delivered=True)

    assert store.complete(context.run_id)
    assert store.get_run(context.run_id)["status"] == "failed"
    assert store.complete(context.run_id)

    record = store.get_record(context.run_id)
    assert record.status == "failed"
    assert record.assistant_msg_id == message_id
    assert record.terminal_event_id == event_id
    assert record.answered_turn_ids == ("m1",)


def test_no_change_result_commits_completed_lifecycle_and_preserves_event(store):
    context = _create_running(store)
    message_id, event_id = new_terminal_identities(context.run_id)
    result = AgentWorkflowResult(
        status="no_change",
        response="No files were written.",
        answered_turn_ids=("m1",),
    )
    terminal = {
        "event_id": event_id,
        "run_id": context.run_id,
        "status": "no_change",
        "response": result.response,
        "answered_turn_ids": ["m1"],
    }

    staged = store.stage_terminal(
        context.run_id,
        result=result,
        assistant_payload={
            "role": "assistant",
            "content": result.response,
            "msg_id": message_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        terminal_event=terminal,
    )

    assert staged["terminal_status"] == "completed"
    assert staged["terminal_event"]["status"] == "no_change"
    assert store.mark_message_committed(context.run_id)
    assert store.mark_inbox_acknowledged(context.run_id)
    assert store.record_outbox_attempt(event_id, delivered=True)
    assert store.complete(context.run_id)
    completed = store.get_run(context.run_id)
    assert completed["status"] == "completed"
    assert completed["terminal_event"]["status"] == "no_change"


def test_stale_or_terminal_runs_cannot_commit_again(store):
    context = _create_running(store)
    assert store.supersede(context.run_id)
    assert not store.is_current_generation(context.run_id)
    with pytest.raises(RuntimeError, match="cannot stage"):
        store.stage_terminal(
            context.run_id,
            result=AgentWorkflowResult(
                status="completed", response="late",
                answered_turn_ids=("m1",)),
            assistant_payload={
                "msg_id": "workflow:late", "role": "assistant",
                "content": "late", "ts": "2026-08-24T00:00:00+00:00"},
            terminal_event={
                "event_id": str(uuid.uuid4()), "turn_id": "m1"})


def test_recoverable_runs_and_step_cache_survive_restart(store):
    context = _create_running(store)
    assert store.cache_step_result(
        context.run_id, "llm", "input-hash",
        {"response": "cached"}, {"tokens": 4})
    assert not store.cache_step_result(
        context.run_id, "llm", "input-hash",
        {"response": "charged twice"}, {"tokens": 99})

    reopened = WorkflowRunStore(store.database_path)
    rows = reopened.list_recoverable()
    assert [row["run_id"] for row in rows] == [context.run_id]
    assert reopened.reacquire(context.run_id, 60)
    assert reopened.get_run(context.run_id)["recovery_count"] == 1
    assert reopened.get_step_result(
        context.run_id, "llm", "input-hash") == {
            "result": {"response": "cached"}, "usage": {"tokens": 4}}
    first = reopened.append_event(
        context.run_id, "started", {"stage": "recovery"})
    second = reopened.append_event(
        context.run_id, "progress", {"stage": "work"})
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert [event["event_type"]
            for event in reopened.list_events(context.run_id)] == [
                "started", "progress"]


def test_retryable_failure_keeps_exact_checkpoint_and_retries_once(store):
    context = _create_running(store)
    checkpoint = FlowFile(content=b"before task", attributes={"stage": "ready"})
    error = WorkflowRunError(
        error_id=str(uuid.uuid4()),
        run_id=context.run_id,
        code="task_failed",
        message="provider temporarily unavailable",
        retryable=True,
        task_id="generate",
        details={"exception_type": "TemporaryProviderError"},
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    assert store.pause_retryable_failure(
        context.run_id,
        error=error,
        task_id="generate",
        flowfile=checkpoint,
        lease_seconds=60,
    )
    assert not store.pause_retryable_failure(
        context.run_id,
        error=error,
        task_id="generate",
        flowfile=checkpoint,
        lease_seconds=60,
    )

    reopened = WorkflowRunStore(store.database_path)
    paused = reopened.get_run(context.run_id)
    assert paused["status"] == "retryable_failed"
    assert paused["error"] == error.to_dict()
    assert paused["resume_task_id"] == "generate"
    assert paused["run_generation"] == context.run_generation

    from core.confirmation_store import ConfirmationStore
    restored = ConfirmationStore._restore_flowfile(paused["resume_flowfile_json"])
    assert restored.get_content() == b"before task"
    assert restored.get_attribute("stage") == "ready"
    assert reopened.retry_failure(context.run_id, lease_seconds=60)
    assert not reopened.retry_failure(context.run_id, lease_seconds=60)
    running = reopened.get_run(context.run_id)
    assert running["status"] == "running"
    assert running["run_generation"] == context.run_generation
    assert running["recovery_count"] == 1


def test_retryable_failure_projection_and_action_use_explicit_retry(
        store, monkeypatch):
    from core.workflow_run_inspector import inspect_workflow_run
    from tasks.ai.actions._agentres_k8 import _handle_agentres_k8

    context = _create_running(store)
    error = WorkflowRunError(
        error_id=str(uuid.uuid4()), run_id=context.run_id,
        code="task_failed", message="token=secret upstream unavailable",
        retryable=True, task_id="generate", details={},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert store.pause_retryable_failure(
        context.run_id, error=error, task_id="generate",
        flowfile=FlowFile(content=b"private"), lease_seconds=60)

    projection = inspect_workflow_run(context.run_id, store=store)
    assert projection["safe_retry"] is True
    assert projection["error"]["code"] == "task_failed"
    assert projection["error"]["task_id"] == "generate"
    assert "secret" not in str(projection)
    assert "private" not in str(projection)

    runtime = MagicMock()
    runtime.retry.return_value = {
        "status": "retrying", "run_id": context.run_id, "task_id": "generate"}
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: store))
    monkeypatch.setattr(
        "core.workflow_agent_runtime.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime))
    flowfile = FlowFile()
    _handle_agentres_k8(
        None, "retry_workflow_run",
        {"conversation_id": "c1", "run_id": context.run_id},
        None, "alice", flowfile)

    assert json.loads(flowfile.get_content())["recovery"]["status"] == "retrying"
    runtime.retry.assert_called_once_with(context.run_id)
    runtime.recover.assert_not_called()


def test_runtime_retry_restores_checkpoint_and_starts_one_worker(
        store, monkeypatch):
    from core.workflow_agent_runtime import WorkflowAgentRuntime, _ActiveRun

    context = _context()
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": context.flow_ref.name,
        "flow_scope": context.flow_ref.scope,
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": ["resource.read"],
        "parameters": {},
        "limits": context.limits.to_dict(),
        "flow_ref": context.flow_ref.to_dict(),
    })
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=300,
        binding=binding.to_dict())
    assert store.transition(context.run_id, "accepted", "running")
    error = WorkflowRunError(
        error_id=str(uuid.uuid4()), run_id=context.run_id,
        code="task_failed", message="temporary", retryable=True,
        task_id="generate", details={},
        created_at=datetime.now(timezone.utc).isoformat())
    assert store.pause_retryable_failure(
        context.run_id, error=error, task_id="generate",
        flowfile=FlowFile(content=b"resume me", attributes={"step": "7"}),
        lease_seconds=60)

    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: store))
    runtime = WorkflowAgentRuntime()
    paused = store.get_run(context.run_id)
    request = runtime._prepared_from_run(paused)
    key = runtime._key(request.conversation_id, request.agent_name)
    runtime._active[key] = _ActiveRun(
        request=request, run_id=context.run_id, binding=binding,
        status="retryable_failed")
    started = []
    monkeypatch.setattr(
        runtime, "_start_worker",
        lambda worker_key, active: started.append((worker_key, active)))

    first = runtime.retry(context.run_id)
    second = runtime.retry(context.run_id)

    assert first == {
        "status": "retrying", "run_id": context.run_id,
        "task_id": "generate"}
    assert second is None
    assert len(started) == 1
    active = started[0][1]
    assert active.run_id == context.run_id
    assert active.resume_task_id == "generate"
    assert active.resume_flowfile.get_content() == b"resume me"
    assert active.resume_flowfile.get_attribute("step") == "7"


def test_run_inspector_is_conversation_scoped_and_redacts_payloads(store):
    from core.workflow_run_inspector import (
        inspect_workflow_run, list_workflow_runs,
    )

    context = _create_running(store)
    store.append_event(context.run_id, "progress", {
        "task_id": "writer", "stage": "completed",
        "phase": "build", "iteration": 2,
        "tool_name": "write", "outcome": "completed",
        "service_id": "Writer", "source_body": "never expose this",
        "usage": {"tokens_in": 10, "provider_response": "hidden"},
    })
    store.append_event(context.run_id, "agent_message", {
        "task_id": "writer", "phase": "build", "iteration": 2,
        "role": "assistant", "content": "Inspecting the rendered page.",
        "model": "writer-model",
    })
    store.append_event(context.run_id, "tool_call", {
        "task_id": "writer", "phase": "build", "iteration": 2,
        "tool_call_id": "call-1", "tool_name": "write",
        "arguments": {"path": "index.html", "password": "do-not-expose"},
    })
    store.append_event(context.run_id, "tool_result", {
        "task_id": "writer", "phase": "build", "iteration": 2,
        "tool_call_id": "call-1", "tool_name": "write",
        "content": "token=do-not-expose write completed",
        "outcome": "completed",
    })
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_runs SET reason=? WHERE run_id=?",
            ("api_key=top-secret", context.run_id))

    listed = list_workflow_runs("c1", "demo", store=store)
    detail = inspect_workflow_run(context.run_id, store=store)

    assert len(listed) == 1 and listed[0]["safe_retry"] is True
    assert detail["events"][0]["data"] == {
        "task_id": "writer", "stage": "completed", "service_id": "Writer",
        "phase": "build", "iteration": 2,
        "tool_name": "write", "outcome": "completed",
        "usage": {"tokens_in": 10},
    }
    assert detail["events"][1]["data"]["content"] == (
        "Inspecting the rendered page.")
    assert detail["events"][1]["data"]["role"] == "assistant"
    assert detail["events"][2]["data"]["arguments"] == {
        "path": "index.html", "password": "<redacted>",
    }
    assert "do-not-expose" not in str(detail["events"][3])
    assert detail["events"][3]["data"]["outcome"] == "completed"
    assert "top-secret" not in str(detail)
    assert "never expose this" not in str(detail)
    assert "request" not in detail and "service_snapshot" not in detail
    assert list_workflow_runs("other", store=store) == []


def test_run_inspector_projects_structured_agent_messages_without_broken_json(store):
    from core.workflow_run_inspector import inspect_workflow_run

    context = _create_running(store)
    store.append_event(context.run_id, "agent_message", {
        "task_id": "writer", "role": "assistant",
        "content": json.dumps({
            "summary": "Readable summary",
            "mapping": [{"source": "A", "implementation": "B"}],
            "notes": "x" * 9000,
            "password": "do-not-expose",
        }),
    })

    event = inspect_workflow_run(context.run_id, store=store)["events"][0]

    assert "content" not in event["data"]
    assert event["data"]["structured_content"]["summary"] == "Readable summary"
    assert event["data"]["structured_content"]["mapping"] == [
        {"source": "A", "implementation": "B"},
    ]
    assert event["data"]["structured_content"]["password"] == "<redacted>"
    assert "do-not-expose" not in str(event)


def test_run_inspector_projects_native_structured_agent_messages(store):
    from core.workflow_run_inspector import inspect_workflow_run

    context = _create_running(store)
    store.append_event(context.run_id, "agent_message", {
        "task_id": "writer", "role": "assistant",
        "structured_content": {
            "summary": "Readable summary",
            "password": "do-not-expose",
        },
    })

    event = inspect_workflow_run(context.run_id, store=store)["events"][0]

    assert event["data"]["structured_content"] == {
        "summary": "Readable summary",
        "password": "<redacted>",
    }
    assert "do-not-expose" not in str(event)


def test_run_inspector_never_projects_incomplete_json_as_raw_content(store):
    from core.workflow_run_inspector import inspect_workflow_run

    context = _create_running(store)
    broken = '{"summary":"Readable","mapping":[{"source":"cut'
    store.append_event(context.run_id, "agent_message", {
        "task_id": "writer", "role": "assistant", "content": broken,
    })

    event = inspect_workflow_run(context.run_id, store=store)["events"][0]

    assert broken not in str(event)
    assert event["data"]["content"] == "Structured response incomplete."


def test_run_inspector_projects_flow_blocks_and_disables_live_retry(
        store, monkeypatch):
    from core.workflow_agent_resources import ResolvedAgentWorkflow
    from core.workflow_run_inspector import inspect_workflow_run

    context = _create_running(store)
    store.append_event(context.run_id, "progress", {
        "task_id": "input", "stage": "task_completed",
    })
    store.append_event(context.run_id, "progress", {
        "task_id": "work", "stage": "task_started",
    })
    resolved = ResolvedAgentWorkflow(definition={
        "tasks": {
            "input": {
                "type": "inputPort", "label": "Receive request",
                "description": "Accept the request.", "parameters": {"secret": "x"},
            },
            "work": {
                "type": "websiteCreatorTool", "label": "Build website",
                "description": "Create the approved project.", "parameters": {},
            },
        },
        "relations": [{"from": "input", "to": "work", "type": "success"}],
        "default_layout_id": "functional",
        "layouts": {"functional": {
            "direction": "LR",
            "nodes": {
                "input": {"x": 10, "y": 20, "width": 180, "height": 72},
                "work": {"x": 260, "y": 20, "width": 180, "height": 72},
            },
        }},
    }, ref=context.flow_ref)
    monkeypatch.setattr(
        "core.workflow_agent_resources.resolve_exact_agent_workflow",
        lambda *_args, **_kwargs: resolved)

    detail = inspect_workflow_run(
        context.run_id, store=store, live_run_ids={context.run_id})

    assert detail["safe_retry"] is False
    assert detail["flow_graph"] == {
        "tasks": [
            {
                "id": "input", "type": "inputPort", "label": "Receive request",
                "description": "Accept the request.",
                "status": "completed", "x": 10.0, "y": 20.0,
                "width": 180.0, "height": 72.0,
            },
            {
                "id": "work", "type": "websiteCreatorTool",
                "label": "Build website",
                "description": "Create the approved project.",
                "status": "running", "x": 260.0, "y": 20.0,
                "width": 180.0, "height": 72.0,
            },
        ],
        "relations": [{"from": "input", "to": "work", "type": "success"}],
        "direction": "LR",
    }
    assert "secret" not in str(detail)


def test_run_inspector_uses_authorized_flow_task_when_progress_uses_runtime_class(
        store, monkeypatch):
    from core.workflow_agent_resources import ResolvedAgentWorkflow
    from core.workflow_run_inspector import inspect_workflow_run

    context = _create_running(store)
    store.append_event(context.run_id, "authorization", {
        "task_id": "prepare_request", "decision": "execute",
    })
    store.append_event(context.run_id, "authorization", {
        "task_id": "explore_sites", "decision": "execute",
    })
    store.append_event(context.run_id, "progress", {
        "task_id": "WebsiteCreatorToolTask", "stage": "llm_attempt",
        "label": "Explore source and template: model attempt 1",
    })
    resolved = ResolvedAgentWorkflow(definition={
        "tasks": {
            "prepare_request": {"type": "prepareWebsiteRequest"},
            "explore_sites": {"type": "websiteCreatorTool"},
        },
        "relations": [{
            "from": "prepare_request", "to": "explore_sites", "type": "success",
        }],
    }, ref=context.flow_ref)
    monkeypatch.setattr(
        "core.workflow_agent_resources.resolve_exact_agent_workflow",
        lambda *_args, **_kwargs: resolved)

    detail = inspect_workflow_run(context.run_id, store=store)

    assert [task["status"] for task in detail["flow_graph"]["tasks"]] == [
        "completed", "running"]


def test_workflow_run_snapshot_returns_list_and_selected_detail_once(monkeypatch):
    from tasks.ai.actions._agentres_k8 import _handle_agentres_k8

    runtime = MagicMock()
    runtime.live_run_ids.return_value = {"wr-1"}
    monkeypatch.setattr(
        "core.workflow_agent_runtime.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime))
    listed = MagicMock(return_value=[{"run_id": "wr-1"}])
    inspected = MagicMock(return_value={"run_id": "wr-1", "flow_graph": {}})
    monkeypatch.setattr(
        "core.workflow_run_inspector.list_workflow_runs", listed)
    monkeypatch.setattr(
        "core.workflow_run_inspector.inspect_workflow_run", inspected)
    run_store = MagicMock()
    run_store.get_run.return_value = {"conversation_id": "c1"}
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: run_store))

    flowfile = FlowFile()
    response = _handle_agentres_k8(
        None, "workflow_run_snapshot", {
            "conversation_id": "c1", "agent_name": "Demo",
            "run_id": "wr-1", "limit": 25,
        }, None, "alice", flowfile)

    assert response == [flowfile]
    assert json.loads(flowfile.get_content()) == {
        "runs": [{"run_id": "wr-1"}],
        "run": {"run_id": "wr-1", "flow_graph": {}},
    }
    listed.assert_called_once_with(
        "c1", "Demo", 25, store=run_store, live_run_ids={"wr-1"})
    inspected.assert_called_once_with(
        "wr-1", store=run_store, live_run_ids={"wr-1"})


def test_workflow_operations_reports_scoped_metrics_and_alerts(
        store, tmp_path):
    from core.agent_inbox_store import AgentInboxStore
    from core.workflow_run_inspector import workflow_operational_summary

    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    failed = _context("wr_failed", generation=1)
    store.create_run(
        context=failed, request=_request(), parameters={}, lease_seconds=300)
    assert store.fail(failed.run_id, "provider token=secret")
    overdue = _context("wr_overdue", generation=2)
    store.create_run(
        context=overdue, request=_request(), parameters={}, lease_seconds=300)
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_runs SET deadline_at=?, recovery_count=3 "
            "WHERE run_id=?",
            ("2026-08-23T00:00:00+00:00", overdue.run_id))
    inbox.enqueue("c1", "demo", {
        "role": "user", "content": "private", "msg_id": "m-pending",
        "ts": "2026-08-24T00:00:00+00:00",
    }, now=1)

    summary = workflow_operational_summary(
        "c1", "demo", store=store, inbox=inbox,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp(),
        backlog_alert=1)

    assert summary["health"] == "critical"
    assert summary["runs"]["status"] == {"accepted": 1, "failed": 1}
    assert summary["runs"]["recoveries"] == 3
    assert summary["inbox"]["pending"] == 1
    assert {row["code"] for row in summary["alerts"]} == {
        "workflow_failed_runs", "workflow_overdue_runs",
        "workflow_recovery_churn", "workflow_inbox_backlog",
    }
    assert "private" not in str(summary)
    assert "secret" not in str(summary)


def test_workflow_without_explicit_deadline_is_not_overdue():
    from core.workflow_run_inspector import workflow_operational_summary

    store = MagicMock()
    store.list_runs.return_value = [{
        "run_id": "wr_unlimited",
        "agent_name": "Demo",
        "status": "running",
        "invocation_mode": "conversation",
        "deadline_at": None,
        "recovery_count": 0,
        "usage": {},
    }]
    inbox = MagicMock()
    inbox.list_items.return_value = []

    summary = workflow_operational_summary(
        "c1", "Demo", store=store, inbox=inbox,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())

    assert summary["health"] == "ok"
    assert summary["alerts"] == []


def test_run_inspector_actions_reject_cross_conversation_and_lost_retry_race(
        store, monkeypatch):
    from tasks.ai.actions._agentres_k8 import _handle_agentres_k8

    context = _create_running(store)
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: store))
    flowfile = FlowFile()
    response = _handle_agentres_k8(
        None, "inspect_workflow_run",
        {"conversation_id": "other", "run_id": context.run_id},
        None, "alice", flowfile)
    assert response == [flowfile]
    assert flowfile.get_attribute("http.response.status") == "404"

    runtime = MagicMock()
    runtime.recover.return_value = None
    monkeypatch.setattr(
        "core.workflow_agent_runtime.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime))
    flowfile = FlowFile()
    response = _handle_agentres_k8(
        None, "retry_workflow_run",
        {"conversation_id": "c1", "run_id": context.run_id},
        None, "alice", flowfile)
    assert response == [flowfile]
    assert flowfile.get_attribute("http.response.status") == "409"
    assert "could not be acquired" in json.loads(flowfile.get_content())["error"]


def test_workflow_operations_action_is_conversation_scoped(monkeypatch):
    from tasks.ai.actions._agentres_k8 import _handle_agentres_k8

    projection = MagicMock(return_value={"health": "ok", "alerts": []})
    monkeypatch.setattr(
        "core.workflow_run_inspector.workflow_operational_summary", projection)
    missing = FlowFile()
    _handle_agentres_k8(
        None, "workflow_operations", {}, None, "alice", missing)
    assert missing.get_attribute("http.response.status") == "400"

    flowfile = FlowFile()
    _handle_agentres_k8(
        None, "workflow_operations", {
            "conversation_id": "c1", "agent_name": "Demo",
            "backlog_alert": 25,
        }, None, "alice", flowfile)

    assert json.loads(flowfile.get_content()) == {
        "operations": {"health": "ok", "alerts": []}}
    projection.assert_called_once_with(
        "c1", "Demo", backlog_alert=25)


def test_llm_step_cache_aggregates_once_and_reserves_call_budget(store):
    context = _create_running(store)
    assert store.begin_llm_step(context.run_id, "writer", "hash-1") is None
    committed = store.commit_llm_step(
        context.run_id, "writer", "hash-1", {"content": "done"}, {
            "llm_calls": 1, "tokens_in": 10, "tokens_out": 4,
            "cache_read": 2, "cache_write": 0, "duration_ms": 8,
            "cost_usd": 0.01, "virtual_cost_usd": 0.0,
        })
    cached = store.begin_llm_step(context.run_id, "writer", "hash-1")

    assert committed["inserted"] is True
    assert cached["result"] == {"content": "done"}
    assert store.get_run(context.run_id)["usage"] == committed["run_usage"]
    assert committed["run_usage"]["llm_calls"] == 1
    with pytest.raises(WorkflowBudgetExceeded, match="call budget"):
        store.begin_llm_step(context.run_id, "reviewer", "hash-2")


def test_delete_conversation_cascades_run_state(store):
    _create_running(store)
    assert store.delete_conversation("c1") == 1
    assert store.get_run("wr_test") is None
    assert store.list_recoverable() == ()


def test_terminal_retention_never_prunes_live_runs(store):
    context = _create_running(store)
    assert store.prune_terminal(0, now=10_000_000_000) == 0
    assert store.get_run(context.run_id)["status"] == "running"
    assert store.force_stop(context.run_id)
    assert store.prune_terminal(0, now=10_000_000_000) == 1


def test_delete_terminal_run_is_scoped_and_never_deletes_live_run(store):
    context = _create_running(store)

    assert store.delete_terminal(context.run_id, "c1") is False
    assert store.get_run(context.run_id)["status"] == "running"
    assert store.force_stop(context.run_id)
    assert store.delete_terminal(context.run_id, "other") is False
    assert store.get_run(context.run_id)["status"] == "force_stopped"
    assert store.delete_terminal(context.run_id, "c1") is True
    assert store.get_run(context.run_id) is None


def test_delete_workflow_run_action_is_conversation_scoped(store, monkeypatch):
    from tasks.ai.actions._agentres_k8 import _handle_agentres_k8

    context = _create_running(store)
    assert store.force_stop(context.run_id)
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: store))

    cross = FlowFile()
    response = _handle_agentres_k8(
        None, "delete_workflow_run",
        {"conversation_id": "other", "run_id": context.run_id},
        None, "alice", cross)
    assert response == [cross]
    assert cross.get_attribute("http.response.status") == "404"
    assert store.get_run(context.run_id) is not None

    scoped = FlowFile()
    response = _handle_agentres_k8(
        None, "delete_workflow_run",
        {"conversation_id": "c1", "run_id": context.run_id},
        None, "alice", scoped)
    assert response == [scoped]
    assert json.loads(scoped.get_content()) == {"ok": True}
    assert store.get_run(context.run_id) is None


def test_terminal_recovery_replays_each_saga_boundary_once(
        store, monkeypatch):
    context = _create_running(store)
    result = AgentWorkflowResult(
        status="completed", response="durable",
        answered_turn_ids=("m1",))
    persisted = {}
    delivered = []

    class Writer:
        fail = True

        def enqueue_message_if_absent(self, message, **_kwargs):
            if self.fail:
                raise OSError("transcript unavailable")
            inserted = message["msg_id"] not in persisted
            persisted[message["msg_id"]] = dict(message)
            return inserted

    writer = Writer()

    class Inbox:
        fail = True
        acknowledgements = 0

        def acknowledge(self, *_args):
            if self.fail:
                raise OSError("inbox unavailable")
            self.acknowledgements += 1
            return 1

        def release(self, *_args):
            return 0

    inbox = Inbox()

    class Bus:
        fail = True

        def publish_event(self, _conversation_id, _event_type, event):
            if self.fail:
                raise OSError("event bus unavailable")
            delivered.append(dict(event))

    bus = Bus()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(lambda cls, _cid: writer))
    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        classmethod(lambda cls: bus))
    coordinator = WorkflowTurnCoordinator(store, inbox)

    with pytest.raises(OSError, match="transcript"):
        coordinator.finalize(context, result)
    assert store.get_run(context.run_id)["status"] == "committing"
    assert persisted == {}
    assert coordinator.recover_committing() == {
        "completed": 0, "pending": 1}
    assert store.get_run(context.run_id)["status"] == "committing"

    writer.fail = False
    with pytest.raises(OSError, match="inbox"):
        coordinator.commit(context.run_id)
    assert len(persisted) == 1
    assert store.get_run(context.run_id)["message_committed"] is True

    inbox.fail = False
    assert coordinator.commit(context.run_id) is None
    committing = store.get_run(context.run_id)
    assert committing["inbox_acknowledged"] is True
    assert store.pending_outbox(context.run_id)[0]["attempts"] == 1

    bus.fail = False
    terminal = coordinator.commit(context.run_id)
    assert terminal["response"] == "durable"
    assert store.get_run(context.run_id)["status"] == "completed"
    assert len(persisted) == 1
    assert inbox.acknowledgements == 1
    assert len(delivered) == 1


def test_delegate_terminal_is_private_and_wakes_the_calling_agent(
        store, monkeypatch):
    context = _context()
    source = {
        "type": "agent_delegate", "from": "Planner", "to": "Demo",
        "task_id": "delegate-1",
    }
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=300,
        ingress_source=source)
    assert store.transition(context.run_id, "accepted", "running")
    persisted = []
    events = []

    class Writer:
        def enqueue_message_if_absent(self, message, **_kwargs):
            persisted.append(dict(message))
            return True

    class Inbox:
        def acknowledge(self, *_args):
            return 1

        def release(self, *_args):
            return 0

    class Bus:
        def publish_event(self, conversation_id, event_type, data):
            events.append((conversation_id, event_type, dict(data)))

    runtime = MagicMock()
    runtime._active_contexts = {}
    runtime._active_contexts_lock = MagicMock()
    wake = MagicMock()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(lambda cls, _cid: Writer()))
    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        classmethod(lambda cls: Bus()))
    monkeypatch.setattr(
        "tasks.ai.agent_loop.AgentLoopTask._live_instance", runtime)
    monkeypatch.setattr(
        "core.handlers.resource_agent.SpawnAgentsHandler._wake_caller", wake)

    result = AgentWorkflowResult(
        status="completed", response="media ready",
        answered_turn_ids=("m1",))
    coordinator = WorkflowTurnCoordinator(store, Inbox())
    terminal = coordinator.finalize(context, result)

    assert terminal["status"] == "completed"
    assert len(persisted) == 1
    assert persisted[0]["source"] == {
        "type": "agent_delegate", "from": "Demo", "to": "Planner",
        "kind": "reply", "task_id": "delegate-1",
    }
    wake.assert_called_once()
    wake_args = wake.call_args.args
    assert wake_args[1:5] == ("c1", "Planner", "alice", "media ready")
    assert wake.call_args.kwargs["source"] == persisted[0]["source"]
    assert [event_type for _, event_type, _ in events] == ["done"]
    assert coordinator.finalize(context, result) == terminal
    assert len(persisted) == 1
    assert wake.call_count == 1


def test_stale_committer_never_releases_successor_state(store):
    old = _create_running(store, run_id="wr_old", generation=1)
    result = AgentWorkflowResult(
        status="completed", response="old", answered_turn_ids=("m1",))
    message_id, event_id = new_terminal_identities(old.run_id)
    store.stage_terminal(
        old.run_id, result=result,
        assistant_payload={
            "role": "assistant", "content": "old", "msg_id": message_id,
            "ts": "2026-08-24T00:00:00+00:00"},
        terminal_event={
            "event_id": event_id, "turn_id": "m1", "response": "old"})
    assert store.supersede(old.run_id, "new generation")

    successor = _context("wr_new", generation=2)
    store.create_run(
        context=successor, request=_request(), parameters={},
        lease_seconds=300)
    assert store.transition("wr_new", "accepted", "running")

    class Inbox:
        def __init__(self):
            self.releases = []

        def release(self, *args):
            self.releases.append(args)

    inbox = Inbox()
    with pytest.raises(RuntimeError, match="requires committing"):
        WorkflowTurnCoordinator(store, inbox).commit(old.run_id)

    assert store.is_current_generation("wr_new")
    assert store.get_run("wr_new")["status"] == "running"
    assert inbox.releases == []


def test_silent_maintenance_commits_without_transcript_or_done(store, monkeypatch):
    context = _create_running(store, invocation_mode="silent_maintenance")
    result = AgentWorkflowResult(
        status="completed", response="maintenance complete",
        answered_turn_ids=("m1",))
    calls = []

    class Inbox:
        def acknowledge(self, *_args):
            calls.append("acknowledge")

        def release(self, *_args):
            calls.append("release")

    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(lambda cls, _cid: pytest.fail("silent run wrote transcript")))
    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        classmethod(lambda cls: pytest.fail("silent run published done")))

    terminal = WorkflowTurnCoordinator(store, Inbox()).finalize(context, result)

    assert terminal["response"] == "maintenance complete"
    run = store.get_run(context.run_id)
    assert run["status"] == "completed"
    assert run["message_committed"] is True
    assert run["inbox_acknowledged"] is True
    assert calls == ["acknowledge", "release"]
    assert store.pending_outbox(context.run_id) == ()


@pytest.mark.parametrize("channel", ("web", "slack"))
def test_terminal_commit_preserves_http_and_non_http_transport_correlation(
        store, monkeypatch, channel):
    from core.agent_runtime_api import AgentResultWaiter
    from core.conversation_event_bus import ConversationEventBus

    context = _create_running(store, channel=channel)
    result = AgentWorkflowResult(
        status="completed", response=f"{channel} complete",
        answered_turn_ids=("m1",))
    persisted = []

    class Writer:
        def enqueue_message_if_absent(self, message, **_kwargs):
            persisted.append(dict(message))
            return True

    class Inbox:
        def acknowledge(self, *_args):
            return 1

        def release(self, *_args):
            return 0

    ConversationEventBus.reset()
    AgentResultWaiter._instance = None
    waiter = AgentResultWaiter.instance()
    waiter.register("c1", "m1")
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(lambda cls, _cid: Writer()))

    terminal = WorkflowTurnCoordinator(store, Inbox()).finalize(context, result)
    waited = waiter.wait("c1", "m1", timeout=0.1)

    assert terminal["channel"] == channel
    assert waited is not None
    assert waited.response == f"{channel} complete"
    assert len(persisted) == 1
    assert persisted[0]["channel"] == channel
