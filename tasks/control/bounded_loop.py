"""Bounded loop guard shared by declarative collection and loop composites."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from core import FlowFile, TaskError, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.base_task import BaseTask


class BoundedLoopGuardTask(BaseTask):
    """Route one iteration only while duration, size, and cancellation permit it."""

    TYPE = "boundedLoopGuard"
    VERSION = "1.0.0"
    NAME = "Bounded Loop Guard"
    DESCRIPTION = ("Enforces collection size, aggregate duration, and runtime "
                   "cancellation before a declarative loop iteration runs.")
    ICON = "shield"
    RELATIONSHIPS: ClassVar = ["continue", "exhausted", "cancelled", "failure"]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"

    def set_workflow_run_context(
        self, context, *, cancel_event=None, **_kwargs: Any,
    ) -> None:
        self._workflow_run_context = context
        self._workflow_cancel_event = cancel_event

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        cancel_event = getattr(self, "_workflow_cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            relationship = "cancelled"
        else:
            started_attribute = str(
                self.config.get("started_at_attribute") or "fragment.started_at")
            count_attribute = str(
                self.config.get("count_attribute") or "fragment.count")
            try:
                started_at = float(flowfile.get_attribute(started_attribute) or "")
                count = int(flowfile.get_attribute(count_attribute) or "")
                max_duration = float(self.config.get("max_duration_seconds"))
                max_flowfiles = int(self.config.get("max_flowfiles"))
            except (TypeError, ValueError) as exc:
                raise TaskError("boundedLoopGuard requires valid bounds metadata") from exc
            if max_duration < 0 or max_flowfiles < 0:
                raise TaskError("boundedLoopGuard bounds must be non-negative")
            relationship = (
                "exhausted"
                if ((max_flowfiles > 0 and count > max_flowfiles)
                    or (max_duration > 0
                        and time.time() - started_at > max_duration))
                else "continue"
            )
        flowfile.set_attribute("route.relationship", relationship)
        return [flowfile]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "max_duration_seconds": {
                "type": "number", "required": False, "default": 0,
                "description": "Maximum wall-clock duration for the wave; 0 means unlimited",
            },
            "max_flowfiles": {
                "type": "integer", "required": False, "default": 0,
                "description": "Maximum FlowFiles in the correlated wave; 0 means unlimited",
            },
            "started_at_attribute": {
                "type": "string", "required": False,
                "default": "fragment.started_at",
            },
            "count_attribute": {
                "type": "string", "required": False,
                "default": "fragment.count",
            },
        }


TaskFactory.register(BoundedLoopGuardTask)
