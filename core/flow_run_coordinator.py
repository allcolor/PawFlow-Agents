"""Coordinator for durable one-shot executor lifecycle and terminal commit."""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

from core import FlowFile
from core.flow_run_authorization import (
    FlowExecutionAuthority,
    FlowRunTaskAuthorizationContext,
)
from core.flow_run_store import FLOW_RUN_TERMINALS, FlowRunStore

logger = logging.getLogger(__name__)


class FlowRunCoordinator:
    """Bind one stored run to one uniquely identified continuous executor."""

    def __init__(self, store: FlowRunStore | None = None) -> None:
        self.store = store or FlowRunStore.instance()

    def attach_and_start(
        self, run_id: str, executor, *, entry_task_id: str = "",
        inject_input: bool = True,
    ):
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "created":
            run = self.store.transition(run_id, "starting")
        if run["status"] != "starting":
            raise ValueError("flow run must be created or starting")
        context = {
            "run_id": run_id,
            "generation": run["generation"],
            "flow_ref": copy.deepcopy(run["flow_ref"]),
            "authorization_ref": copy.deepcopy(run["authorization_ref"]),
        }
        authority = FlowExecutionAuthority.from_dict(
            run.get("execution_authority"))
        workflow_context = FlowRunTaskAuthorizationContext.from_run(run)
        relay_snapshot = dict(authority.service_snapshot.get("relay") or {})
        executor._runtime_context.update({
            "user_id": run["user_id"],
            "conversation_id": run["conversation_id"],
            "scope": "conversation",
            "agent_name": authority.agent_name,
            "workflow_run_context": workflow_context,
            "workflow_allowed_effects": tuple(
                effect.value for effect in authority.allowed_effects),
            "workflow_allowed_relay_ids": tuple(
                relay_snapshot.get("candidates") or ()),
            "workflow_resource_roots": tuple(
                authority.service_snapshot.get("resource_roots") or ()),
            "flow_run_context": context,
            "flow_run_store": self.store,
            "flow_run_coordinator": self,
        })
        executor._instance_id = run["deployment_instance_id"]
        for task_id, task in executor._tasks.items():
            executor._inject_runtime_context(task, task_id=task_id)
        from core.executor_registry import ExecutorRegistry
        ExecutorRegistry.get_instance().register(run["deployment_instance_id"], executor)
        if inject_input:
            snapshot = run["input"] or {}
            flowfile = FlowFile(
                content=str(snapshot.get("content") or "").encode(),
                attributes=copy.deepcopy(snapshot.get("attributes") or {}),
            )
            flowfile.set_attribute("flow.run.id", run_id)
            executor.inject(flowfile, entry_task_id=entry_task_id or None)
        self.store.transition(run_id, "running")
        executor.start()
        return executor

    def finalize(self, run_id: str, terminal: dict[str, Any]) -> dict[str, Any]:
        self.store.stage_terminal(run_id, terminal)
        run = self.store.commit(run_id)
        self.deliver_pending_events()
        return run

    def deliver_pending_events(self, proposal_store=None) -> int:
        """Project terminal outbox events into linked proposal state once."""
        if proposal_store is None:
            from core.workflow_proposal_store import WorkflowProposalStore
            proposal_store = WorkflowProposalStore.instance()
        delivered = 0
        for event in self.store.pending_events():
            try:
                run = self.store.get(str(event.get("run_id") or ""))
                if run is None:
                    continue
                proposal_id = str(run.get("proposal_id") or "")
                if proposal_id:
                    status = str(event.get("status") or run.get("status") or "")
                    if status == "force_stopped":
                        status = "cancelled"
                    elif status == "timed_out":
                        status = "failed"
                    proposal = proposal_store.mark_run_status(
                        proposal_id, run_id=run["run_id"], status=status)
                    from core.workflow_proposal_notifications import (
                        publish_proposal_update,
                    )
                    publish_proposal_update(proposal)
                self.store.acknowledge_event(str(event["event_id"]))
                delivered += 1
            except Exception:
                logger.exception(
                    "Flow run terminal event %s remains pending",
                    event.get("event_id"))
        return delivered

    def cancel(self, run_id: str, *, force: bool = False,
               reason: str = "cancelled") -> dict[str, Any]:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in FLOW_RUN_TERMINALS:
            return run
        target = "force_stopped" if force else "cancelled"
        if force:
            run = self.store.transition(run_id, target, reason)
        else:
            if run["status"] != "cancelling":
                self.store.transition(run_id, "cancelling", reason)
            run = self.store.transition(run_id, target, reason)
        from core.executor_registry import ExecutorRegistry
        registry = ExecutorRegistry.get_instance()
        executor = registry.get(run["deployment_instance_id"])
        if executor is not None:
            executor.stop()
            registry.unregister(run["deployment_instance_id"])
        self.deliver_pending_events()
        return self.store.get(run_id)

    def recover(self, run_id: str, executor_factory: Callable[[dict[str, Any]], Any]):
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "committing":
            return self.store.commit(run_id)
        if run["status"] not in {"starting", "running", "waiting"}:
            raise ValueError("flow run is not recoverable")
        recovered = self.store.mark_recovered(run_id)
        return self.attach_and_start(
            run_id, executor_factory(recovered), inject_input=False)

    def replay(
        self, run_id: str, *, authorization_ref: dict[str, Any],
        flow_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        original = self.store.get(run_id)
        if original is None:
            raise KeyError(run_id)
        if original["status"] not in FLOW_RUN_TERMINALS:
            raise ValueError("only a terminal flow run can be replayed")
        exact_ref = flow_ref or original["flow_ref"]
        if exact_ref != original["flow_ref"]:
            raise ValueError("replay must use the same exact flow version")
        return self.store.create(
            user_id=original["user_id"],
            conversation_id=original["conversation_id"],
            flow_ref=exact_ref,
            authorization_ref=authorization_ref,
            execution_authority=copy.deepcopy(
                original["execution_authority"]),
            input_snapshot=copy.deepcopy(original["input"]),
            parameters=copy.deepcopy(original["parameters"]),
            proposal_id=original.get("proposal_id") or "",
            parent_invocation=copy.deepcopy(original.get("parent_invocation")),
            replay_of=run_id,
        )


__all__ = ["FlowRunCoordinator"]
