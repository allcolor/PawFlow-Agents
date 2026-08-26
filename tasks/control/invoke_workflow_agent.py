"""Invoke an exact Workflow Agent resource from a deployed flow."""

from __future__ import annotations

import hashlib
from typing import Any, ClassVar

from core import FlowFile, TaskError, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.base_task import BaseTask
from core.confirmation_store import find_own_flow_ids, parse_timeout_seconds
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    AgentDefinitionRuntimeDefaults,
    WorkflowInstanceConfig,
)
from core.workflow_agent_invocation import (
    WorkflowParentInvocationStore,
    validate_flow_invocation_ancestry,
)
from core.workflow_agent_resources import bind_agent_workflow
from core.workflow_agent_runtime import WorkflowAgentRuntime, prepare_workflow_turn


def resolve_workflow_agent_binding(
    *,
    agent_ref: ResourceRef,
    user_id: str,
    conversation_id: str,
    parameters: dict[str, Any],
) -> tuple[ResourceRef, WorkflowInstanceConfig]:
    """Resolve an exact visible agent and bind its exact Workflow runtime."""

    if agent_ref.resource_type != "agent":
        raise ValueError("agent_ref must identify an agent")
    from core.agent_group_resources import resolve_agent_resource

    resolved = resolve_agent_resource(
        agent_ref.name, user_id, conversation_id)
    if resolved.ref != agent_ref:
        raise ValueError("agent_ref is stale or resolves to different content")
    defaults = AgentDefinitionRuntimeDefaults.from_dict(
        resolved.definition.get("runtime_defaults") or {})
    workflow = dict(defaults.workflow)
    base_parameters = dict(workflow.get("parameters") or {})
    base_parameters.update(parameters)
    workflow["parameters"] = base_parameters
    binding = WorkflowInstanceConfig.from_dict(bind_agent_workflow(
        workflow, user_id, conversation_id))
    return resolved.ref, binding


