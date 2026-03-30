"""ConvTaskOps — Assign or cancel agent tasks in a linked conversation.

For conversation-scoped flows: manage agent tasks in the conversation
as if the flow were a user giving instructions.

Flow pattern:
    someTask → assignTask
    someTask → cancelTask
"""

import json
import logging
import time
import uuid
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class AssignTaskToAgentTask(BaseTask):
    """Assign a task to an agent in a linked conversation."""

    TYPE = "assignTaskToAgent"
    VERSION = "1.0.0"
    NAME = "Assign Task to Agent"
    DESCRIPTION = "Assign a recurring task to an agent in a linked conversation"
    ICON = "task"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {
                "type": "string", "required": True,
                "default": "${_conversation_id}",
                "description": "Target conversation ID",
            },
            "user_id": {
                "type": "string", "required": True,
                "default": "${_user_id}",
                "description": "User ID",
            },
            "agent_name": {
                "type": "string", "required": True,
                "description": "Agent to assign the task to",
            },
            "task_prompt": {
                "type": "textarea", "required": True,
                "description": "Task description / instructions for the agent",
            },
            "interval": {
                "type": "string", "required": False, "default": "6/1m",
                "description": "Re-check frequency (e.g. '6/1m' = 6 times per minute)",
            },
            "max_iterations": {
                "type": "integer", "required": False, "default": 50,
                "description": "Max iterations per check",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        conv_id = flowfile.get_attribute("conversation_id") or self.config.get("conversation_id", "")
        user_id = self.config.get("user_id", "")
        agent = self.config.get("agent_name", "")
        prompt = self.config.get("task_prompt", "")
        interval = self.config.get("interval", "6/1m")
        max_iter = int(self.config.get("max_iterations", 50))

        if not conv_id or "${" in conv_id:
            flowfile.set_content(json.dumps({
                "error": "No conversation_id - set via FlowFile attribute or flow parameter",
            }).encode())
            return [flowfile]

        if not agent or not prompt:
            flowfile.set_content(json.dumps({
                "error": "Missing agent_name or task_prompt",
            }).encode())
            return [flowfile]

        # Override prompt from FlowFile content if present
        content = flowfile.get_content().decode("utf-8", errors="replace").strip()
        if content:
            prompt = content

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        task_id = uuid.uuid4().hex[:12]
        task_entry = {
            "task": prompt,
            "agent": agent,
            "status": "active",
            "created_at": time.time(),
            "iterations_done": 0,
            "max_iterations": max_iter,
            "interval": interval,
        }

        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
        all_tasks[task_id] = task_entry
        store.set_extra(conv_id, "agent_tasks", all_tasks)

        # Schedule first check
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().schedule_delay(
            conv_id, 5,
            key=f"{conv_id}::task::{task_id}",
            reason=f"[agent_task:{task_id}] assigned by flow ({agent})",
            user_id=user_id,
        )

        logger.info(f"[assignTask] {task_id} assigned to {agent} in {conv_id[:8]}")
        flowfile.set_content(json.dumps({
            "ok": True, "task_id": task_id, "agent": agent,
        }).encode())
        return [flowfile]


class CancelAgentTaskTask(BaseTask):
    """Cancel a running agent task in a linked conversation."""

    TYPE = "cancelAgentTask"
    VERSION = "1.0.0"
    NAME = "Cancel Agent Task"
    DESCRIPTION = "Cancel a running task assigned to an agent"
    ICON = "stop"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {
                "type": "string", "required": True,
                "default": "${_conversation_id}",
                "description": "Target conversation ID",
            },
            "task_id": {
                "type": "string", "required": True,
                "description": "Task ID to cancel",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        conv_id = flowfile.get_attribute("conversation_id") or self.config.get("conversation_id", "")
        task_id = self.config.get("task_id", "")

        # Allow task_id from FlowFile content as override
        content = flowfile.get_content().decode("utf-8", errors="replace").strip()
        if content and not task_id:
            task_id = content

        if not conv_id or "${" in conv_id:
            flowfile.set_content(json.dumps({
                "error": "No conversation_id - set via FlowFile attribute or flow parameter",
            }).encode())
            return [flowfile]

        if not task_id:
            flowfile.set_content(json.dumps({"error": "Missing task_id"}).encode())
            return [flowfile]

        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        store = ConversationStore.instance()
        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}

        if task_id not in all_tasks:
            flowfile.set_content(json.dumps({
                "error": f"Task '{task_id}' not found",
            }).encode())
            return [flowfile]

        all_tasks[task_id]["status"] = "cancelled"
        store.set_extra(conv_id, "agent_tasks", all_tasks)

        # Cancel scheduled recheck
        sched_key = f"{conv_id}::task::{task_id}"
        PollScheduler.instance().cancel(sched_key)

        logger.info(f"[cancelTask] {task_id} cancelled in {conv_id[:8]}")
        flowfile.set_content(json.dumps({
            "ok": True, "task_id": task_id, "status": "cancelled",
        }).encode())
        return [flowfile]


TaskFactory.register(AssignTaskToAgentTask)
TaskFactory.register(CancelAgentTaskTask)
