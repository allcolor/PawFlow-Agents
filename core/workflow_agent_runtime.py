"""Process-resident experimental runtime for exact-version workflow agents."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core import FlowFile
from core.agent_contracts import AuthorizationRefContract
from core.agent_feature_flags import WORKFLOW_AGENT_RUNTIME_KIND
from core.agent_runtime_router import AgentRunKey
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    AgentWorkflowRequest,
    PreparedAgentTurn,
    WorkflowConversationRef,
    WorkflowInstanceConfig,
    WorkflowLimits,
    WorkflowRequestBody,
    WorkflowRunContext,
    WorkflowTurnRef,
)
from core.workflow_agent_resources import (
    resolve_exact_agent_workflow,
    snapshot_agent_workflow_services,
    validate_agent_workflow_definition,
)

logger = logging.getLogger(__name__)

BOOTSTRAP_WORKFLOW_TASK_TYPES = frozenset({
    "inputPort",
    "outputPort",
    "agentWorkflowInput",
    "emitAgentProgress",
    "workflowFakeLLM",
    "agentLLMCall",
    "completeAgentTurn",
    "receiveAgentMessages",
    "groupDeliberationInput",
    "resolveGroupSnapshot",
    "selectGroupResponders",
    "initializeSharedRoom",
    "agentParticipantCall",
    "synthesizeGroupResult",
})


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def require_single_workflow_terminal(terminals):
    """Return the sole staged result or fail closed on zero/fan-out terminals."""
    if len(terminals) != 1:
        raise RuntimeError(
            f"workflow must stage exactly one terminal; got {len(terminals)}")
    return terminals[0]


@dataclass
class _ActiveRun:
    request: PreparedAgentTurn
    run_id: str
    binding: WorkflowInstanceConfig | None = None
    invocation_mode: str = "conversation"
    parent_invocation: dict[str, Any] | None = None
    publish_to_conversation: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finalized: bool = False
    started_at: float = field(default_factory=time.time)
    status: str = "preparing"


@dataclass(frozen=True)
class _QueuedRun:
    request: PreparedAgentTurn
    binding: WorkflowInstanceConfig | None = None
    invocation_mode: str = "conversation"
    run_id: str = ""
    parent_invocation: dict[str, Any] | None = None
    publish_to_conversation: bool = False

    @property
    def root_turn_id(self) -> str:
        return self.request.root_turn_id


class WorkflowAgentRuntime:
    """Queue-only adapter using one isolated batch executor per turn."""

    runtime_kind = WORKFLOW_AGENT_RUNTIME_KIND
    _instance: WorkflowAgentRuntime | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[AgentRunKey, _ActiveRun] = {}
        self._pending: dict[AgentRunKey, list[_QueuedRun]] = {}
        self._next_generation: dict[AgentRunKey, int] = {}
        self._recover_durable_inbox()

    @staticmethod
    def _recover_durable_inbox() -> None:
        """Repair expired leases and interrupted ingress transitions at boot."""
        try:
            from core.agent_inbox_store import AgentInboxStore
            from core.conversation_store import ConversationStore
            inbox = AgentInboxStore.instance()
            inbox.recover_expired_leases()
            conversation_store = ConversationStore.instance()

            def contains(conversation_id: str, msg_id: str) -> bool:
                return any(
                    str(row.get("msg_id") or "") == msg_id
                    for row in (conversation_store.load(conversation_id) or ()))

            def append(conversation_id: str, agent_name: str,
                       payload: dict[str, Any]) -> None:
                conversation_store.append_message_if_absent(
                    conversation_id, payload, agent_name=agent_name)

            inbox.reconcile_receipts(contains, append)
        except Exception:
            logger.exception("workflow inbox boot reconciliation failed")

    @classmethod
    def instance(cls) -> WorkflowAgentRuntime:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    runtime = cls()
                    cls._instance = runtime
                    runtime._recover_durable_runs()
                    runtime._resume_durable_pending()
        return cls._instance

    def _resume_durable_pending(self) -> None:
        """Launch one recovered pending turn per workflow-agent key."""
        try:
            from core.agent_inbox_store import AgentInboxStore
            from core.conv_agent_config import get_agent_config
            from core.conversation_store import ConversationStore
            inbox = AgentInboxStore.instance()
            conversation_store = ConversationStore.instance()
            for conversation_id, agent_name in inbox.list_ready_keys():
                key = self._key(conversation_id, agent_name)
                with self._lock:
                    if key in self._active:
                        continue
                config = get_agent_config(conversation_id, agent_name)
                if str(config.get("runtime_kind") or "llm") != "workflow":
                    continue
                items = inbox.list_items(
                    conversation_id, agent_name, states=("pending",), limit=1)
                if not items:
                    continue
                user_id = str(
                    conversation_store.resolve_owner(conversation_id) or "")
                self.submit(self._prepared_from_inbox(
                    items[0], user_id=user_id))
        except Exception:
            logger.exception("workflow pending boot resume failed")

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def reserve_generation(self, key: AgentRunKey) -> int:
        key = self._key(key.conversation_id, key.agent_name)
        from core.workflow_run_store import WorkflowRunStore
        return WorkflowRunStore.instance().reserve_generation(
            key.conversation_id, key.agent_name)

    def _recover_durable_runs(self) -> None:
        """Recover committing runs first, then restart the newest live run."""
        from core.workflow_run_store import WorkflowRunStore
        from core.workflow_turn_coordinator import WorkflowTurnCoordinator
        store = WorkflowRunStore.instance()
        WorkflowTurnCoordinator(store).recover_committing()
        recoverable = store.list_recoverable()
        from core.agent_inbox_store import AgentInboxStore
        inbox = AgentInboxStore.instance()
        inbox.recover_orphaned_workflow_claims(
            run["run_id"] for run in recoverable)
        live = tuple(
            run for run in recoverable
            if run["status"] in {"accepted", "running", "cancelling"})
        newest: dict[AgentRunKey, dict[str, Any]] = {}
        for run in live:
            key = self._key(run["conversation_id"], run["agent_name"])
            current = newest.get(key)
            if current is None or run["run_generation"] > current["run_generation"]:
                if current is not None:
                    store.supersede(
                        current["run_id"], "older generation found at recovery")
                newest[key] = run
            else:
                store.supersede(
                    run["run_id"], "older generation found at recovery")
        for run in newest.values():
            if run["status"] == "cancelling":
                store.transition(
                    run["run_id"], "cancelling", "cancelled",
                    "cancel completed during recovery")
                continue
            try:
                self.recover(run["run_id"])
            except Exception as exc:
                logger.exception(
                    "workflow run recovery failed for %s", run["run_id"])
                store.fail(run["run_id"], f"recovery failed: {exc}")
                inbox.release(
                    run["conversation_id"], run["agent_name"], run["run_id"])

    def recover(self, run_id: str):
        """Restart one accepted/running durable record from its stored input."""
        from core.workflow_run_store import WorkflowRunStore
        from core.workflow_turn_coordinator import WorkflowTurnCoordinator
        store = WorkflowRunStore.instance()
        run = store.get_run(run_id)
        if run is None:
            raise KeyError("workflow run does not exist")
        if run["status"] == "committing":
            return WorkflowTurnCoordinator(store).commit(run_id)
        if run["status"] == "completed":
            return run["terminal_event"]
        if run["status"] not in {"accepted", "running"}:
            return None
        request = self._prepared_from_run(run)
        key = self._key(request.conversation_id, request.agent_name)
        with self._lock:
            if key in self._active:
                return {"status": "already_active", "run_id": run_id}
            if not store.reacquire(run_id, 300):
                return None
            active = _ActiveRun(
                request=request,
                run_id=run_id,
                binding=(
                    WorkflowInstanceConfig.from_dict(run["binding"])
                    if run["binding"] else None),
                invocation_mode=str(run["invocation_mode"] or "conversation"),
                parent_invocation=run["parent_invocation"],
                publish_to_conversation=run["publish_to_conversation"],
            )
            self._active[key] = active
            self._start_worker(key, active)
        return {"status": "recovering", "run_id": run_id}

    @staticmethod
    def _prepared_from_run(run: dict[str, Any]) -> PreparedAgentTurn:
        authorization = AuthorizationRefContract.from_dict(
            run["authorization_ref"])
        workflow_request = AgentWorkflowRequest.from_dict(run["request"])
        now = _utc_timestamp(run["created_at"])
        identity = AgentTurnIdentity(
            conversation_id=run["conversation_id"],
            root_conversation_id=run["conversation_id"],
            agent_instance=run["agent_name"],
            turn_id=run["root_turn_id"],
            ingress_msg_id=run["root_turn_id"],
            turn_epoch=max(1, int(run["created_at"] * 1_000_000)),
            run_generation=run["run_generation"],
            authorization_context_id=authorization.context_id,
            authorization_revision_at_start=authorization.revision,
            source_kind="user", created_at=now)
        return PreparedAgentTurn(
            turn_identity=identity,
            conversation_id=run["conversation_id"],
            agent_name=run["agent_name"],
            user_id=run["user_id"],
            root_turn_id=run["root_turn_id"],
            request_message_ids=workflow_request.turn.request_message_ids,
            channel=run["channel"],
            message=workflow_request.request.message,
            attachments=workflow_request.request.attachments,
            source={"type": "user", "authorization": authorization.to_dict()},
            permission_mode=run["permission_mode"],
            authorization_ref=authorization)

    def submit(self, request: PreparedAgentTurn) -> dict[str, Any]:
        return self._submit(_QueuedRun(request=request))

    def submit_bound(
        self,
        request: PreparedAgentTurn,
        binding: WorkflowInstanceConfig,
        *,
        invocation_mode: str,
    ) -> dict[str, Any]:
        """Submit a server-owned exact binding outside conversation dispatch."""
        if binding.flow_ref is None:
            raise ValueError("bound workflow submission requires an exact flow_ref")
        if invocation_mode not in {"automation", "silent_maintenance"}:
            raise ValueError("bound workflow invocation_mode is invalid")
        return self._submit(_QueuedRun(
            request=request,
            binding=binding,
            invocation_mode=invocation_mode,
        ))

    def submit_flow(
        self,
        request: PreparedAgentTurn,
        binding: WorkflowInstanceConfig,
        *,
        parent: dict[str, Any],
        run_id: str,
        publish_to_conversation: bool = False,
    ) -> dict[str, Any]:
        """Submit one idempotent child owned by a durable parent continuation."""
        if binding.flow_ref is None:
            raise ValueError("flow submission requires an exact flow_ref")
        if not str(run_id or "").strip():
            raise ValueError("flow submission requires a stable run_id")
        if not isinstance(parent, dict) or not parent.get("invocation_id"):
            raise ValueError("flow submission requires a parent invocation")
        return self._submit(_QueuedRun(
            request=request,
            binding=binding,
            invocation_mode="flow",
            run_id=str(run_id),
            parent_invocation=dict(parent),
            publish_to_conversation=bool(publish_to_conversation),
        ))

    def _submit(self, submission: _QueuedRun) -> dict[str, Any]:
        request = submission.request
        key = self._key(request.conversation_id, request.agent_name)
        with self._lock:
            if key in self._active:
                requested_run_id = submission.run_id
                if requested_run_id and self._active[key].run_id == requested_run_id:
                    return {
                        "status": "accepted", "queued": False,
                        "run_id": requested_run_id,
                    }
                if requested_run_id:
                    existing = next((
                        item for item in self._pending.get(key, ())
                        if item.run_id == requested_run_id), None)
                    if existing is not None:
                        return {
                            "status": "accepted", "queued": True,
                            "checkpoint": (
                                existing.binding is not None
                                and existing.binding.preempt_policy == "checkpoint"),
                            "run_id": requested_run_id,
                        }
                policy = (
                    submission.binding.preempt_policy
                    if submission.binding is not None
                    else self._preempt_policy(request))
                if policy == "restart":
                    previous = self._active[key]
                    previous.cancel_event.set()
                    active = _ActiveRun(
                        request=request,
                        run_id=submission.run_id or f"wr_{uuid.uuid4().hex}",
                        binding=submission.binding,
                        invocation_mode=submission.invocation_mode,
                        parent_invocation=submission.parent_invocation,
                        publish_to_conversation=submission.publish_to_conversation,
                    )
                    self._active[key] = active
                    try:
                        from core.agent_inbox_store import AgentInboxStore
                        from core.workflow_run_store import WorkflowRunStore
                        WorkflowRunStore.instance().supersede(
                            previous.run_id, "restart preemption")
                        AgentInboxStore.instance().transfer(
                            key.conversation_id, key.agent_name,
                            previous.run_id, active.run_id)
                    except Exception:
                        logger.exception("workflow restart claim transfer failed")
                        self._active[key] = previous
                        previous.cancel_event.clear()
                        raise
                    self._start_worker(key, active)
                    return {
                        "status": "accepted", "queued": False,
                        "restarted": True, "run_id": active.run_id,
                    }
                self._pending.setdefault(key, []).append(submission)
                return {
                    "status": "accepted", "queued": True,
                    "checkpoint": policy == "checkpoint",
                    "run_id": submission.run_id,
                }
            active = _ActiveRun(
                request=request,
                run_id=submission.run_id or f"wr_{uuid.uuid4().hex}",
                binding=submission.binding,
                invocation_mode=submission.invocation_mode,
                parent_invocation=submission.parent_invocation,
                publish_to_conversation=submission.publish_to_conversation,
            )
            self._active[key] = active
            self._start_worker(key, active)
            return {"status": "accepted", "queued": False, "run_id": active.run_id}

    @staticmethod
    def _preempt_policy(request: PreparedAgentTurn) -> str:
        try:
            from core.conv_agent_config import get_agent_config
            config = get_agent_config(
                request.conversation_id, request.agent_name)
            return WorkflowInstanceConfig.from_dict(
                config.get("workflow") or {}).preempt_policy
        except (AttributeError, ImportError, KeyError, TypeError, ValueError):
            return "queue"

    def cancel(self, key: AgentRunKey, reason: str, force: bool) -> bool:
        key = self._key(key.conversation_id, key.agent_name)
        with self._lock:
            active = self._active.get(key)
            if active is None:
                return False
            active.cancel_event.set()
            try:
                from core.workflow_run_store import WorkflowRunStore
                store = WorkflowRunStore.instance()
                if force:
                    store.force_stop(active.run_id, reason or "force_stop")
                else:
                    run = store.get_run(active.run_id)
                    if run is not None and run["status"] == "accepted":
                        store.transition(
                            active.run_id, "accepted", "cancelled",
                            reason or "cancelled")
                    elif run is not None and run["status"] == "running":
                        store.transition(
                            active.run_id, "running", "cancelling",
                            reason or "cancelling")
            except Exception:
                logger.exception("workflow cancel run transition failed")
            if force and self._active.get(key) is active:
                self._active.pop(key, None)
                self._pending.pop(key, None)
            return True

    def cancel_run(self, run_id: str, reason: str, force: bool = True) -> bool:
        """Cancel one exact child run without resolving its agent roster key."""

        with self._lock:
            found = next((
                (key, active) for key, active in self._active.items()
                if active.run_id == run_id
            ), None)
            if found is None:
                return False
            key, active = found
            if active.invocation_mode != "flow":
                return self.cancel(key, reason, force)
            from core.workflow_run_store import WorkflowRunStore
            store = WorkflowRunStore.instance()
            row = store.get_run(run_id)
            if row is None:
                return False
            if row["status"] == "accepted":
                if not store.transition(run_id, "accepted", "running"):
                    return False
                row = store.get_run(run_id)
            if row["status"] != "running":
                return row["status"] in {
                    "cancelled", "force_stopped", "completed"}
            binding = WorkflowInstanceConfig.from_dict(row["binding"])
            context = self._context(active, binding, row)
            from core.workflow_turn_coordinator import WorkflowTurnCoordinator
            terminal = WorkflowTurnCoordinator(store).finalize_status(
                context,
                status="force_stopped" if force else "cancelled",
                reason=reason or ("force_stop" if force else "cancelled"),
            )
            active.cancel_event.set()
            active.finalized = terminal is not None
            if self._active.get(key) is active:
                self._active.pop(key, None)
                self._launch_next_locked(key, user_id=active.request.user_id)
            return True

    def active_snapshot(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return user-visible workflow turns for the Active Agents status API."""
        now = time.time()
        with self._lock:
            return [
                {
                    "agent_name": active.request.agent_name,
                    "task_id": "",
                    "turn_id": active.request.root_turn_id,
                    "workflow_run_id": active.run_id,
                    "iteration": 0,
                    "round": 0,
                    "max_rounds": 0,
                    "last_tool": "",
                    "duration_s": max(0.0, now - active.started_at),
                    "status": active.status,
                    "message_preview": active.request.message[:160],
                    "runtime_kind": WORKFLOW_AGENT_RUNTIME_KIND,
                }
                for key, active in self._active.items()
                if (key.conversation_id == conversation_id
                    and active.invocation_mode == "conversation")
            ]

    def _record_progress(
            self, key: AgentRunKey, active: _ActiveRun,
            data: dict[str, Any]) -> None:
        status = str(data.get("label") or data.get("stage") or "").strip()
        if not status:
            return
        with self._lock:
            if self._active.get(key) is active:
                active.status = status[:160].replace("_", " ")

    @staticmethod
    def _key(conversation_id: str, agent_name: str) -> AgentRunKey:
        return AgentRunKey(conversation_id, str(agent_name or "").casefold())

    def _start_worker(self, key: AgentRunKey, active: _ActiveRun) -> None:
        threading.Thread(
            target=self._run, args=(key, active), daemon=True,
            name=f"workflow-agent-{key.conversation_id[:8]}-{active.run_id[-8:]}",
        ).start()

    def _launch_next_locked(self, key: AgentRunKey, *,
                            user_id: str = "",
                            exclude_msg_ids=()) -> None:
        pending = self._pending.get(key) or []
        if not pending:
            self._pending.pop(key, None)
            try:
                from core.agent_inbox_store import AgentInboxStore
                excluded = {str(value) for value in exclude_msg_ids}
                items = AgentInboxStore.instance().list_items(
                    key.conversation_id, key.agent_name,
                    states=("pending",), limit=20)
                item = next(
                    (value for value in items
                     if value.msg_id not in excluded), None)
            except Exception:
                logger.exception("workflow final inbox drain failed")
                item = None
            if item is None:
                return
            submission = _QueuedRun(
                request=self._prepared_from_inbox(item, user_id=user_id))
        else:
            submission = pending.pop(0)
            if not pending:
                self._pending.pop(key, None)
        active = _ActiveRun(
            request=submission.request,
            run_id=submission.run_id or f"wr_{uuid.uuid4().hex}",
            binding=submission.binding,
            invocation_mode=submission.invocation_mode,
            parent_invocation=submission.parent_invocation,
            publish_to_conversation=submission.publish_to_conversation,
        )
        self._active[key] = active
        self._start_worker(key, active)

    def _prepared_from_inbox(self, item, *, user_id: str) -> PreparedAgentTurn:
        payload = dict(item.payload)
        source = dict(payload.get("source") or {})
        return prepare_workflow_turn(
            conversation_id=item.conversation_id,
            agent_name=item.agent_key,
            user_id=user_id or str(source.get("name") or "workflow-recovery"),
            message=str(payload.get("content") or ""),
            attachments=list(payload.get("attachments") or ()),
            message_id=item.msg_id,
            channel=str(payload.get("channel") or "web"),
            permission_mode="default",
            source=source,
            runtime=self,
        )

    def _is_current(self, key: AgentRunKey, active: _ActiveRun) -> bool:
        with self._lock:
            return self._active.get(key) is active and not active.cancel_event.is_set()

    def _run(self, key: AgentRunKey, active: _ActiveRun) -> None:
        request = active.request
        run_store = None
        try:
            if not self._is_current(key, active):
                return
            from core.workflow_run_store import WorkflowRunStore
            run_store = WorkflowRunStore.instance()
            stored = run_store.get_run(active.run_id)
            if stored is not None and stored["binding"]:
                binding = WorkflowInstanceConfig.from_dict(stored["binding"])
            elif active.binding is not None:
                binding = active.binding
            else:
                # Legacy accepted/running rows can predate the binding snapshot.
                # They may use the current ports only when the exact stored flow
                # identity still matches; all newly-created rows are self-contained.
                from core.conv_agent_config import get_agent_config
                config = get_agent_config(
                    request.conversation_id, request.agent_name)
                binding = WorkflowInstanceConfig.from_dict(
                    config.get("workflow") or {})
            context = self._context(active, binding)
            workflow_request = AgentWorkflowRequest(
                request=WorkflowRequestBody(
                    message=request.message, attachments=request.attachments),
                conversation=WorkflowConversationRef(
                    id=request.conversation_id, agent=request.agent_name),
                turn=WorkflowTurnRef(
                    root_turn_id=request.root_turn_id,
                    request_message_ids=request.request_message_ids),
                parameters=binding.parameters,
            )
            if stored is None:
                stored = run_store.create_run(
                    context=context, request=workflow_request,
                    parameters=binding.parameters,
                    lease_seconds=binding.limits.max_duration_seconds + 60,
                    binding=binding.to_dict(),
                    parent_invocation=active.parent_invocation,
                    publish_to_conversation=active.publish_to_conversation)
            if stored["status"] == "accepted":
                if not run_store.transition(
                        active.run_id, "accepted", "running"):
                    raise RuntimeError("workflow start CAS lost")
                stored = run_store.get_run(active.run_id)
            elif stored["status"] == "committing":
                from core.workflow_turn_coordinator import WorkflowTurnCoordinator
                event = WorkflowTurnCoordinator(run_store).commit(active.run_id)
                active.finalized = event is not None
                return
            elif stored["status"] != "running":
                return
            if stored["flow_ref"] != binding.flow_ref.to_dict():
                raise ValueError("stored workflow identity differs from binding")
            context = self._context(active, binding, stored)
            run_parameters = dict(stored["parameters"])
            workflow_request = AgentWorkflowRequest(
                request=workflow_request.request,
                conversation=workflow_request.conversation,
                turn=workflow_request.turn,
                parameters=run_parameters,
            )
            run_store.append_event(active.run_id, "started", {
                "turn_id": request.root_turn_id,
                "recovery_count": stored["recovery_count"],
            })
            resolved = resolve_exact_agent_workflow(
                binding.flow_fqn, request.user_id, request.conversation_id)
            if resolved.ref != binding.flow_ref:
                raise ValueError("pinned workflow identity or digest changed")
            from tasks import register_all_tasks
            register_all_tasks()
            report = validate_agent_workflow_definition(resolved.definition)
            if not report["ok"]:
                raise ValueError("pinned workflow definition is invalid")
            if not stored["service_snapshot"]:
                service_snapshot = snapshot_agent_workflow_services(
                    binding, resolved.definition, request.user_id,
                    request.conversation_id)
                group_contract = resolved.definition.get("group_contract")
                if group_contract is not None:
                    if group_contract != {"version": 1, "parameter": "group_name"}:
                        raise ValueError("group workflow contract is invalid")
                    from core.agent_group_resources import snapshot_bound_agent_group
                    group_name = str(binding.parameters.get("group_name") or "")
                    group_snapshot = snapshot_bound_agent_group(
                        group_name, request.user_id, request.conversation_id)
                    service_snapshot["agent_group"] = group_snapshot["agent_group"]
                    service_snapshot["services"].update(
                        group_snapshot["services"])
                stored = run_store.set_service_snapshot(
                    active.run_id,
                    service_snapshot)
                context = self._context(active, binding, stored)
            from core.agent_inbox_store import AgentInboxStore
            inbox = AgentInboxStore.instance()
            visible_through_sequence = inbox.latest_sequence(
                request.conversation_id, request.agent_name)
            _root_claim, root_items = inbox.claim(
                request.conversation_id, request.agent_name,
                active.run_id, "__workflow_input__", max_messages=1,
                lease_seconds=binding.limits.max_duration_seconds + 60,
                include_msg_ids=request.request_message_ids)
            run_store.record_claimed_ids(
                active.run_id, [item.msg_id for item in root_items])
            attributes = self._reserved_attributes(context)
            terminals = []
            terminal_lock = threading.Lock()

            def stage_terminal(result) -> None:
                with terminal_lock:
                    terminals.append(result)

            def emit(event_type: str, data: dict[str, Any]) -> None:
                if not active.cancel_event.is_set():
                    if event_type == "workflow_progress":
                        self._record_progress(key, active, data)
                    run_store.append_event(
                        active.run_id,
                        "progress" if event_type == "workflow_progress"
                        else event_type,
                        data)
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        request.conversation_id, event_type, data)

            emit("workflow_progress", {
                "turn_id": request.root_turn_id,
                "run_id": active.run_id,
                "agent_name": request.agent_name,
                "flow_fqn": binding.flow_fqn,
                "stage": "started",
            })
            from core.relay_bindings import get_default
            from engine.continuous_executor import ContinuousFlowExecutor
            from engine.parser import FlowParser
            relay_id = str(get_default(
                request.conversation_id, agent=request.agent_name) or "")
            flow = FlowParser.parse(resolved.definition)
            execution = ContinuousFlowExecutor.run_batch(
                flow,
                input_flowfiles=[FlowFile(
                    content=json.dumps(
                        workflow_request.to_dict(), ensure_ascii=False
                    ).encode("utf-8"),
                    attributes=attributes,
                )],
                parameters=run_parameters,
                max_workers=min(4, binding.limits.max_fanout),
                max_retries=3,
                timeout=binding.limits.max_duration_seconds,
                runtime_context={
                    "user_id": request.user_id,
                    "conversation_id": request.conversation_id,
                    "scope": binding.flow_scope,
                    "agent_name": request.agent_name,
                    "workflow_run_context": context,
                    "workflow_event_callback": emit,
                    "workflow_terminal_callback": stage_terminal,
                    "workflow_reserved_attributes": attributes,
                    "workflow_inbox_store": inbox,
                    "workflow_run_store": run_store,
                    "workflow_cancel_event": active.cancel_event,
                    "workflow_preempt_policy": binding.preempt_policy,
                    "workflow_visible_through_sequence": visible_through_sequence,
                    "workflow_allowed_effects": [
                        effect.value for effect in binding.allowed_effects],
                    "workflow_allowed_relay_ids": (
                        [relay_id] if relay_id else []),
                    "workflow_resource_roots": [],
                },
                entry_task_id=binding.input_port,
                suppress_one_shot_roots=True,
            )
            if not self._is_current(key, active):
                return
            discarded_errors = list(
                execution.statistics.get("discarded_flowfile_errors") or [])
            if not execution.success or discarded_errors:
                raise RuntimeError(
                    "workflow execution failed: " + json.dumps(
                        [*execution.errors, *discarded_errors]))
            terminal = require_single_workflow_terminal(terminals)
            self._finalize(key, active, context, terminal)
        except Exception as exc:
            if self._is_current(key, active):
                logger.exception("workflow agent turn failed")
                committing = False
                if run_store is not None:
                    stored = run_store.get_run(active.run_id)
                    committing = bool(
                        stored and stored["status"] == "committing")
                    if not committing:
                        if active.invocation_mode == "flow" and stored:
                            from core.workflow_turn_coordinator import (
                                WorkflowTurnCoordinator,
                            )
                            lowered = str(exc).casefold()
                            status = (
                                "budget_exceeded"
                                if type(exc).__name__ == "WorkflowBudgetExceeded"
                                else (
                                    "timed_out"
                                    if "timeout" in lowered or "timed out" in lowered
                                    else "failed"
                                )
                            )
                            context = self._context(active, binding, stored)
                            terminal = WorkflowTurnCoordinator(
                                run_store).finalize_status(
                                    context, status=status, reason=str(exc))
                            active.finalized = terminal is not None
                            committing = not active.finalized
                        else:
                            run_store.fail(active.run_id, str(exc))
                if not committing:
                    if active.invocation_mode != "flow":
                        self._publish_error(active, str(exc))
        finally:
            committing = False
            if run_store is not None:
                stored = run_store.get_run(active.run_id)
                committing = bool(stored and stored["status"] == "committing")
                if stored and stored["status"] == "cancelling":
                    run_store.transition(
                        active.run_id, "cancelling", "cancelled",
                        "worker cancelled")
            if not active.finalized and not committing:
                try:
                    from core.agent_inbox_store import AgentInboxStore
                    AgentInboxStore.instance().release(
                        request.conversation_id, request.agent_name,
                        active.run_id)
                except Exception:
                    logger.exception("workflow inbox claim release failed")
            with self._lock:
                if self._active.get(key) is active:
                    self._active.pop(key, None)
                    if not committing:
                        self._launch_next_locked(
                            key, user_id=request.user_id,
                            exclude_msg_ids=(
                                () if active.finalized
                                else request.request_message_ids))

    @staticmethod
    def _context(active: _ActiveRun, binding: WorkflowInstanceConfig,
                 stored: dict[str, Any] | None = None) -> WorkflowRunContext:
        request = active.request
        parent = (
            stored["parent_invocation"]
            if stored is not None else active.parent_invocation)
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=binding.limits.max_duration_seconds)
        return WorkflowRunContext(
            run_id=active.run_id,
            turn_identity=request.turn_identity,
            conversation_id=request.conversation_id,
            agent_name=request.agent_name,
            user_id=request.user_id,
            root_turn_id=request.root_turn_id,
            run_generation=request.turn_identity.run_generation,
            flow_ref=binding.flow_ref,
            channel=request.channel,
            invocation_mode=active.invocation_mode,
            permission_mode=request.permission_mode,
            authorization_ref=request.authorization_ref,
            deadline_at=(
                stored["deadline_at"] if stored is not None
                else deadline.isoformat()),
            limits=(
                WorkflowLimits.from_dict(stored["limits"])
                if stored is not None else binding.limits),
            service_snapshot=(
                dict(stored["service_snapshot"])
                if stored is not None else {}),
            cancel_token=f"cancel:{active.run_id}",
            event_sink=f"conversation:{request.conversation_id}",
            parent_invocation=(
                dict(parent) if parent is not None else None),
            publish_to_conversation=(
                bool(stored["publish_to_conversation"])
                if stored is not None else active.publish_to_conversation),
            invocation_depth=int(
                (parent or {}).get("invocation_depth") or 0),
            ancestor_agent_refs=tuple(
                ResourceRef.from_dict(value)
                for value in (parent or {}).get("ancestor_agent_refs", ())),
            ancestor_flow_refs=tuple(
                ResourceRef.from_dict(value)
                for value in (parent or {}).get("ancestor_flow_refs", ())),
        )

    @staticmethod
    def _reserved_attributes(context: WorkflowRunContext) -> dict[str, str]:
        return {
            "workflow.run_id": context.run_id,
            "workflow.generation": str(context.run_generation),
            "workflow.flow_fqn": context.flow_ref.name,
            "workflow.root_turn_id": context.root_turn_id,
            "workflow.principal": context.user_id,
            "workflow.authorization_context_id": context.authorization_ref.context_id,
            "workflow.authorization_revision": str(context.authorization_ref.revision),
        }

    def _finalize(self, key, active, context, result) -> None:
        if not self._is_current(key, active):
            return
        from core.workflow_turn_coordinator import WorkflowTurnCoordinator
        terminal = WorkflowTurnCoordinator().finalize(context, result)
        if terminal is None:
            return
        answered = set(result.answered_turn_ids)
        with self._lock:
            queued = self._pending.get(key) or []
            if queued:
                remaining = [
                    submission for submission in queued
                    if submission.root_turn_id not in answered]
                if remaining:
                    self._pending[key] = remaining
                else:
                    self._pending.pop(key, None)
        active.finalized = True

    @staticmethod
    def _publish_error(active: _ActiveRun, message: str) -> None:
        request = active.request
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            request.conversation_id, "error_event", {
                "turn_id": request.root_turn_id,
                "run_id": active.run_id,
                "runtime_kind": "workflow",
                "agent_name": request.agent_name,
                "channel": request.channel,
                "message": message,
                "finish_reason": "error",
            })


