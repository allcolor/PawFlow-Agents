"""Checkpoint-safe bounded Repeat Until controller."""

from __future__ import annotations

import copy
import time
from typing import Any, ClassVar

from core import FlowFile, TaskError, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.base_task import BaseTask


class RepeatUntilTask(BaseTask):
    """Execute one isolated child iteration and schedule the next without cycles."""

    TYPE = "repeatUntil"
    VERSION = "1.0.0"
    NAME = "Repeat Until"
    DESCRIPTION = ("Runs one isolated child-flow iteration per invocation, then "
                   "completes, exhausts, cancels, or durably schedules the next.")
    ICON = "repeat"
    RELATIONSHIPS: ClassVar = ["success", "exhausted", "cancelled", "failure"]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.WORKFLOW_EXECUTE,)
    IDEMPOTENCY = IdempotencyClass.RUN_CACHED
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._runtime_context: dict[str, Any] = {}
        self._workflow_runtime: dict[str, Any] = {}

    def set_runtime_context(
        self, *, user_id: str = "", conversation_id: str = "",
        scope: str = "", agent_name: str = "",
    ) -> None:
        self._runtime_context = {
            "user_id": user_id, "conversation_id": conversation_id,
            "scope": scope, "agent_name": agent_name,
        }

    def set_workflow_run_context(self, context, **kwargs: Any) -> None:
        self._workflow_runtime = {
            "workflow_run_context": context,
            "workflow_event_callback": kwargs.get("event_callback"),
            "workflow_terminal_callback": kwargs.get("terminal_callback"),
            "workflow_inbox_store": kwargs.get("inbox_store"),
            "workflow_run_store": kwargs.get("run_store"),
            "workflow_cancel_event": kwargs.get("cancel_event"),
            "workflow_preempt_policy": kwargs.get("preempt_policy"),
            "workflow_visible_through_sequence": kwargs.get(
                "visible_through_sequence"),
        }

    def _route(self, flowfile: FlowFile, relationship: str) -> list[FlowFile]:
        flowfile.set_attribute("repeat.until.status", relationship)
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]

    def _run_iteration(self, flowfile: FlowFile) -> FlowFile:
        from engine import FlowParser
        from engine.continuous_executor import ContinuousFlowExecutor

        body = copy.deepcopy(self.config.get("body"))
        definition = {
            "id": "repeat_until_body", "name": "Repeat Until Body",
            "version": "1.0.0", "tasks": body.get("tasks", {}),
            "relations": body.get("relations", []),
            "entries": body.get("entries", []), "exits": body.get("exits", []),
            "services": {}, "groups": {},
        }
        child = FlowParser.parse(definition)
        child.services = dict(self._services)
        runtime_context = {**self._runtime_context, **self._workflow_runtime}
        result = ContinuousFlowExecutor.run_batch(
            child, input_flowfiles=[flowfile], max_retries=1,
            timeout=float(self.config.get("iteration_timeout_seconds", 30)),
            runtime_context=runtime_context or None,
            suppress_one_shot_roots=True,
        )
        if not result.success:
            raise TaskError("Repeat Until child iteration failed: " + "; ".join(
                str(error) for error in result.errors))
        if len(result.output_flowfiles) != 1:
            raise TaskError("Repeat Until body must produce exactly one FlowFile")
        return result.output_flowfiles[0]

    def _condition_matches(self, flowfile: FlowFile) -> bool:
        from tasks.control.route_on_attribute import RouteOnAttributeTask
        condition = self.config.get("condition")
        evaluator = RouteOnAttributeTask({"routes": {"stop": condition}})
        return evaluator._evaluate_condition(flowfile, condition)

    def _schedule_next(self, flowfile: FlowFile, delay_seconds: float) -> None:
        from core.confirmation_store import ConfirmationStore, find_own_flow_ids
        from core.executor_registry import ExecutorRegistry

        ids = find_own_flow_ids(self)
        if not ids:
            raise TaskError(
                "Repeat Until requires a DEPLOYED continuous flow when another "
                "iteration is needed")
        flowfile.delete_attribute("route.relationship")
        flowfile.delete_attribute("route")
        if delay_seconds > 0:
            wait_id = ConfirmationStore.instance().park_timer(
                instance_id=ids["instance_id"], task_id=ids["task_id"],
                flowfile=flowfile, deadline_at=time.time() + delay_seconds)
            if wait_id is None:
                raise TaskError("Repeat Until continuation deadline was not future")
            flowfile.set_attribute("repeat.until.wait_id", wait_id)
            return
        executor = ExecutorRegistry.get_instance().get(ids["instance_id"])
        if executor is None or not executor.inject(
                flowfile, entry_task_id=ids["task_id"]):
            raise TaskError("Repeat Until continuation could not be queued")

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        from core.confirmation_store import parse_timeout_seconds

        cancel_event = self._workflow_runtime.get("workflow_cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return self._route(flowfile, "cancelled")
        try:
            max_iterations = int(self.config.get("max_iterations"))
            max_duration = float(self.config.get("max_duration_seconds"))
            delay_seconds = parse_timeout_seconds(
                self.config.get("iteration_delay", 0))
        except (TypeError, ValueError) as exc:
            raise TaskError("Repeat Until bounds are invalid") from exc
        if max_iterations < 1 or max_duration <= 0:
            raise TaskError("Repeat Until bounds must be positive")
        started_at = float(
            flowfile.get_attribute("repeat.until.started_at") or time.time())
        iteration = int(flowfile.get_attribute("repeat.until.iteration") or "0") + 1
        flowfile.set_attribute("repeat.until.started_at", str(started_at))
        flowfile.set_attribute("repeat.until.iteration", str(iteration))
        flowfile.delete_attribute("durable.timer.status")
        flowfile.delete_attribute("durable.timer.elapsed_at")
        flowfile.delete_attribute("route.relationship")
        if time.time() - started_at > max_duration:
            return self._route(flowfile, "exhausted")
        output = self._run_iteration(flowfile)
        if self._condition_matches(output):
            return self._route(output, "success")
        if iteration >= max_iterations or time.time() - started_at > max_duration:
            return self._route(output, "exhausted")
        self._schedule_next(output, delay_seconds)
        return []

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "body": {"type": "object", "required": True},
            "condition": {"type": "object", "required": True},
            "max_iterations": {"type": "integer", "required": True},
            "max_duration_seconds": {"type": "number", "required": True},
            "iteration_delay": {"type": "string", "required": False, "default": "0"},
            "iteration_timeout_seconds": {
                "type": "number", "required": False, "default": 30},
        }


TaskFactory.register(RepeatUntilTask)