def _stable_ids(instance_id: str, task_id: str,
                process_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{instance_id}\0{task_id}\0{process_id}".encode("utf-8")).hexdigest()
    return f"wfi_{digest}", f"wr_flow_{digest}"


class InvokeWorkflowAgentTask(BaseTask):
    """Submit one exact Workflow Agent and durably resume this FlowFile."""

    TYPE = "invokeWorkflowAgent"
    VERSION = "1.0.0"
    NAME = "Invoke Workflow Agent"
    DESCRIPTION = (
        "Invokes an exact Workflow Agent resource through its durable runtime. "
        "By default the parent FlowFile is parked and resumes exactly once with "
        "the child response, artifacts, metrics, and terminal relationship.")
    ICON = "bot"
    RELATIONSHIPS: ClassVar = [
        "submitted", "completed", "no_change", "failed", "cancelled",
        "timed_out", "superseded", "budget_exceeded", "force_stopped",
        "failure",
    ]
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.WORKFLOW_EXECUTE,)
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"

    def set_runtime_context(
        self, user_id: str = "", conversation_id: str = "",
        scope: str = "", agent_name: str = "",
    ) -> None:
        self._runtime_user_id = user_id
        self._runtime_conversation_id = conversation_id
        self._runtime_scope = scope
        self._runtime_agent_name = agent_name

    def set_workflow_run_context(self, context, **_kwargs: Any) -> None:
        self._workflow_run_context = context

    def set_flow_run_context(self, context, **_kwargs: Any) -> None:
        """Accept the distinct durable one-shot FlowRun context."""

        self._flow_run_context = context

    def workflow_authorization_target(self, _flowfile: FlowFile) -> dict[str, Any]:
        return {
            "user_id": getattr(self, "_runtime_user_id", ""),
            "conversation_id": getattr(
                self, "_runtime_conversation_id", ""),
            "scope": getattr(self, "_runtime_scope", ""),
        }

    def _authorization_ref(self):
        context = getattr(self, "_workflow_run_context", None)
        if context is not None:
            return context.authorization_ref
        flow_context = getattr(self, "_flow_run_context", None)
        if isinstance(flow_context, dict):
            from core.authorization_context import AuthorizationRef

            return AuthorizationRef.from_dict(
                flow_context.get("authorization_ref"))
        conversation_id = getattr(self, "_runtime_conversation_id", "")
        agent_name = getattr(self, "_runtime_agent_name", "")
        if not conversation_id or not agent_name:
            return None
        from core.authorization_context import active_authority_ref
        return active_authority_ref(conversation_id, agent_name)

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        if flowfile.get_attribute("workflow.agent.status", ""):
            return [flowfile]

        user_id = getattr(self, "_runtime_user_id", "")
        conversation_id = getattr(self, "_runtime_conversation_id", "")
        if not user_id or not conversation_id:
            raise TaskError(
                "invokeWorkflowAgent requires injected user and conversation context")
        ids = find_own_flow_ids(self)
        if not ids:
            raise TaskError(
                "invokeWorkflowAgent requires a deployed continuous flow")
        authorization = self._authorization_ref()
        if authorization is None:
            raise TaskError(
                "invokeWorkflowAgent requires an active authorization lineage")

        try:
            agent_ref = ResourceRef.from_dict(self.config.get("agent_ref") or {})
        except (TypeError, ValueError) as exc:
            raise TaskError(f"Invalid exact agent_ref: {exc}") from exc
        parameters = self.config.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise TaskError("parameters must be an object")
        try:
            resolved_agent_ref, binding = resolve_workflow_agent_binding(
                agent_ref=agent_ref,
                user_id=user_id,
                conversation_id=conversation_id,
                parameters=dict(parameters),
            )
            timeout_value = self.config.get("terminal_timeout")
            timeout = None
            if timeout_value not in (None, ""):
                timeout = parse_timeout_seconds(timeout_value)
                if timeout <= 0:
                    raise ValueError("terminal_timeout must be positive")
        except (TypeError, ValueError) as exc:
            raise TaskError(f"Invalid Workflow Agent invocation: {exc}") from exc

        if timeout is not None:
            timeout_seconds = max(1, int(timeout))
            limits = binding.limits.model_copy(
                update={"max_duration_seconds": timeout_seconds})
            binding = binding.model_copy(update={"limits": limits})

        parent_context = (
            getattr(self, "_workflow_run_context", None)
            or getattr(self, "_flow_run_context", None))
        if isinstance(parent_context, dict):
            depth = int(parent_context.get("invocation_depth", 0) or 0) + 1
            ancestor_agents = tuple(
                ResourceRef.from_dict(value)
                for value in parent_context.get("ancestor_agent_refs", ()) or ())
            ancestor_flows = tuple(
                ResourceRef.from_dict(value)
                for value in parent_context.get("ancestor_flow_refs", ()) or ())
            parent_flow_ref = parent_context.get("flow_ref")
            if not ancestor_flows and isinstance(parent_flow_ref, dict):
                ancestor_flows = (ResourceRef.from_dict(parent_flow_ref),)
            parent_flow_run_id = str(parent_context.get("run_id") or "")
            permission_mode = str(
                parent_context.get("permission_mode") or "default")
        else:
            depth = int(getattr(parent_context, "invocation_depth", 0) or 0) + 1
            ancestor_agents = tuple(
                getattr(parent_context, "ancestor_agent_refs", ()) or ())
            ancestor_flows = tuple(
                getattr(parent_context, "ancestor_flow_refs", ()) or ())
            parent_flow_run_id = str(
                getattr(parent_context, "run_id", "") or "")
            permission_mode = str(
                getattr(parent_context, "permission_mode", "") or "default")
        try:
            validate_flow_invocation_ancestry(
                agent_ref=resolved_agent_ref,
                flow_ref=binding.flow_ref,
                invocation_depth=depth,
                ancestor_agent_refs=ancestor_agents,
                ancestor_flow_refs=ancestor_flows,
            )
        except ValueError as exc:
            raise TaskError(str(exc)) from exc

        message = str(self.config.get("message") or "").strip()
        if not message:
            raise TaskError("message is required")
        attachments = self.config.get("attachments") or []
        if not isinstance(attachments, list):
            raise TaskError("attachments must be an array")
        cancellation_policy = str(
            self.config.get("cancellation_policy") or "propagate")
        if cancellation_policy not in {"propagate", "detach"}:
            raise TaskError("cancellation_policy must be propagate or detach")
        await_terminal = bool(self.config.get("await_terminal", True))
        publish = bool(self.config.get("publish_to_conversation", False))

        invocation_id, child_run_id = _stable_ids(
            ids["instance_id"], ids["task_id"], flowfile.process_id)
        parent = {
            "schema_version": 1,
            "invocation_id": invocation_id,
            "instance_id": ids["instance_id"],
            "task_id": ids["task_id"],
            "flowfile_process_id": flowfile.process_id,
            "parent_flow_run_id": parent_flow_run_id,
            "authorization_ref": authorization.to_dict(),
            "invocation_depth": depth,
            "ancestor_agent_refs": [
                value.to_dict() for value in (*ancestor_agents, resolved_agent_ref)],
            "ancestor_flow_refs": [
                value.to_dict() for value in (*ancestor_flows, binding.flow_ref)],
            "cancellation_policy": cancellation_policy,
            "publish_to_conversation": publish,
            "await_terminal": await_terminal,
        }
        store = WorkflowParentInvocationStore.instance()
        store.create(parent=parent, flowfile=flowfile)
        flowfile.set_attribute("workflow.agent.invocation_id", invocation_id)
        flowfile.set_attribute("workflow.agent.run_id", child_run_id)

        runtime = WorkflowAgentRuntime.instance()
        request = prepare_workflow_turn(
            conversation_id=conversation_id,
            agent_name=resolved_agent_ref.name,
            user_id=user_id,
            message=message,
            attachments=attachments,
            message_id=authorization.root_turn_id,
            channel="flow",
            permission_mode=permission_mode,
            source={
                "type": "task",
                "name": ids["task_id"],
                "authorization": authorization.to_dict(),
            },
            runtime=runtime,
        )
        acknowledgement = runtime.submit_flow(
            request,
            binding,
            parent=parent,
            run_id=child_run_id,
            publish_to_conversation=publish,
        )
        acknowledged_run_id = str(acknowledgement.get("run_id") or "")
        if acknowledged_run_id != child_run_id:
            raise TaskError("Workflow Agent runtime changed the stable child run_id")
        store.bind_child(invocation_id, child_run_id)
        if not await_terminal:
            store.detach(invocation_id)
            flowfile.set_attribute("workflow.agent.status", "submitted")
            flowfile.set_attribute("route.relationship", "submitted")
            return [flowfile]
        return []

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "agent_ref": {
                "type": "object", "required": True,
                "description": "Exact visible Workflow Agent ResourceRef",
            },
            "message": {
                "type": "string", "required": True,
                "description": "Message expression sent to the child",
            },
            "attachments": {"type": "array", "required": False},
            "parameters": {"type": "object", "required": False},
            "await_terminal": {
                "type": "boolean", "required": False, "default": True},
            "publish_to_conversation": {
                "type": "boolean", "required": False, "default": False},
            "terminal_timeout": {
                "type": "string", "required": False,
                "description": (
                    "Optional explicit terminal deadline; omitted waits forever")},
            "cancellation_policy": {
                "type": "string", "required": False, "default": "propagate",
                "description": "propagate | detach",
            },
            "result_content": {
                "type": "string", "required": False, "default": "response"},
            "artifact_attribute": {
                "type": "string", "required": False,
                "default": "workflow.agent.artifacts",
            },
        }


TaskFactory.register(InvokeWorkflowAgentTask)


__all__ = [
    "InvokeWorkflowAgentTask",
    "resolve_workflow_agent_binding",
]