def prepare_workflow_turn(
    *,
    conversation_id: str,
    agent_name: str,
    user_id: str,
    message: str,
    attachments: list[dict[str, Any]],
    message_id: str,
    channel: str,
    permission_mode: str,
    source: dict[str, Any],
    runtime: WorkflowAgentRuntime | None = None,
) -> PreparedAgentTurn:
    """Build the immutable WP3 ingress contract from a stamped user row."""
    authorization = AuthorizationRefContract.from_dict(
        source.get("authorization") or {})
    key = WorkflowAgentRuntime._key(conversation_id, agent_name)
    generation = (runtime or WorkflowAgentRuntime.instance()).reserve_generation(key)
    now = datetime.now(timezone.utc).isoformat()
    identity = AgentTurnIdentity(
        conversation_id=conversation_id,
        root_conversation_id=conversation_id,
        agent_instance=agent_name,
        turn_id=message_id,
        ingress_msg_id=message_id,
        turn_epoch=max(1, int(datetime.now(timezone.utc).timestamp() * 1_000_000)),
        run_generation=generation,
        authorization_context_id=authorization.context_id,
        authorization_revision_at_start=authorization.revision,
        source_kind="a2a" if source.get("type") == "a2a" else "user",
        source_id=str(source.get("name") or "") or None,
        created_at=now,
    )
    return PreparedAgentTurn(
        turn_identity=identity,
        conversation_id=conversation_id,
        agent_name=agent_name,
        user_id=user_id,
        root_turn_id=message_id,
        request_message_ids=(message_id,),
        channel=channel,
        message=message or "[attachment-only message]",
        attachments=tuple(attachments or ()),
        source=dict(source),
        permission_mode=permission_mode or "default",
        authorization_ref=authorization,
    )


__all__ = [
    "BOOTSTRAP_WORKFLOW_TASK_TYPES",
    "WorkflowAgentRuntime",
    "prepare_workflow_turn",
    "require_single_workflow_terminal",
]
