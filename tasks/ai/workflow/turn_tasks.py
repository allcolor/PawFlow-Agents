"""Bootstrap-safe tasks for the experimental workflow-agent vertical slice."""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.base_task import BaseTask
from core.workflow_agent_contracts import AgentWorkflowRequest, AgentWorkflowResult


class _WorkflowContextTask(BaseTask):
    """Task base whose authority comes from executor injection, never FlowFiles."""

    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"

    def set_workflow_run_context(
            self, context, *, event_callback=None, terminal_callback=None,
            inbox_store=None, run_store=None, cancel_event=None,
            preempt_policy=None,
            visible_through_sequence=None) -> None:
        self._workflow_run_context = context
        self._workflow_event_callback = event_callback
        self._workflow_terminal_callback = terminal_callback
        self._workflow_inbox_store = inbox_store
        self._workflow_run_store = run_store
        self._workflow_cancel_event = cancel_event
        self._workflow_preempt_policy = preempt_policy
        self._workflow_visible_through_sequence = visible_through_sequence

    def _context(self):
        context = getattr(self, "_workflow_run_context", None)
        if context is None:
            raise RuntimeError("workflow run context was not injected")
        return context


class AgentWorkflowInputTask(_WorkflowContextTask):
    TYPE = "agentWorkflowInput"
    NAME = "Agent Workflow Input"
    DESCRIPTION = "Validate the server-owned workflow request."

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        request = AgentWorkflowRequest.from_dict(json.loads(
            flowfile.get_content().decode("utf-8")))
        if request.conversation.id != context.conversation_id:
            raise ValueError("workflow request conversation does not match run context")
        if request.conversation.agent.casefold() != context.agent_name.casefold():
            raise ValueError("workflow request agent does not match run context")
        if request.turn.root_turn_id != context.root_turn_id:
            raise ValueError("workflow request turn does not match run context")
        return [flowfile]


class EmitAgentProgressTask(_WorkflowContextTask):
    TYPE = "emitAgentProgress"
    NAME = "Emit Agent Progress"
    DESCRIPTION = "Publish bounded stage progress without transcript content."
    IDEMPOTENCY = IdempotencyClass.NATURAL

    def get_parameter_schema(self) -> dict[str, Any]:
        return {"stage": {"type": "string", "required": True}}

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        callback = getattr(self, "_workflow_event_callback", None)
        if callback is not None:
            callback("workflow_progress", {
                "turn_id": context.root_turn_id,
                "run_id": context.run_id,
                "agent_name": context.agent_name,
                "flow_fqn": context.flow_ref.name,
                "stage": str(self.config.get("stage") or "")[:160],
            })
        return [flowfile]


class WorkflowFakeLLMTask(_WorkflowContextTask):
    TYPE = "workflowFakeLLM"
    NAME = "Workflow Fake LLM"
    DESCRIPTION = "Deterministic stand-in for the WP3 LLM node."

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "response_prefix": {
                "type": "string", "required": False,
                "default": "Workflow completed: ",
            },
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        request = AgentWorkflowRequest.from_dict(json.loads(
            flowfile.get_content().decode("utf-8")))
        result = AgentWorkflowResult(
            status="completed",
            response=(str(self.config.get("response_prefix") or "")
                      + request.request.message),
            answered_turn_ids=(context.root_turn_id,),
        )
        flowfile.set_content(json.dumps(
            result.to_dict(), ensure_ascii=False).encode("utf-8"))
        return [flowfile]


class CompleteAgentTurnTask(_WorkflowContextTask):
    TYPE = "completeAgentTurn"
    NAME = "Complete Agent Turn"
    DESCRIPTION = "Validate and stage the sole workflow terminal result."
    IDEMPOTENCY = IdempotencyClass.NATURAL

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        result = AgentWorkflowResult.from_dict(json.loads(
            flowfile.get_content().decode("utf-8")))
        if context.root_turn_id not in result.answered_turn_ids:
            raise ValueError("terminal result must answer the root turn")
        callback = getattr(self, "_workflow_terminal_callback", None)
        if callback is None:
            raise RuntimeError("workflow terminal collector was not injected")
        callback(result)
        return [flowfile]


class ReceiveAgentMessagesTask(_WorkflowContextTask):
    TYPE = "receiveAgentMessages"
    NAME = "Receive Agent Messages"
    DESCRIPTION = "Lease durable workflow-agent inbox messages."
    IDEMPOTENCY = IdempotencyClass.NATURAL
    RELATIONSHIPS: ClassVar = ["messages", "empty", "cancelled", "failure"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "max_messages": {
                "type": "integer", "required": False, "default": 0},
            "wait_ms": {
                "type": "integer", "required": False, "default": 0},
            "sources": {
                "type": "array", "required": False, "default": []},
            "output_attribute": {
                "type": "string", "required": False, "default": ""},
            "include_content": {
                "type": "boolean", "required": False, "default": True},
            "empty_relationship": {
                "type": "string", "required": False, "default": "empty"},
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        context = self._context()
        inbox = getattr(self, "_workflow_inbox_store", None)
        if inbox is None:
            from core.agent_inbox_store import AgentInboxStore
            inbox = AgentInboxStore.instance()
        cancel = getattr(self, "_workflow_cancel_event", None)
        wait_ms = max(0, int(self.config.get("wait_ms", 0) or 0))
        deadline = time.monotonic() + wait_ms / 1000.0
        task_id = self.get_task_id()
        claim = None
        items = ()
        while True:
            if cancel is not None and cancel.is_set():
                flowfile.set_attribute("route.relationship", "cancelled")
                return [flowfile]
            claim, items = inbox.claim(
                context.conversation_id, context.agent_name,
                context.run_id, task_id,
                max_messages=max(
                    0, int(self.config.get("max_messages", 0) or 0)),
                lease_seconds=max(
                    60,
                    int(context.limits.max_duration_seconds or 0) + 60,
                ),
                sources=self.config.get("sources") or (),
                max_sequence=(
                    getattr(self, "_workflow_visible_through_sequence", None)
                    if getattr(self, "_workflow_preempt_policy", None) == "queue"
                    else None))
            if items or time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        if not items:
            relationship = str(
                self.config.get("empty_relationship") or "empty")
            flowfile.set_attribute("route.relationship", relationship)
            return [flowfile]
        run_store = getattr(self, "_workflow_run_store", None)
        if run_store is not None:
            run_store.record_claimed_ids(
                context.run_id, [item.msg_id for item in items])
        include_content = bool(self.config.get("include_content", True))
        messages = []
        for item in items:
            payload = dict(item.payload)
            if not include_content:
                payload.pop("content", None)
            messages.append({
                "msg_id": item.msg_id,
                "sequence": item.sequence,
                "source": item.source,
                "payload": payload,
            })
        output = {
            "claim": claim.to_dict() if claim is not None else None,
            "messages": messages,
        }
        encoded = json.dumps(output, ensure_ascii=False)
        output_attribute = str(
            self.config.get("output_attribute") or "")
        if output_attribute:
            flowfile.set_attribute(output_attribute, encoded)
        else:
            flowfile.set_content(encoded.encode("utf-8"))
        flowfile.set_attribute("route.relationship", "messages")
        return [flowfile]


for _task in (
    AgentWorkflowInputTask,
    EmitAgentProgressTask,
    WorkflowFakeLLMTask,
    CompleteAgentTurnTask,
    ReceiveAgentMessagesTask,
):
    TaskFactory.register(_task)
