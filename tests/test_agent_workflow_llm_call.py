"""Production agentLLMCall contracts: cache, schema, cancellation, and budget."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
import uuid
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest

from core import FlowFile
from core._service_defs import ServiceDef
from core.agent_contracts import AuthorizationRefContract
from core.agent_turn_identity import AgentTurnIdentity
from core.llm_client import LLMResponse, LLMToolCall
from core.resource_identity import ResourceRef
from core.service_definition_revision import compute_service_definition_revision
from core.usage_ledger import UsageLedger
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    WorkflowConversationRef,
    WorkflowLimits,
    WorkflowRequestBody,
    WorkflowRunContext,
    WorkflowTurnRef,
)
from core.workflow_run_store import WorkflowBudgetExceeded, WorkflowRunStore
from tasks.ai.workflow.llm_call import AgentLLMCallTask


def _context(
    run_id: str = "wr_llm",
    *,
    max_llm_calls: int = 2,
    max_cost_usd: float | None = 1.0,
    service_snapshot: dict | None = None,
) -> WorkflowRunContext:
    auth = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1, root_turn_id="m1"
    )
    identity = AgentTurnIdentity(
        conversation_id="c1",
        root_conversation_id="c1",
        agent_instance="Demo",
        turn_id="m1",
        ingress_msg_id="m1",
        turn_epoch=1,
        run_generation=1,
        authorization_context_id=auth.context_id,
        authorization_revision_at_start=1,
        source_kind="user",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ref = ResourceRef(
        resource_type="flow",
        name="demo:1.0.0",
        scope="global",
        version="1.0.0",
        content_digest="a" * 64,
        source_id="test",
    )
    return WorkflowRunContext(
        run_id=run_id,
        turn_identity=identity,
        conversation_id="c1",
        agent_name="Demo",
        user_id="alice",
        root_turn_id="m1",
        run_generation=1,
        flow_ref=ref,
        channel="web",
        invocation_mode="conversation",
        permission_mode="default",
        authorization_ref=auth,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        limits=WorkflowLimits(
            max_duration_seconds=120,
            max_llm_calls=max_llm_calls,
            max_flowfiles=20,
            max_fanout=2,
            max_cost_usd=max_cost_usd,
        ),
        service_snapshot=service_snapshot or {},
        cancel_token="cancel",
        event_sink="test",
    )


def _request() -> AgentWorkflowRequest:
    return AgentWorkflowRequest(
        request=WorkflowRequestBody(message="hello"),
        conversation=WorkflowConversationRef(id="c1", agent="Demo"),
        turn=WorkflowTurnRef(root_turn_id="m1", request_message_ids=("m1",)),
        parameters={},
    )


class FakeClient:
    def __init__(self, response: LLMResponse):
        self.response = response
        self.calls = 0
        self.last_kwargs = {}
        self._timeout = 60
        self.aborted = threading.Event()

    @property
    def timeout(self):
        return self._timeout

    def reset_abort(self):
        self.aborted.clear()

    def complete(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.response

    def abort(self):
        self.aborted.set()


class BlockingClient(FakeClient):
    def complete(self, **_kwargs):
        self.calls += 1
        if not self.aborted.wait(2):
            raise AssertionError("provider request was not aborted")
        raise RuntimeError("provider aborted")


class FakeService:
    provider = "openai"

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def get_client(self):
        return self.client


@pytest.fixture
def environment(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs.sqlite3")
    ledger = UsageLedger(path=str(tmp_path / "usage.sqlite3"))
    config = {
        "provider": "openai",
        "api_key": "secret",
        "cost_per_1m_input": "10",
        "cost_per_1m_output": "20",
    }
    definition = ServiceDef(
        service_id="Writer",
        service_type="llmConnection",
        scope="user",
        scope_id="alice",
        config=config,
        created_at=1,
    )
    client = FakeClient(
        LLMResponse(
            content='{"answer":"ok"}',
            model="model-1",
            tokens_in=100,
            tokens_out=20,
            duration_ms=12,
            finish_reason="stop",
        )
    )
    service = FakeService(client, config)

    class Registry:
        @staticmethod
        def get_definition(scope, scope_id, service_id):
            if (scope, scope_id, service_id) == ("user", "alice", "Writer"):
                return definition
            return None

        @staticmethod
        def get_live_instance(scope, scope_id, service_id):
            if (scope, scope_id, service_id) == ("user", "alice", "Writer"):
                return service
            return None

    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        classmethod(lambda cls: Registry()),
    )
    monkeypatch.setattr(
        "core.usage_ledger.UsageLedger.instance", classmethod(lambda cls: ledger)
    )
    snapshot = {
        "bindings": {"writer": "Writer"},
        "services": {
            "Writer": {
                "service_id": "Writer",
                "service_type": "llmConnection",
                "scope": "user",
                "scope_id": "alice",
                "definition_revision": compute_service_definition_revision(definition),
            },
        },
    }
    yield store, ledger, definition, service, snapshot


def _running(store, snapshot, **context_kwargs):
    context = _context(service_snapshot=snapshot, **context_kwargs)
    store.create_run(
        context=context, request=_request(), parameters={}, lease_seconds=180
    )
    assert store.transition(context.run_id, "accepted", "running")
    return context


def _task(context, store, **config):
    values = {
        "service": "writer",
        "response_format": "json_schema",
        "json_schema": {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
            "additionalProperties": False,
        },
        "cache_policy": "run_idempotent",
    }
    values.update(config)
    task = AgentLLMCallTask(values)
    task.get_task_id = lambda: "writer_step"
    task.set_workflow_run_context(
        context, run_store=store, cancel_event=threading.Event()
    )
    return task


def test_successful_retry_uses_cache_and_records_usage_once(environment):
    store, ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    task = _task(context, store)
    first = FlowFile(content=b"hello")
    second = FlowFile(content=b"hello")

    task.execute(first)
    task.execute(second)

    assert first.get_content() == second.get_content() == b'{"answer":"ok"}'
    assert service.client.calls == 1
    assert service.client.last_kwargs["max_tokens"] == 0
    assert store.get_run(context.run_id)["usage"]["llm_calls"] == 1
    summary = ledger.summary(run_id=context.run_id, task_id="writer_step")
    assert summary["calls"] == 1
    assert summary["tokens_in"] == 100
    assert summary["tokens_out"] == 20


def test_llm_call_defaults_have_no_token_or_wall_clock_limit():
    task = AgentLLMCallTask({"service": "writer"})
    schema = task.get_parameter_schema()
    assert schema["max_tokens"]["default"] == 0
    assert schema["timeout"]["default"] == 0
    assert task._bound_timeout(SimpleNamespace(deadline_at="")) is None


def test_agent_message_event_preserves_structured_json(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client.response.content = json.dumps({
        "summary": "Readable",
        "notes": "x" * 9000,
        "password": "do-not-expose",
    })
    task = _task(context, store, response_format="text")
    events = []
    task._workflow_event_callback = lambda kind, data: events.append((kind, data))

    task.execute(FlowFile(content=b"hello"))

    message = next(data for kind, data in events if kind == "agent_message")
    assert "content" not in message
    assert message["structured_content"]["summary"] == "Readable"
    assert message["structured_content"]["password"] == "<redacted>"
    assert "more chars" in message["structured_content"]["notes"]
    assert "do-not-expose" not in str(message)


def test_retry_after_ledger_failure_reuses_completed_provider_result(
    environment, monkeypatch
):
    store, ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    task = _task(context, store, retry_attempts=2)
    original_record = ledger.record
    attempts = 0

    def flaky_record(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("usage ledger unavailable")
        return original_record(**kwargs)

    monkeypatch.setattr(ledger, "record", flaky_record)
    with pytest.raises(OSError, match="ledger unavailable"):
        task.execute(FlowFile(content=b"hello"))

    output = FlowFile(content=b"hello")
    task.execute(output)

    assert output.get_content() == b'{"answer":"ok"}'
    assert service.client.calls == 1
    assert store.get_run(context.run_id)["usage"]["llm_calls"] == 1
    assert ledger.summary(run_id=context.run_id)["calls"] == 1


def test_malformed_structured_output_fails_without_usage(environment):
    store, ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client.response.content = "not-json"
    task = _task(context, store, response_format="json")

    with pytest.raises(ValueError, match="malformed JSON"):
        task.execute(FlowFile(content=b"hello"))

    assert store.get_run(context.run_id)["usage"] == {}
    assert ledger.summary(run_id=context.run_id)["calls"] == 0


def test_schema_mismatch_routes_to_failure(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client.response.content = '{"wrong":true}'
    task = _task(context, store)

    with pytest.raises(jsonschema.ValidationError):
        task.execute(FlowFile(content=b"hello"))


def test_json_schema_uses_normal_automatic_tool_path(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    task = _task(context, store, thinking_budget=2048)

    task.execute(FlowFile(content=b"hello"))

    messages = service.client.last_kwargs["messages"]
    assert messages[0].role == "system"
    assert "matching this JSON Schema exactly" in messages[0].content
    assert '"answer"' in messages[0].content
    assert service.client.last_kwargs["response_format"] is None
    assert service.client.last_kwargs["thinking_budget"] == 2048
    tools = service.client.last_kwargs["tools"]
    assert len(tools) == 1
    assert tools[0].name == "submit_workflow_result"
    assert tools[0].parameters == task._json_schema()


def test_json_schema_tool_arguments_become_validated_content(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client.response.content = "ignored free-form text"
    service.client.response.tool_calls = [LLMToolCall(
        id="structured-1",
        name="submit_workflow_result",
        arguments={"answer": "forced"},
    )]
    task = _task(context, store)
    output = FlowFile(content=b"hello")

    task.execute(output)

    assert output.get_content() == b'{"answer":"forced"}'


def test_cancellation_aborts_active_provider_request(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client = BlockingClient(service.client.response)
    cancel = threading.Event()
    task = _task(context, store)
    task._workflow_cancel_event = cancel
    timer = threading.Timer(0.05, cancel.set)
    timer.start()

    with pytest.raises(RuntimeError, match="provider aborted"):
        task.execute(FlowFile(content=b"hello"))

    timer.join()
    assert service.client.aborted.is_set()
    assert store.get_run(context.run_id)["usage"] == {}


def test_bounded_timeout_aborts_without_mutating_read_only_client(environment):
    store, _ledger, _definition, service, snapshot = environment
    context = _running(store, snapshot)
    service.client = BlockingClient(service.client.response)
    task = _task(context, store)

    with pytest.raises(TimeoutError, match="bounded timeout"):
        task._complete_with_cancellation(
            service.client, [], threading.Event(), 0.05)

    assert service.client.aborted.is_set()


def test_changed_service_revision_fails_closed(environment):
    store, _ledger, definition, _service, snapshot = environment
    context = _running(store, snapshot)
    definition.config["default_model"] = "changed"
    task = _task(context, store)

    with pytest.raises(ValueError, match="definition changed"):
        task.execute(FlowFile(content=b"hello"))


def test_summarizer_selector_resolves_to_snapshotted_llm(environment):
    store, _ledger, _definition, service, snapshot = environment
    snapshot["services"]["Summary"] = {
        "service_id": "Summary",
        "service_type": "summarizer",
        "scope": "user",
        "scope_id": "alice",
        "definition_revision": "b" * 64,
        "resolved_llm_service": "Writer",
    }
    context = _running(store, snapshot)
    task = _task(context, store, service="Summary")

    assert task.workflow_authorization_target(None) == {
        "service_id": "Writer",
        "scope": "user",
        "scope_id": "alice",
    }
    task.execute(FlowFile(content=b"hello"))

    assert service.client.calls == 1


def test_cost_exhaustion_is_terminal_after_recording_one_step(environment):
    store, ledger, _definition, _service, snapshot = environment
    context = _running(store, snapshot, max_cost_usd=0.0001)
    task = _task(context, store)

    with pytest.raises(WorkflowBudgetExceeded, match="cost budget"):
        task.execute(FlowFile(content=b"hello"))

    run = store.get_run(context.run_id)
    assert run["status"] == "budget_exceeded"
    assert run["usage"]["llm_calls"] == 1
    assert ledger.summary(run_id=context.run_id)["calls"] == 1
    assert task._workflow_cancel_event.is_set()


def test_retries_require_idempotent_cache(environment):
    store, _ledger, _definition, _service, snapshot = environment
    context = _running(store, snapshot)
    task = _task(context, store, cache_policy="none", retry_attempts=2)

    with pytest.raises(ValueError, match="run_idempotent"):
        task.workflow_retry_attempts(3)


def test_zero_retry_attempts_are_unlimited(environment):
    store, _ledger, _definition, _service, snapshot = environment
    context = _running(store, snapshot)
    task = _task(context, store, retry_attempts=0)

    assert task.workflow_retry_attempts(3) == 0


def test_budget_terminal_stops_authored_failure_branch(environment):
    from core import TaskFactory
    from core.base_task import BaseTask
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    store, _ledger, _definition, _service, snapshot = environment
    context = _running(store, snapshot, max_cost_usd=0.0001)
    mutations = []

    class MutationSentinel(BaseTask):
        TYPE = "testWorkflowBudgetMutationSentinel"

        def execute(self, flowfile):
            mutations.append(flowfile.get_content())
            return [flowfile]

    TaskFactory.register(MutationSentinel)
    flow = FlowParser.parse({
        "id": "budget-stop",
        "name": "Budget stop",
        "tasks": {
            "llm": {
                "type": "agentLLMCall",
                "parameters": {
                    "service": "Writer",
                    "response_format": "json",
                },
            },
            "mutation": {
                "type": MutationSentinel.TYPE, "parameters": {}},
        },
        "relations": [{
            "from": "llm", "to": "mutation", "type": "failure"}],
        "entries": ["llm"],
        "exits": ["mutation"],
    })

    result = ContinuousFlowExecutor.run_batch(
        flow, input_flowfiles=[FlowFile(content=b"hello")],
        entry_task_id="llm", suppress_one_shot_roots=True,
        max_retries=1, timeout=5,
        runtime_context={
            "workflow_run_context": context,
            "workflow_allowed_effects": ["resource.read"],
            "workflow_run_store": store,
            "workflow_cancel_event": threading.Event(),
        })

    assert result.output_flowfiles == []
    assert store.get_run(context.run_id)["status"] == "budget_exceeded"
    assert mutations == []
