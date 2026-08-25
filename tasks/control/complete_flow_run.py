"""Commit the single terminal result of a durable one-shot flow run."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from core import FlowFile, TaskError, TaskFactory
from core.agent_contracts import IdempotencyClass
from core.base_task import BaseTask


class CompleteFlowRunTask(BaseTask):
    """Stage and commit one typed terminal for the current durable run."""

    TYPE = "completeFlowRun"
    VERSION = "1.0.0"
    NAME = "Complete Flow Run"
    DESCRIPTION = "Commit the sole terminal result of a durable one-shot flow run"
    ICON = "check-circle"
    RELATIONSHIPS: ClassVar = ["completed", "failure"]
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT

    def set_flow_run_context(self, context, *, store=None, coordinator=None) -> None:
        self._flow_run_context = context
        self._flow_run_store = store
        self._flow_run_coordinator = coordinator

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = getattr(self, "_flow_run_context", None)
        store = getattr(self, "_flow_run_store", None)
        coordinator = getattr(self, "_flow_run_coordinator", None)
        run_id = (
            str(context.get("run_id") or "") if isinstance(context, dict)
            else str(getattr(context, "run_id", "") or ""))
        if not run_id or store is None or coordinator is None:
            raise TaskError("completeFlowRun requires durable_one_shot runtime context")
        artifact_attribute = str(
            self.config.get("artifact_attribute") or "flow.run.artifacts")
        raw_artifacts = flowfile.get_attribute(artifact_attribute, "[]") or "[]"
        try:
            artifacts = json.loads(raw_artifacts)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TaskError("flow run artifacts must be a JSON array") from exc
        summary = str(self.config.get("summary") or "")
        if not summary:
            summary = flowfile.get_content().decode("utf-8", errors="replace")
        terminal = {
            "schema_version": 1,
            "summary": summary,
            "artifacts": artifacts,
            "attributes": {
                str(key): str(value) for key, value in flowfile.attributes.items()
                if str(key).startswith("result.")
            },
        }
        coordinator.finalize(run_id, terminal)
        flowfile.set_attribute("flow.run.id", run_id)
        flowfile.set_attribute("flow.run.status", "completed")
        flowfile.set_attribute("route.relationship", "completed")
        return [flowfile]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "summary": {"type": "string", "required": False},
            "artifact_attribute": {
                "type": "string", "required": False,
                "default": "flow.run.artifacts",
            },
        }


TaskFactory.register(CompleteFlowRunTask)


__all__ = ["CompleteFlowRunTask"]
