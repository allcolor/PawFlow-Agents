"""WP3 process-resident workflow-agent vertical slice."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from core.agent_contracts import (
    AuthorizationRefContract,
    CapabilityEffect,
    IdempotencyClass,
)
from core.agent_runtime_router import AgentRunKey
from core.agent_turn_identity import AgentTurnIdentity
from core.base_task import BaseTask
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    AgentWorkflowResult,
    PreparedAgentTurn,
    WorkflowConversationRef,
    WorkflowInstanceConfig,
    WorkflowLimits,
    WorkflowRequestBody,
    WorkflowRunContext,
    WorkflowTurnRef,
)
from core.workflow_agent_resources import (
    ResolvedAgentWorkflow,
    validate_agent_workflow_definition,
)
from core.workflow_agent_runtime import (
    _MAX_PENDING_CACHE_PER_AGENT,
    WorkflowAgentRuntime,
    _ActiveRun,
    require_single_workflow_terminal,
)
from core.workflow_task_safety import (
    WorkflowTaskSafetyError,
    authorize_workflow_task,
    workflow_task_metadata,
)

EFFECTS = ["resource.read"]


def _ref() -> ResourceRef:
    return ResourceRef(
        resource_type="flow",
        name="pawflow.agents.demo:1.0.0",
        scope="global",
        version="1.0.0",
        content_digest="a" * 64,
        source_id="repository:global:pawflow.agents.demo:1.0.0",
    )


def _turn(generation: int, turn_id: str = "web:turn-1",
          permission_mode: str = "default") -> PreparedAgentTurn:
    auth_id = str(uuid.uuid4())
    auth = AuthorizationRefContract(
        context_id=auth_id, revision=1, root_turn_id=turn_id)
    identity = AgentTurnIdentity(
        conversation_id="conv-1",
        root_conversation_id="conv-1",
        agent_instance="Demo",
        turn_id=turn_id,
        ingress_msg_id=turn_id,
        turn_epoch=generation,
        run_generation=generation,
        authorization_context_id=auth_id,
        authorization_revision_at_start=1,
        source_kind="user",
        source_id="alice",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return PreparedAgentTurn(
        turn_identity=identity,
        conversation_id="conv-1",
        agent_name="Demo",
        user_id="alice",
        root_turn_id=turn_id,
        request_message_ids=(turn_id,),
        channel="web",
        message="hello",
        source={"type": "user", "name": "alice"},
        permission_mode=permission_mode,
        authorization_ref=auth,
    )


def _definition() -> dict:
    path = Path(
        "data/repository/flows/global/pawflow/agents/demo/versions/1.0.0.json")
    return json.loads(path.read_text(encoding="utf-8"))


class _SafeReadTask(BaseTask):
    TYPE = "testWorkflowSafeRead"
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"

    def workflow_authorization_target(self, _flowfile):
        return dict(self.config.get("target") or {})

    def execute(self, flowfile):
        return [flowfile]


def _safety_context(auth_ref, permission_mode="default"):
    return SimpleNamespace(
        run_id="wr_safety",
        user_id="alice",
        conversation_id="conv-1",
        agent_name="Demo",
        root_turn_id="web:turn-1",
        permission_mode=permission_mode,
        authorization_ref=auth_ref,
        service_snapshot={"services": {}},
    )


def test_reference_workflow_is_valid_and_bootstrap_only():
    from tasks import register_all_tasks
    register_all_tasks()
    definition = _definition()
    assert validate_agent_workflow_definition(definition)["ok"]
    assert {
        task["type"] for task in definition["tasks"].values()
    } == {
        "inputPort", "agentWorkflowInput", "emitAgentProgress",
        "workflowFakeLLM", "completeAgentTurn", "outputPort",
    }


def test_workflow_task_metadata_rejects_undeclared_pfp_capabilities():
    class UndeclaredPackageTask(_SafeReadTask):
        TYPE = "testUndeclaredPackageTask"
        PACKAGE_RUNTIME: ClassVar = {
            "package": "example.tasks", "version": "1.0.0"}

    with pytest.raises(WorkflowTaskSafetyError, match="no package capability"):
        workflow_task_metadata(UndeclaredPackageTask)


@pytest.mark.parametrize("target, message", [
    ({"user_id": "bob"}, "another user"),
    ({"conversation_id": "conv-2"}, "another conversation"),
    ({"scope": "user", "scope_id": "bob"}, "another user scope"),
    ({"relay_id": "OtherRelay"}, "outside the conversation binding"),
    ({"resource_paths": ["/workspace-other/file"]}, "outside the bound roots"),
    ({"service_id": "OtherService"}, "outside the run snapshot"),
])
def test_workflow_task_targets_cannot_escape_the_run(target, message):
    auth = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1,
        root_turn_id="web:turn-1")
    with pytest.raises(WorkflowTaskSafetyError, match=message):
        authorize_workflow_task(
            _SafeReadTask({"target": target}), "read", None,
            {
                "workflow_run_context": _safety_context(auth),
                "workflow_allowed_effects": EFFECTS,
                "workflow_allowed_relay_ids": ["MyWorkspace"],
                "workflow_resource_roots": ["/workspace"],
            },
            1,
        )


def test_read_only_workflow_denial_happens_before_task_execution():
    from core import FlowFile, TaskFactory
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    executed = []

    class WriteTask(BaseTask):
        TYPE = "testWorkflowDeniedWrite"
        AGENT_WORKFLOW_SAFE = True
        EFFECTS = (CapabilityEffect.FILESYSTEM_WRITE,)
        IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
        AUTHORIZATION_TARGET_KIND = "filesystem.paths"

        def execute(self, flowfile):
            executed.append(True)
            return [flowfile]

    TaskFactory.register(WriteTask)
    auth = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1,
        root_turn_id="web:turn-1")
    flow = FlowParser.parse({
        "id": "denied-write",
        "name": "Denied write",
        "tasks": {"write": {"type": WriteTask.TYPE, "parameters": {}}},
        "relations": [],
        "entries": ["write"],
        "exits": ["write"],
    })

    ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=[FlowFile(content=b"{}")],
        entry_task_id="write",
        suppress_one_shot_roots=True,
        max_retries=1,
        timeout=5,
        runtime_context={
            "workflow_run_context": _safety_context(auth, "read_only"),
            "workflow_allowed_effects": ["filesystem.write"],
        },
    )

    assert executed == []


def test_authority_revision_is_reevaluated_at_each_task_boundary(
        tmp_path, monkeypatch):
    from core import authorization_context as ac
    from core import tool_authorization as ta

    store = ac.AuthorizationContextStore(root=tmp_path / "authorization")
    first_doc = store.create(
        user_id="alice", conversation_id="conv-1",
        root_turn_id="web:turn-1", root_message_id="web:turn-1",
        content="Read the resource")
    first_ref = ac.AuthorizationContextStore.ref(first_doc)
    active = [first_ref]
    seen_revisions = []

    class Gate:
        def evaluate(self, envelope, **_kwargs):
            authority = envelope["authority"]
            seen_revisions.append(authority["revision"])
            decision = "deny" if authority["followups"] else "allow"
            return {"decision": decision, "reason": "current authority",
                    "evaluators": []}

    resolved = {
        "bound": True,
        "broken": False,
        "conversation": {"error": ""},
        "agent": {"error": ""},
        "gates": [{"origin": "conversation", "ref": {}, "service": Gate()}],
    }
    monkeypatch.setattr(
        ac.AuthorizationContextStore, "instance",
        classmethod(lambda cls: store))
    monkeypatch.setattr(ac, "active_authority_ref", lambda *_args: active[0])
    monkeypatch.setattr("core.gating_bindings.resolve_gates",
                        lambda *_args: resolved)
    monkeypatch.setattr(ta, "_audit_dir", lambda: tmp_path / "audit")
    auth = AuthorizationRefContract.from_dict(first_ref.to_dict())
    runtime_context = {
        "workflow_run_context": _safety_context(auth),
        "workflow_allowed_effects": EFFECTS,
    }
    task = _SafeReadTask({})

    authorize_workflow_task(task, "read", None, runtime_context, 1)
    revised = store.append_user_directive(
        "alice", "conv-1", first_ref.context_id,
        message_id="web:correction", content="Stop reading",
        expected_revision=1)
    active[0] = ac.AuthorizationContextStore.ref(revised)
    with pytest.raises(WorkflowTaskSafetyError, match="authorization deny"):
        authorize_workflow_task(task, "read", None, runtime_context, 2)

    assert seen_revisions == [1, 2]


def test_unrelated_active_authority_does_not_replace_run_lineage(monkeypatch):
    from core import authorization_context as ac
    from core import tool_authorization as ta

    initial = AuthorizationRefContract(
        context_id=str(uuid.uuid4()), revision=1,
        root_turn_id="web:turn-1")
    unrelated = ac.AuthorizationRef(
        context_id=str(uuid.uuid4()), revision=1,
        root_turn_id="web:turn-2")
    seen = []

    monkeypatch.setattr(ac, "active_authority_ref", lambda *_args: unrelated)
    monkeypatch.setattr(
        ta, "authorize_tool_call",
        lambda **kwargs: (
            seen.append(kwargs["authorization_ref"])
            or SimpleNamespace(decision="legacy", reason="")
        ),
    )

    authorize_workflow_task(
        _SafeReadTask({}), "read", None,
        {
            "workflow_run_context": _safety_context(initial),
            "workflow_allowed_effects": EFFECTS,
        },
        1,
    )

    assert len(seen) == 1
    assert seen[0].context_id == initial.context_id
    assert seen[0].root_turn_id == initial.root_turn_id


def test_zero_or_multiple_terminals_fail_closed():
    import pytest

    with pytest.raises(RuntimeError, match="got 0"):
        require_single_workflow_terminal([])
    with pytest.raises(RuntimeError, match="got 2"):
        require_single_workflow_terminal([object(), object()])


def test_new_conversation_run_refreshes_only_a_stale_binding(monkeypatch):
    current_ref = ResourceRef.from_dict({
        **_ref().to_dict(),
        "content_digest": "b" * 64,
    })
    stale_binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "flow_ref": _ref().to_dict(),
    })
    refreshed_binding = {
        **stale_binding.to_dict(),
        "flow_ref": current_ref.to_dict(),
    }
    resolved = ResolvedAgentWorkflow(_definition(), current_ref)
    monkeypatch.setattr(
        "core.workflow_agent_runtime.resolve_exact_agent_workflow",
        lambda *_args: resolved)
    rebound = []
    monkeypatch.setattr(
        "core.workflow_agent_resources.bind_agent_workflow",
        lambda workflow, user_id, conversation_id: (
            rebound.append((workflow, user_id, conversation_id))
            or refreshed_binding))

    binding, reusable_resolution = (
        WorkflowAgentRuntime._refresh_new_conversation_binding(
            _turn(1), stale_binding))

    assert binding.flow_ref == current_ref
    assert reusable_resolution is None
    assert rebound == [(stale_binding.to_dict(), "alice", "conv-1")]


def test_executor_reasserts_reserved_identity_at_every_boundary():
    from core import FlowFile, TaskFactory
    from core.base_task import BaseTask
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    class MutateIdentity(BaseTask):
        TYPE = "testMutateWorkflowIdentity"

        def execute(self, flowfile):
            flowfile.set_attribute("workflow.run_id", "attacker")
            return [flowfile]

    class AssertIdentity(BaseTask):
        TYPE = "testAssertWorkflowIdentity"

        def execute(self, flowfile):
            assert flowfile.get_attribute("workflow.run_id") == "wr_server"
            return [flowfile]

    TaskFactory.register(MutateIdentity)
    TaskFactory.register(AssertIdentity)
    flow = FlowParser.parse({
        "id": "reserved-boundary",
        "name": "Reserved boundary",
        "tasks": {
            "mutate": {"type": MutateIdentity.TYPE, "parameters": {}},
            "assert": {"type": AssertIdentity.TYPE, "parameters": {}},
        },
        "relations": [{"from": "mutate", "to": "assert", "type": "success"}],
        "entries": ["mutate"],
        "exits": ["assert"],
    })
    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=[FlowFile(
            content=b"{}", attributes={"workflow.run_id": "wr_server"})],
        entry_task_id="mutate",
        suppress_one_shot_roots=True,
        max_retries=1,
        timeout=5,
        runtime_context={
            "workflow_reserved_attributes": {"workflow.run_id": "wr_server"},
        },
    )
    assert result.success


def test_isolated_executor_stages_one_terminal_and_progress_only():
    from core import FlowFile
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser
    from tasks import register_all_tasks

    register_all_tasks()
    request = _turn(1)
    context = WorkflowRunContext(
        run_id="wr_test",
        turn_identity=request.turn_identity,
        conversation_id=request.conversation_id,
        agent_name=request.agent_name,
        user_id=request.user_id,
        root_turn_id=request.root_turn_id,
        run_generation=1,
        flow_ref=_ref(),
        channel="web",
        invocation_mode="conversation",
        permission_mode="default",
        authorization_ref=request.authorization_ref,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        limits=WorkflowLimits(
            max_duration_seconds=60, max_llm_calls=1,
            max_flowfiles=10, max_fanout=2),
        service_snapshot={},
        cancel_token="cancel:test",
        event_sink="test",
    )
    terminals = []
    events = []
    body = {
        "schema_version": 1,
        "request": {"message": "hello", "attachments": []},
        "conversation": {"id": "conv-1", "agent": "Demo"},
        "turn": {
            "root_turn_id": "web:turn-1",
            "request_message_ids": ["web:turn-1"],
        },
        "parameters": {},
    }
    execution = ContinuousFlowExecutor.run_batch(
        FlowParser.parse(_definition()),
        input_flowfiles=[FlowFile(content=json.dumps(body).encode())],
        max_retries=1,
        timeout=10,
        runtime_context={
            "workflow_run_context": context,
            "workflow_allowed_effects": EFFECTS,
            "workflow_event_callback": lambda kind, data: events.append((kind, data)),
            "workflow_terminal_callback": terminals.append,
        },
        entry_task_id="turn_in",
        suppress_one_shot_roots=True,
    )
    assert execution.success
    assert len(terminals) == 1
    assert terminals[0].response == "Workflow completed: hello"
    lifecycle = [data for kind, data in events
                 if kind == "workflow_progress"
                 and data.get("stage") in {"task_started", "task_completed"}]
    task_ids = list(_definition()["tasks"])
    assert [(row["task_id"], row["stage"]) for row in lifecycle] == [
        item for task_id in task_ids
        for item in ((task_id, "task_started"), (task_id, "task_completed"))
    ]


def test_recovery_preserves_read_only_permission_mode():
    request = _turn(1, permission_mode="read_only")
    workflow_request = AgentWorkflowRequest(
        request=WorkflowRequestBody(message=request.message),
        conversation=WorkflowConversationRef(
            id=request.conversation_id, agent=request.agent_name),
        turn=WorkflowTurnRef(
            root_turn_id=request.root_turn_id,
            request_message_ids=request.request_message_ids),
        parameters={},
    )
    recovered = WorkflowAgentRuntime._prepared_from_run({
        "authorization_ref": request.authorization_ref.to_dict(),
        "request": workflow_request.to_dict(),
        "created_at": time.time(),
        "conversation_id": request.conversation_id,
        "agent_name": request.agent_name,
        "user_id": request.user_id,
        "root_turn_id": request.root_turn_id,
        "run_generation": request.turn_identity.run_generation,
        "channel": request.channel,
        "permission_mode": "read_only",
    })

    assert recovered.permission_mode == "read_only"


def test_boot_recovery_isolates_bad_run_and_releases_its_claims(monkeypatch):
    failed = []
    released = []
    recovered = []

    class Store:
        def list_recoverable(self, statuses=None):
            if statuses == ("committing",):
                return ()
            return ({
                "run_id": "wr_bad", "conversation_id": "conv-bad",
                "agent_name": "Bad", "run_generation": 1,
                "status": "accepted",
            }, {
                "run_id": "wr_good", "conversation_id": "conv-good",
                "agent_name": "Good", "run_generation": 1,
                "status": "running",
            })

        def fail(self, run_id, reason):
            failed.append((run_id, reason))

        def supersede(self, *_args):
            raise AssertionError("no run should be superseded")

    class Inbox:
        def recover_orphaned_workflow_claims(self, run_ids):
            assert set(run_ids) == {"wr_bad", "wr_good"}

        def release(self, *args):
            released.append(args)

    store = Store()
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: store))
    monkeypatch.setattr(
        "core.agent_inbox_store.AgentInboxStore.instance",
        classmethod(lambda cls: Inbox()))
    runtime = WorkflowAgentRuntime.__new__(WorkflowAgentRuntime)
    runtime._lock = threading.RLock()
    runtime._active = {}

    def recover(run_id):
        if run_id == "wr_bad":
            raise ValueError("corrupt snapshot")
        recovered.append(run_id)

    runtime.recover = recover

    runtime._recover_durable_runs()

    assert failed == [("wr_bad", "recovery failed: corrupt snapshot")]
    assert released == [("conv-bad", "Bad", "wr_bad")]
    assert recovered == ["wr_good"]


def test_force_stop_cancels_active_and_drops_queued_successor(monkeypatch):
    runtime = WorkflowAgentRuntime()
    first_started = threading.Event()
    second_started = threading.Event()

    def fake_run(key, active):
        if active.request.root_turn_id == "web:first":
            first_started.set()
            active.cancel_event.wait(2)
        else:
            second_started.set()

    monkeypatch.setattr(runtime, "_run", fake_run)
    first = _turn(1, "web:first")
    second = _turn(2, "web:second")
    assert runtime.submit(first)["queued"] is False
    assert first_started.wait(1)
    assert runtime.submit(second)["queued"] is True
    assert runtime.cancel(AgentRunKey("conv-1", "Demo"), "force stop", True)
    assert not second_started.wait(0.1)


def test_checkpoint_policy_queues_for_active_run(monkeypatch):
    runtime = WorkflowAgentRuntime()
    key = runtime._key("conv-1", "Demo")
    active = _ActiveRun(request=_turn(1, "web:first"), run_id="wr_first")
    runtime._active[key] = active
    monkeypatch.setattr(runtime, "_preempt_policy", lambda _request: "checkpoint")

    result = runtime.submit(_turn(2, "web:follow-up"))

    assert result["queued"] is True
    assert result["checkpoint"] is True
    assert runtime._pending[key][0].root_turn_id == "web:follow-up"
    assert not active.cancel_event.is_set()


def test_conversation_submission_deduplicates_active_and_pending_root_turns(
        monkeypatch):
    runtime = WorkflowAgentRuntime()
    key = runtime._key("conv-1", "Demo")
    runtime._active[key] = _ActiveRun(
        request=_turn(1, "web:first"), run_id="wr_first")
    monkeypatch.setattr(runtime, "_preempt_policy", lambda _request: "queue")

    active_duplicate = runtime.submit(_turn(2, "web:first"))
    first_pending = runtime.submit(_turn(3, "web:follow-up"))
    pending_duplicate = runtime.submit(_turn(4, "web:follow-up"))

    assert active_duplicate == {
        "status": "accepted", "queued": False, "run_id": "wr_first"}
    assert first_pending["queued"] is True
    assert pending_duplicate["queued"] is True
    assert len(runtime._pending[key]) == 1


def test_pending_cache_is_bounded_and_spills_conversation_turns_to_inbox(
        monkeypatch):
    runtime = WorkflowAgentRuntime()
    key = runtime._key("conv-1", "Demo")
    runtime._active[key] = _ActiveRun(
        request=_turn(1, "web:first"), run_id="wr_first")
    monkeypatch.setattr(runtime, "_preempt_policy", lambda _request: "queue")

    for index in range(_MAX_PENDING_CACHE_PER_AGENT):
        assert runtime.submit(_turn(
            index + 2, f"web:pending-{index}"))["queued"] is True
    overflow = runtime.submit(_turn(99, "web:durable-overflow"))

    assert len(runtime._pending[key]) == _MAX_PENDING_CACHE_PER_AGENT
    assert overflow == {
        "status": "accepted", "queued": True, "checkpoint": False,
        "run_id": "", "durable_queue": True,
    }


def test_pending_cache_overflow_rejects_non_durable_bound_submission(
        monkeypatch):
    runtime = WorkflowAgentRuntime()
    key = runtime._key("conv-1", "Demo")
    runtime._active[key] = _ActiveRun(
        request=_turn(1, "web:first"), run_id="wr_first")
    monkeypatch.setattr(runtime, "_preempt_policy", lambda _request: "queue")
    for index in range(_MAX_PENDING_CACHE_PER_AGENT):
        runtime.submit(_turn(index + 2, f"web:pending-{index}"))
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "flow_ref": _ref().to_dict(),
    })

    with pytest.raises(RuntimeError, match="pending queue is full"):
        runtime.submit_bound(
            _turn(99, "web:maintenance"), binding,
            invocation_mode="silent_maintenance")


def test_bound_silent_submission_pins_binding_and_invocation_mode(monkeypatch):
    runtime = WorkflowAgentRuntime()
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "flow_ref": _ref().to_dict(),
    })
    started = []
    monkeypatch.setattr(
        runtime, "_start_worker",
        lambda worker_key, active: started.append((worker_key, active)))

    result = runtime.submit_bound(
        _turn(2, "web:maintenance"),
        binding,
        invocation_mode="silent_maintenance",
    )

    assert result["queued"] is False
    active = started[0][1]
    assert active.binding == binding
    assert active.invocation_mode == "silent_maintenance"
    assert runtime._context(active, binding).invocation_mode == "silent_maintenance"


def test_active_snapshot_exposes_conversation_run_and_hides_silent_maintenance():
    runtime = WorkflowAgentRuntime()
    visible = _ActiveRun(
        request=_turn(1, "web:visible"), run_id="wr_visible",
        started_at=100.0, status="Extracting facts")
    silent = _ActiveRun(
        request=_turn(2, "web:silent"), run_id="wr_silent",
        invocation_mode="silent_maintenance", started_at=100.0)
    other = _ActiveRun(
        request=_turn(3, "web:other"), run_id="wr_other",
        started_at=100.0)
    other.request = other.request.model_copy(
        update={"conversation_id": "conv-other"})
    runtime._active = {
        runtime._key("conv-1", "Demo"): visible,
        runtime._key("conv-1", "Silent"): silent,
        runtime._key("conv-other", "Demo"): other,
    }

    visible.started_at = time.time() - 30.0
    rows = runtime.active_snapshot("conv-1")

    assert len(rows) == 1
    assert rows[0] == {
        "agent_name": "Demo",
        "task_id": "",
        "turn_id": "web:visible",
        "workflow_run_id": "wr_visible",
        "iteration": 0,
        "round": 0,
        "max_rounds": 0,
        "last_tool": "",
        "duration_s": pytest.approx(30.0, abs=0.1),
        "status": "Extracting facts",
        "message_preview": "hello",
        "runtime_kind": "workflow",
    }


def test_active_snapshot_hides_durable_waits_and_retryable_pauses():
    runtime = WorkflowAgentRuntime()
    runtime._active = {
        runtime._key("conv-1", "Waiting"): _ActiveRun(
            request=_turn(1, "web:waiting"), run_id="wr_waiting",
            status="waiting"),
        runtime._key("conv-1", "Paused"): _ActiveRun(
            request=_turn(2, "web:paused"), run_id="wr_paused",
            status="retryable_failed"),
        runtime._key("conv-1", "Working"): _ActiveRun(
            request=_turn(3, "web:working"), run_id="wr_working",
            status="reviewing"),
    }

    rows = runtime.active_snapshot("conv-1")

    assert [row["workflow_run_id"] for row in rows] == ["wr_working"]


def test_restart_policy_transfers_claims_and_replaces_generation(monkeypatch):
    runtime = WorkflowAgentRuntime()
    key = runtime._key("conv-1", "Demo")
    previous = _ActiveRun(request=_turn(1, "web:first"), run_id="wr_first")
    runtime._active[key] = previous
    transfers = []
    started = []

    class Inbox:
        def transfer(self, *args):
            transfers.append(args)

    monkeypatch.setattr(runtime, "_preempt_policy", lambda _request: "restart")
    monkeypatch.setattr(runtime, "_start_worker",
                        lambda worker_key, active: started.append(
                            (worker_key, active)))
    monkeypatch.setattr(
        "core.agent_inbox_store.AgentInboxStore.instance",
        classmethod(lambda cls: Inbox()))

    result = runtime.submit(_turn(2, "web:restart"))

    assert result["restarted"] is True
    assert previous.cancel_event.is_set()
    replacement = runtime._active[key]
    assert replacement is started[0][1]
    assert transfers == [
        ("conv-1", "demo", "wr_first", replacement.run_id)]


def test_recovery_fails_closed_when_exact_flow_is_missing(
        monkeypatch, tmp_path):
    from core.agent_inbox_store import AgentInboxStore
    from core.workflow_run_store import WorkflowRunStore

    run_store = WorkflowRunStore(tmp_path / "runs.sqlite3")
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    request = _turn(1, "web:missing-flow")
    inbox.enqueue("conv-1", "Demo", {
        "msg_id": request.root_turn_id,
        "content": request.message,
        "attachments": [],
        "channel": "web",
        "source": request.source,
    }, source="user")
    binding = {
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "parameters": {},
        "limits": {
            "max_duration_seconds": 30,
            "max_llm_calls": 1,
            "max_flowfiles": 10,
            "max_fanout": 2,
        },
        "flow_ref": _ref().to_dict(),
    }
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: run_store))
    monkeypatch.setattr(
        "core.agent_inbox_store.AgentInboxStore.instance",
        classmethod(lambda cls: inbox))
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args: {"runtime_kind": "workflow", "workflow": binding})
    monkeypatch.setattr(
        "core.workflow_agent_runtime.resolve_exact_agent_workflow",
        lambda *_args: (_ for _ in ()).throw(
            FileNotFoundError("exact flow version is missing")))
    original_fail = run_store.fail

    def fail_after_inbox_cleanup(run_id, reason):
        assert [item.state for item in inbox.list_items("conv-1", "Demo")] == [
            "discarded"]
        return original_fail(run_id, reason)

    monkeypatch.setattr(run_store, "fail", fail_after_inbox_cleanup)

    runtime = WorkflowAgentRuntime()
    ack = runtime.submit(request)
    deadline = time.monotonic() + 3
    record = None
    while time.monotonic() < deadline:
        record = run_store.get_run(ack["run_id"])
        if record is not None and record["status"] == "failed":
            break
        time.sleep(0.01)

    assert record is not None
    assert record["status"] == "failed"
    assert "exact flow version is missing" in record["reason"]
    assert [item.state for item in inbox.list_items("conv-1", "Demo")] == [
        "discarded"]
    assert inbox.pending_count("conv-1", "Demo") == 0


def test_terminal_failure_discards_inbox_instead_of_replaying(
        monkeypatch, tmp_path):
    from core.agent_inbox_store import AgentInboxStore
    from core.workflow_run_store import WorkflowRunStore
    from engine.continuous_executor import ContinuousFlowExecutor

    run_store = WorkflowRunStore(tmp_path / "runs.sqlite3")
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    resolved = ResolvedAgentWorkflow(definition=_definition(), ref=_ref())
    binding = {
        "flow_fqn": resolved.ref.name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "parameters": {},
        "limits": {
            "max_duration_seconds": 30,
            "max_llm_calls": 1,
            "max_flowfiles": 10,
            "max_fanout": 2,
        },
        "flow_ref": resolved.ref.to_dict(),
    }
    request = _turn(1, "web:terminal-failure")
    inbox.enqueue("conv-1", "Demo", {
        "msg_id": request.root_turn_id,
        "content": request.message,
        "attachments": [],
        "channel": "web",
        "source": request.source,
    }, source="user")
    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: run_store))
    monkeypatch.setattr(
        "core.agent_inbox_store.AgentInboxStore.instance",
        classmethod(lambda cls: inbox))
    monkeypatch.setattr(
        "core.workflow_agent_runtime.resolve_exact_agent_workflow",
        lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args, **_kwargs: {
            "runtime_kind": "workflow", "workflow": binding})
    monkeypatch.setattr(
        ContinuousFlowExecutor, "run_batch",
        classmethod(lambda cls, *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("deterministic terminal failure"))))

    runtime = WorkflowAgentRuntime()
    ack = runtime.submit(request)
    deadline = time.monotonic() + 3
    record = None
    while time.monotonic() < deadline:
        record = run_store.get_run(ack["run_id"])
        if record is not None and record["status"] == "failed":
            break
        time.sleep(0.01)

    assert record is not None
    assert record["status"] == "failed"
    items = inbox.list_items("conv-1", "Demo")
    assert [item.state for item in items] == ["discarded"]
    assert inbox.pending_count("conv-1", "Demo") == 0


def test_recovery_uses_durable_binding_not_current_config(
        monkeypatch, tmp_path):
    from core.agent_inbox_store import AgentInboxStore
    from core.workflow_run_store import WorkflowRunStore

    run_store = WorkflowRunStore(tmp_path / "runs.sqlite3")
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    runtime = WorkflowAgentRuntime()
    request = _turn(1, "web:snapshot")
    active = _ActiveRun(request=request, run_id="wr_snapshot")
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "parameters": {"snapshot": True},
        "limits": {
            "max_duration_seconds": 30,
            "max_llm_calls": 1,
            "max_flowfiles": 10,
            "max_fanout": 2,
        },
        "flow_ref": _ref().to_dict(),
    })
    context = runtime._context(active, binding)
    workflow_request = AgentWorkflowRequest(
        request=WorkflowRequestBody(message=request.message),
        conversation=WorkflowConversationRef(
            id=request.conversation_id, agent=request.agent_name),
        turn=WorkflowTurnRef(
            root_turn_id=request.root_turn_id,
            request_message_ids=request.request_message_ids),
        parameters=binding.parameters,
    )
    run_store.create_run(
        context=context, request=workflow_request,
        parameters=binding.parameters, lease_seconds=90,
        binding=binding.to_dict())

    monkeypatch.setattr(
        "core.workflow_run_store.WorkflowRunStore.instance",
        classmethod(lambda cls: run_store))
    monkeypatch.setattr(
        "core.agent_inbox_store.AgentInboxStore.instance",
        classmethod(lambda cls: inbox))
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("current config must not be read during recovery")))
    resolved_names = []

    def missing_exact(flow_fqn, *_args):
        resolved_names.append(flow_fqn)
        raise FileNotFoundError("stored exact flow is missing")

    monkeypatch.setattr(
        "core.workflow_agent_runtime.resolve_exact_agent_workflow",
        missing_exact)
    key = runtime._key(request.conversation_id, request.agent_name)
    runtime._active[key] = active

    runtime._run(key, active)

    assert resolved_names == [binding.flow_fqn]
    record = run_store.get_run(active.run_id)
    assert record["status"] == "failed"
    assert record["parameters"] == {"snapshot": True}


def test_stale_generation_cannot_finalize(monkeypatch):
    runtime = WorkflowAgentRuntime()
    active = _ActiveRun(request=_turn(1), run_id="wr_stale")
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": _ref().name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "flow_ref": _ref().to_dict(),
    })
    context = runtime._context(active, binding)

    def forbidden_writer(*_args, **_kwargs):
        raise AssertionError("stale workflow attempted transcript finalization")

    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(forbidden_writer))
    runtime._finalize(
        AgentRunKey("conv-1", "Demo"),
        active,
        context,
        AgentWorkflowResult(
            status="completed",
            response="must not commit",
            answered_turn_ids=("web:turn-1",),
        ),
    )


def test_active_agent_soft_stop_routes_to_workflow_runtime(monkeypatch):
    from tasks.ai.actions.cancel_interrupt import _cancel_workflow_runtime

    calls = []

    class Runtime:
        def cancel(self, key, reason, force):
            calls.append((key, reason, force))
            return True

    monkeypatch.setattr(WorkflowAgentRuntime, "_instance", Runtime())

    assert _cancel_workflow_runtime(
        "conv-1", "Demo", reason="interrupt", force=False)
    assert calls == [(AgentRunKey("conv-1", "demo"), "interrupt", False)]


def test_runtime_executes_and_commits_one_correlated_terminal(monkeypatch):
    from engine.continuous_executor import ContinuousFlowExecutor

    definition = _definition()
    resolved = ResolvedAgentWorkflow(definition=definition, ref=_ref())
    binding = {
        "flow_fqn": resolved.ref.name,
        "flow_scope": "global",
        "input_port": "turn_in",
        "terminal_port": "turn_out",
        "preempt_policy": "queue",
        "allowed_effects": EFFECTS,
        "parameters": {},
        "limits": {
            "max_llm_calls": 1,
            "max_flowfiles": 10,
            "max_fanout": 2,
        },
        "flow_ref": resolved.ref.to_dict(),
    }
    monkeypatch.setattr(
        "core.workflow_agent_runtime.resolve_exact_agent_workflow",
        lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args, **_kwargs: {
            "runtime_kind": "workflow", "workflow": binding})

    batch_timeouts = []
    original_run_batch = ContinuousFlowExecutor.run_batch.__func__

    def capture_timeout(cls, *args, **kwargs):
        batch_timeouts.append(kwargs.get("timeout"))
        return original_run_batch(cls, *args, **kwargs)

    monkeypatch.setattr(
        ContinuousFlowExecutor, "run_batch", classmethod(capture_timeout))

    events = []
    committed = []
    done = threading.Event()

    class FakeBus:
        def publish_event(self, conversation_id, event_type, data):
            events.append((conversation_id, event_type, data))
            if event_type == "done":
                done.set()

    class FakeWriter:
        def enqueue_message_if_absent(self, message, **kwargs):
            committed.append((message, kwargs))
            return True

    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        classmethod(lambda cls: FakeBus()))
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        classmethod(lambda cls, _cid: FakeWriter()))

    runtime = WorkflowAgentRuntime()
    ack = runtime.submit(_turn(1))
    assert ack["status"] == "accepted"
    assert done.wait(3)
    deadline = time.monotonic() + 3
    while runtime._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not runtime._active
    assert batch_timeouts == [None]
    assert len(committed) == 1
    assert committed[0][0]["content"] == "Workflow completed: hello"
    done_events = [row for row in events if row[1] == "done"]
    assert len(done_events) == 1
    assert done_events[0][2]["turn_id"] == "web:turn-1"
    assert done_events[0][2]["runtime_kind"] == "workflow"
    assert all(row[1] != "new_message" for row in events[:-1])
