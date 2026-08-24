"""Golden runs for the tool-free bounded agent-group workflow."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import FlowFile
from core._service_defs import ServiceDef
from core.agent_contracts import AuthorizationRefContract
from core.agent_group_contracts import AgentGroupDefinition
from core.agent_group_tools import (
    GroupReadOnlyToolRuntime,
    group_read_only_tool_names,
)
from core.agent_turn_identity import AgentTurnIdentity
from core.llm_client import LLMMessage, LLMResponse, LLMToolCall, LLMToolDefinition
from core.resource_identity import ResourceRef
from core.service_definition_revision import compute_service_definition_revision
from core.tool_registry import ToolRegistry
from core.usage_ledger import UsageLedger
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    WorkflowConversationRef,
    WorkflowLimits,
    WorkflowRequestBody,
    WorkflowRunContext,
    WorkflowTurnRef,
)
from core.workflow_run_inspector import _event_projection
from core.workflow_run_store import WorkflowBudgetExceeded, WorkflowRunStore
from tasks.ai.workflow.group_tasks import (
    AgentParticipantCallTask,
    _GroupLLMCaller,
)

_FLOW_PATH = (
    Path(__file__).parents[1]
    / "data/repository/flows/global/pawflow/agents/group-deliberation"
    / "versions/1.0.0.json"
)


class FakeClient:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []
        self.aborted = threading.Event()

    def reset_abort(self):
        self.aborted.clear()

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.response_factory(len(self.calls), kwargs)

    def abort(self):
        self.aborted.set()


class BlockingClient(FakeClient):
    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.aborted.wait(2):
            raise AssertionError("group provider request was not aborted")
        raise RuntimeError("group provider aborted")


class FakeService:
    provider = "test"

    def __init__(self, client):
        self.client = client
        self.config = {
            "provider": "test",
            "cost_per_1m_input": "0",
            "cost_per_1m_output": "0",
        }

    def get_client(self):
        return self.client


def _response(content: dict, *, tokens: int = 10) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(content),
        model="fake-group-model",
        tokens_in=tokens,
        tokens_out=0,
        duration_ms=1,
        finish_reason="stop",
    )


def _group_definition(
    *, max_rounds=2, max_tokens=200, synthesis=None, tool_policy=None,
):
    members = []
    for index, member_id in enumerate(("architecture", "operations")):
        ref = ResourceRef(
            resource_type="agent",
            name=member_id,
            scope="global",
            content_digest=(str(index + 1) * 64),
            source_id=f"test:{member_id}",
        )
        members.append({
            "member_id": member_id,
            "member_kind": "conversation_instance",
            "agent_ref": ref.to_dict(),
            "instance_name": member_id.title(),
            "display_name": member_id.title(),
            "role": f"{member_id} review",
            "required": True,
        })
    return {
        "schema_version": 1,
        "name": "review-board",
        "description": "Deterministic test review board.",
        "members": members,
        "selection": {"mode": "all"},
        "deliberation": {
            "max_rounds": max_rounds,
            "max_messages_per_member_per_round": 1,
            "max_total_participant_calls": 4,
            "max_parallelism": 2,
            "allow_pass": True,
            "rotate_first_speaker": True,
        },
        "context_policy": {
            "private_context": "none",
            "shared_history_limit": 24,
            "attachments": "explicit_only",
        },
        "tool_policy": tool_policy or {"mode": "none", "allowed_effects": []},
        "synthesis": synthesis or {"mode": "deterministic_concat"},
        "budgets": {"max_tokens": max_tokens, "timeout_seconds": 120},
        "output": {"include_attributions": True, "include_dissent": True},
    }


@pytest.fixture
def group_environment(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs.sqlite3")
    ledger = UsageLedger(path=str(tmp_path / "usage.sqlite3"))
    clients = {
        "architect-llm": FakeClient(
            lambda call, _kwargs: _response({
                "disposition": "post",
                "content": f"architecture finding {call}",
                "citations": [],
                "confidence": 0.9,
            })
        ),
        "operator-llm": FakeClient(
            lambda call, _kwargs: _response({
                "disposition": "post",
                "content": f"operations finding {call}",
                "citations": [],
                "confidence": 0.8,
            })
        ),
    }
    services = {name: FakeService(client) for name, client in clients.items()}
    definitions = {
        name: ServiceDef(
            service_id=name,
            service_type="llmConnection",
            scope="user",
            scope_id="alice",
            config=service.config,
            created_at=1,
        )
        for name, service in services.items()
    }

    class Registry:
        @staticmethod
        def get_definition(scope, scope_id, service_id):
            if (scope, scope_id) == ("user", "alice"):
                return definitions.get(service_id)
            return None

        @staticmethod
        def get_live_instance(scope, scope_id, service_id):
            if (scope, scope_id) == ("user", "alice"):
                return services.get(service_id)
            return None

    monkeypatch.setenv("PAWFLOW_AGENT_GROUPS_ENABLED", "true")
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        classmethod(lambda cls: Registry()),
    )
    monkeypatch.setattr(
        "core.usage_ledger.UsageLedger.instance",
        classmethod(lambda cls: ledger),
    )
    return store, ledger, clients, services, definitions


def _snapshot(definitions, group=None):
    group = group or _group_definition()
    members = []
    services = {}
    for index, member_id in enumerate(("architecture", "operations")):
        service_id = "architect-llm" if member_id == "architecture" else "operator-llm"
        service = definitions[service_id]
        service_snapshot = {
            "service_id": service_id,
            "service_type": "llmConnection",
            "scope": "user",
            "scope_id": "alice",
            "definition_revision": compute_service_definition_revision(service),
        }
        services[service_id] = service_snapshot
        members.append({
            "member_id": member_id,
            "instance_name": member_id.title(),
            "agent_ref": group["members"][index]["agent_ref"],
            "agent_definition": {
                "name": member_id,
                "prompt": f"Public {member_id} instructions.",
            },
            "params": {"public_focus": member_id},
            "model": "",
            "tools": [],
            "service": service_snapshot,
            "snapshot_digest": (chr(ord("a") + index) * 64),
            "private_transcript": "PRIVATE_TRANSCRIPT_SENTINEL",
            "memory": "PRIVATE_MEMORY_SENTINEL",
        })
    return {
        "bindings": {},
        "services": services,
        "agent_group": {
            "schema_version": 1,
            "group_ref": {
                "schema_version": 1,
                "resource_type": "agent_group",
                "name": "review-board",
                "scope": "user",
                "owner_id": "alice",
                "content_digest": "f" * 64,
                "source_id": "test:review-board",
            },
            "definition": group,
            "member_snapshots": members,
            "group_services": {},
            "run_snapshot_digest": "e" * 64,
        },
    }


def _request():
    return AgentWorkflowRequest(
        request=WorkflowRequestBody(
            message="Review the public proposal.",
            attachments=({"name": "public.txt"},),
        ),
        conversation=WorkflowConversationRef(id="conv-1", agent="Review Board"),
        turn=WorkflowTurnRef(
            root_turn_id="web:group-turn-1",
            request_message_ids=("web:group-turn-1",),
        ),
        parameters={"group_name": "review-board"},
    )


def _context(run_id, snapshot, *, max_llm_calls=6):
    auth = AuthorizationRefContract(
        context_id=str(uuid.uuid4()),
        revision=1,
        root_turn_id="web:group-turn-1",
    )
    identity = AgentTurnIdentity(
        conversation_id="conv-1",
        root_conversation_id="conv-1",
        agent_instance="Review Board",
        turn_id="web:group-turn-1",
        ingress_msg_id="web:group-turn-1",
        turn_epoch=1,
        run_generation=1,
        authorization_context_id=auth.context_id,
        authorization_revision_at_start=1,
        source_kind="user",
        created_at="2026-08-24T12:00:00+00:00",
    )
    return WorkflowRunContext(
        run_id=run_id,
        turn_identity=identity,
        conversation_id="conv-1",
        agent_name="Review Board",
        user_id="alice",
        root_turn_id="web:group-turn-1",
        run_generation=1,
        flow_ref=ResourceRef(
            resource_type="flow",
            name="pawflow.agents.group-deliberation:1.0.0",
            scope="global",
            version="1.0.0",
            content_digest="d" * 64,
            source_id="test:group-flow",
        ),
        channel="web",
        invocation_mode="conversation",
        permission_mode="default",
        authorization_ref=auth,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        limits=WorkflowLimits(
            max_duration_seconds=120,
            max_llm_calls=max_llm_calls,
            max_flowfiles=32,
            max_fanout=2,
        ),
        service_snapshot=snapshot,
        cancel_token="cancel:group",
        event_sink="test",
    )


def _running(store, context):
    request = _request()
    store.create_run(
        context=context,
        request=request,
        parameters=request.parameters,
        lease_seconds=180,
    )
    assert store.transition(context.run_id, "accepted", "running")
    return request


def _run_flow(context, store, request, *, cancel_event=None):
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser
    from tasks import register_all_tasks

    register_all_tasks()
    definition = json.loads(_FLOW_PATH.read_text(encoding="utf-8"))
    events = []
    terminals = []
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse(definition),
        input_flowfiles=[FlowFile(content=json.dumps(request.to_dict()).encode())],
        entry_task_id="group_request",
        suppress_one_shot_roots=True,
        max_retries=1,
        timeout=10,
        runtime_context={
            "workflow_run_context": context,
            "workflow_allowed_effects": ["resource.read"],
            "workflow_run_store": store,
            "workflow_cancel_event": cancel_event or threading.Event(),
            "workflow_event_callback": lambda kind, data: events.append((kind, data)),
            "workflow_terminal_callback": terminals.append,
        },
    )
    return execution, events, terminals


def test_fake_provider_golden_run_is_deterministic_cached_and_single_terminal(
    group_environment,
):
    store, ledger, clients, _services, definitions = group_environment
    snapshot = _snapshot(definitions)
    context = _context("wr_group_golden", snapshot)
    request = _running(store, context)

    first, first_events, first_terminals = _run_flow(context, store, request)
    second, second_events, second_terminals = _run_flow(context, store, request)

    assert first.success and second.success
    assert len(first_terminals) == len(second_terminals) == 1
    assert first_terminals[0].to_dict() == second_terminals[0].to_dict()
    assert first_terminals[0].metrics == {
        "group_rounds": 2,
        "group_participant_calls": 4,
        "group_llm_calls": 4,
        "group_tool_calls": 0,
        "group_tokens": 40,
        "group_posts": 4,
        "group_passes": 0,
    }
    assert [len(client.calls) for client in clients.values()] == [2, 2]
    assert store.get_run(context.run_id)["usage"]["llm_calls"] == 4
    assert ledger.summary(run_id=context.run_id)["calls"] == 4
    assert [kind for kind, _data in first_events].count("group_participant_post") == 4
    assert [kind for kind, _data in second_events].count("group_participant_post") == 4
    first_posts = [
        (data["round"], data["member_id"], data["content"])
        for kind, data in first_events if kind == "group_participant_post"
    ]
    second_posts = [
        (data["round"], data["member_id"], data["content"])
        for kind, data in second_events if kind == "group_participant_post"
    ]
    assert first_posts == second_posts
    assert first_posts[:2] != first_posts[2:]
    provider_payload = json.dumps([
        {
            "role": message.role,
            "content": message.content,
        }
        for client in clients.values()
        for call in client.calls
        for message in call["messages"]
    ])
    assert "Review the public proposal." in provider_payload
    assert "PRIVATE_TRANSCRIPT_SENTINEL" not in provider_payload
    assert "PRIVATE_MEMORY_SENTINEL" not in provider_payload


def test_all_pass_stops_after_one_round_and_synthesizes_no_finding(
    group_environment,
):
    store, _ledger, clients, _services, definitions = group_environment
    for client in clients.values():
        client.response_factory = lambda _call, _kwargs: _response({
            "disposition": "pass",
            "content": "",
            "citations": [],
        })
    context = _context("wr_group_pass", _snapshot(
        definitions, _group_definition(max_rounds=5)
    ))
    request = _running(store, context)

    execution, events, terminals = _run_flow(context, store, request)

    assert execution.success
    assert len(terminals) == 1
    assert terminals[0].response == "The group reached no additional finding."
    assert terminals[0].metrics["group_rounds"] == 1
    assert terminals[0].metrics["group_passes"] == 2
    assert [len(client.calls) for client in clients.values()] == [1, 1]
    completed = next(
        data for kind, data in events if kind == "group_rounds_completed"
    )
    assert completed["stop_reason"] == "all_passed"


def test_group_budget_rejects_provider_usage_before_commit(group_environment):
    store, ledger, clients, _services, definitions = group_environment
    client = clients["architect-llm"]
    client.response_factory = lambda _call, _kwargs: _response({
        "disposition": "post",
        "content": "too expensive",
        "citations": [],
    }, tokens=101)
    snapshot = _snapshot(definitions)
    context = _context("wr_group_budget", snapshot)
    _running(store, context)
    task = AgentParticipantCallTask({})
    task.get_task_id = lambda: "participant_calls"
    task.set_workflow_run_context(
        context,
        run_store=store,
        cancel_event=threading.Event(),
    )
    caller = _GroupLLMCaller(task)
    messages = [
        type("Message", (), {
            "role": "user",
            "content": "bounded request",
        })()
    ]

    for _attempt in range(2):
        with pytest.raises(WorkflowBudgetExceeded, match="token allocation"):
            caller.call_json(
                "budget-proof",
                "architect-llm",
                messages,
                100,
                token_budget=100,
            )

    assert len(client.calls) == 2
    assert store.get_run(context.run_id)["usage"] == {}
    assert ledger.summary(run_id=context.run_id)["calls"] == 0


def test_participant_budget_failure_is_terminal_and_cancels_the_group(
    group_environment,
):
    store, ledger, clients, _services, definitions = group_environment
    clients["architect-llm"].response_factory = lambda _call, _kwargs: _response({
        "disposition": "post",
        "content": "over allocation",
        "citations": [],
    }, tokens=101)
    group = _group_definition(max_rounds=1, max_tokens=200)
    snapshot = _snapshot(definitions, group)
    context = _context("wr_group_budget_terminal", snapshot)
    _running(store, context)
    cancel = threading.Event()
    task = AgentParticipantCallTask({})
    task.get_task_id = lambda: "participant_calls"
    task.set_workflow_run_context(
        context,
        run_store=store,
        cancel_event=cancel,
    )
    state = {
        "request": _request().to_dict(),
        "group": snapshot["agent_group"],
        "selected_member_ids": ["architecture", "operations"],
        "shared_room": {
            "request": "Review the public proposal.",
            "attachments": [],
            "policy": {
                "private_context": "none",
                "attachments": "explicit_only",
                "tool_mode": "none",
            },
            "posts": [],
        },
        "budget": {
            "participant_calls": 0,
            "llm_calls": 0,
            "tokens": 0,
            "cost": 0.0,
            "rounds": 0,
        },
    }

    with pytest.raises(WorkflowBudgetExceeded, match="token allocation"):
        task.execute(FlowFile(content=json.dumps(state).encode()))

    run = store.get_run(context.run_id)
    assert run["status"] == "budget_exceeded"
    assert run["usage"]["tokens_in"] == 10
    assert run["usage"]["llm_calls"] == 1
    assert ledger.summary(run_id=context.run_id)["calls"] == 1
    assert cancel.is_set()


def test_cancellation_aborts_all_active_group_participants(group_environment):
    store, _ledger, clients, services, definitions = group_environment
    for name, client in list(clients.items()):
        blocking = BlockingClient(client.response_factory)
        clients[name] = blocking
        services[name].client = blocking
    group = _group_definition(max_rounds=1)
    snapshot = _snapshot(definitions, group)
    context = _context("wr_group_cancel", snapshot)
    _running(store, context)
    cancel = threading.Event()
    task = AgentParticipantCallTask({})
    task.get_task_id = lambda: "participant_calls"
    task.set_workflow_run_context(
        context,
        run_store=store,
        cancel_event=cancel,
    )
    state = {
        "request": _request().to_dict(),
        "group": snapshot["agent_group"],
        "selected_member_ids": ["architecture", "operations"],
        "shared_room": {
            "request": "Review the public proposal.",
            "attachments": [],
            "policy": {
                "private_context": "none",
                "attachments": "explicit_only",
                "tool_mode": "none",
            },
            "posts": [],
        },
        "budget": {
            "participant_calls": 0,
            "llm_calls": 0,
            "tokens": 0,
            "cost": 0.0,
            "rounds": 0,
        },
    }
    timer = threading.Timer(0.05, cancel.set)
    timer.start()

    with pytest.raises(RuntimeError, match="required group member failed"):
        task.execute(FlowFile(content=json.dumps(state).encode()))

    timer.join()
    assert all(client.aborted.is_set() for client in clients.values())
    assert all(len(client.calls) == 1 for client in clients.values())
    assert store.get_run(context.run_id)["usage"] == {}


def test_group_inspector_projection_keeps_bounded_fields_and_redacts_content():
    projected = _event_projection({
        "sequence": 7,
        "event_type": "group_participant_post",
        "timestamp": "2026-08-24T12:00:00+00:00",
        "data": {
            "group_run_id": str(uuid.uuid4()),
            "round": 2,
            "member_id": "architecture",
            "disposition": "post",
            "content": "Finding token=private-value",
            "citations": [{
                "title": "Public source",
                "url": "https://example.test/source",
                "raw_secret": "must-not-project",
            }],
            "confidence": 0.75,
            "token_usage": {"tokens_in": 12, "provider_secret": "hidden"},
            "private_context": "must-not-project",
        },
    })

    assert projected["sequence"] == 7
    assert projected["data"]["content"] == "Finding token=[redacted]"
    assert projected["data"]["citations"] == [{
        "title": "Public source",
        "url": "https://example.test/source",
    }]
    assert projected["data"]["token_usage"] == {"tokens_in": 12}
    assert "private_context" not in projected["data"]


class _FakeToolHandler:
    description = "Fake read-only handler."

    def __init__(self, name, calls):
        self.name = name
        self.calls = calls
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "source": {"type": "string"},
                "query": {"type": "string"},
            },
        }

    def execute(self, arguments):
        self.calls.append((self.name, dict(arguments)))
        return "observed"


def _read_only_group(*effects):
    return AgentGroupDefinition.from_dict(_group_definition(tool_policy={
        "mode": "read_only",
        "allowed_effects": list(effects),
    }))


def test_group_tool_intersection_excludes_mutation_and_private_context_tools():
    group = _read_only_group("filesystem.read", "network.read")
    snapshot = {
        "tools": ["read", "web_search", "write", "recall", "read_history"],
    }

    assert group_read_only_tool_names(group, snapshot) == ("read", "web_search")


def test_group_tool_runtime_denies_unknown_and_projects_full_attribution(
    group_environment, monkeypatch,
):
    store, _ledger, _clients, _services, definitions = group_environment
    context = _context("wr_group_tool", _snapshot(definitions))
    calls = []
    registry = ToolRegistry()
    registry.register(_FakeToolHandler("web_search", calls))
    events = []
    group = _read_only_group("network.read")
    monkeypatch.setattr(
        "core.tool_authorization.authorize_tool_call",
        lambda **_kwargs: SimpleNamespace(decision="legacy", reason=""),
    )
    runtime = GroupReadOnlyToolRuntime(
        context=context,
        group=group,
        member_snapshot={"member_id": "architecture", "tools": ["web_search"]},
        round_number=2,
        group_run_id=str(uuid.uuid4()),
        emit=lambda kind, data: events.append((kind, data)),
        cancelled=lambda: False,
        registry=registry,
    )

    assert runtime.execute(LLMToolCall(
        id="tc-read", name="web_search", arguments={"query": "public docs"},
    )) == "observed"
    denied = runtime.execute(LLMToolCall(
        id="tc-write", name="write", arguments={"path": "/workspace/x"},
    ))

    assert "not allowed" in denied
    assert calls == [("web_search", {"query": "public docs"})]
    lifecycle = [data for kind, data in events if kind == "group_tool_lifecycle"]
    assert [row["phase"] for row in lifecycle] == [
        "announced", "dispatched", "completed", "announced", "denied",
    ]
    assert all(row["run_id"] == context.run_id for row in lifecycle)
    assert all(row["round"] == 2 for row in lifecycle)
    assert all(row["member_id"] == "architecture" for row in lifecycle)
    assert all(row["turn_id"] == context.root_turn_id for row in lifecycle)


def test_group_filesystem_scope_escape_and_cancelled_call_never_dispatch(
    group_environment, monkeypatch,
):
    _store, _ledger, _clients, _services, definitions = group_environment
    context = _context("wr_group_scope", _snapshot(definitions))
    calls = []
    registry = ToolRegistry()
    registry.register(_FakeToolHandler("read", calls))
    events = []
    cancelled = {"value": False}
    runtime = GroupReadOnlyToolRuntime(
        context=context,
        group=_read_only_group("filesystem.read"),
        member_snapshot={"member_id": "operations", "tools": ["read"]},
        round_number=1,
        group_run_id=str(uuid.uuid4()),
        emit=lambda kind, data: events.append((kind, data)),
        cancelled=lambda: cancelled["value"],
        registry=registry,
    )
    monkeypatch.setattr(runtime, "_linked_relays", lambda: ("relay-a",))

    escaped = runtime.execute(LLMToolCall(
        id="tc-escape",
        name="read",
        arguments={"path": "/workspace/a", "source": "relay-b"},
    ))
    cancelled["value"] = True
    retired = runtime.execute(LLMToolCall(
        id="tc-cancelled",
        name="read",
        arguments={"path": "/workspace/a", "source": "relay-a"},
    ))

    assert "escape conversation relay scope" in escaped
    assert "cancelled before tool dispatch" in retired
    assert calls == []
    assert [data["phase"] for kind, data in events] == [
        "announced", "denied", "announced", "retired",
    ]


def test_group_cancellation_invokes_active_tool_kill_hook(
    group_environment, monkeypatch,
):
    from services.tool_relay_service import register_kill_hook

    _store, _ledger, _clients, _services, definitions = group_environment
    context = _context("wr_group_active_tool_cancel", _snapshot(definitions))
    started = threading.Event()
    killed = threading.Event()

    class BlockingHandler(_FakeToolHandler):
        def execute(self, arguments):
            self.calls.append((self.name, dict(arguments)))
            register_kill_hook(killed.set)
            started.set()
            if not killed.wait(2):
                raise AssertionError("group tool kill hook was not invoked")
            return "stopped"

    calls = []
    registry = ToolRegistry()
    registry.register(BlockingHandler("read", calls))
    cancel = threading.Event()
    events = []
    monkeypatch.setattr(
        "core.tool_authorization.authorize_tool_call",
        lambda **_kwargs: SimpleNamespace(decision="legacy", reason=""),
    )
    runtime = GroupReadOnlyToolRuntime(
        context=context,
        group=_read_only_group("filesystem.read"),
        member_snapshot={"member_id": "architecture", "tools": ["read"]},
        round_number=1,
        group_run_id=str(uuid.uuid4()),
        emit=lambda kind, data: events.append((kind, data)),
        cancelled=cancel.is_set,
        cancel_event=cancel,
        registry=registry,
    )
    monkeypatch.setattr(runtime, "_linked_relays", lambda: ("relay-a",))
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault(
        "value",
        runtime.execute(LLMToolCall(
            id="tc-active",
            name="read",
            arguments={"path": "/workspace/a", "source": "relay-a"},
        )),
    ))
    worker.start()
    assert started.wait(1)
    cancel.set()
    worker.join(2)

    assert not worker.is_alive()
    assert killed.is_set()
    assert "cancelled during tool execution" in result["value"]
    assert [data["phase"] for _kind, data in events] == [
        "announced", "dispatched", "retired",
    ]


def test_group_tool_loop_counts_every_llm_call_and_tool_call(group_environment):
    store, _ledger, clients, _services, definitions = group_environment
    client = clients["architect-llm"]

    def response(call, _kwargs):
        if call == 1:
            return LLMResponse(
                model="fake-group-model",
                tokens_in=5,
                finish_reason="tool_calls",
                tool_calls=[LLMToolCall(
                    id="tc-web", name="web_search", arguments={"query": "public"},
                )],
            )
        return _response({
            "disposition": "post",
            "content": "tool-backed finding",
            "citations": [],
        }, tokens=7)

    client.response_factory = response
    context = _context("wr_group_tool_budget", _snapshot(definitions))
    _running(store, context)
    task = AgentParticipantCallTask({})
    task.get_task_id = lambda: "participant_calls"
    task.set_workflow_run_context(
        context, run_store=store, cancel_event=threading.Event(),
    )

    class Runtime:
        allowed_names = ("web_search",)

        def __init__(self):
            self.calls = []

        @staticmethod
        def definitions():
            return [LLMToolDefinition(
                name="web_search", description="search", parameters={
                    "type": "object", "properties": {
                        "query": {"type": "string"},
                    },
                },
            )]

        def execute(self, call):
            self.calls.append(call)
            return "public result"

    runtime = Runtime()
    parsed, usage = _GroupLLMCaller(task).call_json(
        "tool-budget-proof",
        "architect-llm",
        [LLMMessage(role="user", content="review", conversation_id="ephemeral")],
        100,
        token_budget=100,
        tool_runtime=runtime,
    )

    assert parsed["content"] == "tool-backed finding"
    assert usage["llm_calls"] == 2
    assert usage["tokens_in"] == 12
    assert usage["tool_calls"] == 1
    assert [call.id for call in runtime.calls] == ["tc-web"]


def test_group_tool_inspector_projection_is_bounded_and_redacted():
    projected = _event_projection({
        "sequence": 8,
        "event_type": "group_tool_lifecycle",
        "timestamp": "2026-08-24T12:00:00+00:00",
        "data": {
            "group_run_id": str(uuid.uuid4()),
            "run_id": "wr-1",
            "round": 2,
            "member_id": "architecture",
            "turn_id": "turn-1",
            "tool_call_id": "tc-1",
            "tool_name": "read",
            "phase": "denied",
            "reason": "token=private-value",
            "arguments": {"path": "/secret"},
        },
    })

    assert projected["data"]["phase"] == "denied"
    assert projected["data"]["reason"] == "token=[redacted]"
    assert "arguments" not in projected["data"]
