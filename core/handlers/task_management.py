"""Task-management tool handlers (link, complete, verify) + assign re-export.

The module helper functions live in _task_helpers.py and AssignTaskHandler in
task_assign.py (split to keep files <=800 lines). The public surface
(handlers + schedule_agent_task_wake / _append_task_log) is re-exported here so
the core.handlers.task_management import path is unchanged.
"""

import logging
from typing import Any, Dict

from core.task_lifecycle import cleanup_agent_task_context
from core.tool_handler import ToolHandler

from core.handlers._task_helpers import (  # noqa: F401
    _activate_dependents,
    _append_task_log,
    schedule_agent_task_wake,
    wake_agent_poller,
)
from core.handlers.task_assign import AssignTaskHandler  # noqa: F401

logger = logging.getLogger(__name__)


class LinkResourceHandler(ToolHandler):
    """Link or unlink repository resources to the current conversation.

    Only relays are linkable. Everything else is auto-available when
    accessible in scope (global + user + conversation).
    """

    def __init__(self):
        self._conversation_id = ""
        self._user_id = ""

    @property
    def name(self) -> str:
        return "link_resource"

    @property
    def description(self) -> str:
        return (
            "Link or unlink a relay to this conversation.\n"
            "Only relays are linkable (set_default, per-agent binding via /relay).\n"
            "MCPs/skills/tasks/agents are auto-available when in scope.\n\n"
            "Actions: link, unlink, list"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["link", "unlink", "list"],
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["relays"],
                },
                "name": {
                    "type": "string",
                    "description": "Resource name (for link/unlink)",
                },
            },
            "required": ["action", "resource_type"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.conv_links import get_linked, link, unlink
        action = arguments.get("action", "")
        rtype = arguments.get("resource_type", "")
        name = arguments.get("name", "")

        if not self._conversation_id:
            return "Error: no conversation context"

        if action == "list":
            linked = get_linked(self._conversation_id, rtype)
            if not linked:
                return f"No {rtype} linked to this conversation."
            return f"Linked {rtype}: " + ", ".join(linked)

        if not name:
            return "Error: name is required for link/unlink"

        if action == "link":
            link(self._conversation_id, rtype, name)
            return f"{rtype[:-1].title()} '{name}' linked to this conversation."

        if action == "unlink":
            unlink(self._conversation_id, rtype, name)
            return f"{rtype[:-1].title()} '{name}' unlinked from this conversation."

        return f"Unknown action: {action}"


# Keep old name as alias for imports
LinkTaskHandler = LinkResourceHandler



class CompleteTaskHandler(ToolHandler):
    """Report progress or completion of an assigned task.

    Called by the agent at each wake-up to update task status.
    If done=true and a verifier agent is assigned, triggers verification.
    """

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Report progress or completion of your assigned task. "
            "Call this at each iteration to update your progress. "
            "Set done=true when the task is finished."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to update (optional if you have only one active task)",
                },
                "done": {
                    "type": "boolean",
                    "description": "True if the task is complete, false if still in progress",
                },
                "progress": {
                    "type": "string",
                    "description": "Status update (e.g. '30/100 posts scraped')",
                },
                "result": {
                    "type": "string",
                    "description": "Final result summary (only when done=true)",
                },
            },
            "required": ["done", "progress"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        task_id = arguments.get("task_id", "")
        done = arguments.get("done", False)
        progress = arguments.get("progress", "")
        result = arguments.get("result", "")

        if not self._conversation_id:
            return "Error: no conversation context"

        agent = self._agent_name or ""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        # Tasks are stored in the PARENT conversation's extras.
        # Sub-conv IDs have format: parent_id::task::task_id
        from core.service_registry import _parent_conversation_id
        _parent_cid = (_parent_conversation_id(self._conversation_id)
                       or self._conversation_id)
        all_tasks = store.get_extra(_parent_cid, "agent_tasks") or {}

        # Find the task — by ID or by agent (if only one active)
        task = None
        if task_id:
            task = all_tasks.get(task_id)
        else:
            # Find active tasks for this agent
            my_tasks = [t for t in all_tasks.values()
                        if t.get("agent") == agent and t.get("status") in ("active",)]
            if len(my_tasks) == 1:
                task = my_tasks[0]
                task_id = task["task_id"]
            elif len(my_tasks) > 1:
                ids = [t["task_id"] for t in my_tasks]
                return f"Multiple active tasks. Specify task_id: {', '.join(ids)}"

        if not task or task.get("status") not in ("active", "verifying"):
            return "No active task found."

        task["iterations_done"] = task.get("iterations_done", 0) + 1
        task["last_result"] = result if done else progress
        task["last_update"] = _t.time()

        try:
            from core.conversation_event_bus import ConversationEventBus
            _task_iteration = task.get("reschedule_count", task["iterations_done"])
            ConversationEventBus.instance().publish_event(
                _parent_cid, "task_progress", {
                    "task_id": task_id, "agent": agent, "done": done,
                    "progress": progress, "result": result,
                    "iterations": task["iterations_done"],
                    "task_iteration": _task_iteration,
                },
            )
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        try:
            _log_type = "completed" if done else "progress"
            _log_detail = result[:200] if done else progress[:200]
            _append_task_log(_parent_cid, task_id, {
                "type": _log_type,
                "agent": agent,
                "detail": _log_detail,
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        if done:
            # Don't touch cancelled/paused tasks — user cancelled intentionally
            if task.get("status") in ("cancelled", "paused"):
                return f"Task {task_id} was {task['status']} — ignoring completion."
            # Recurring tasks (no criteria) cannot be completed — ignore done=true
            if not task.get("completion_criteria"):
                # Check limits before rescheduling
                from tasks.ai.agent_poller import _check_task_limits
                _cancel_reason = _check_task_limits(task, task_id)
                if _cancel_reason:
                    task["status"] = "cancelled"
                    task["cancel_reason"] = _cancel_reason
                    all_tasks[task_id] = task
                    store.set_extra(_parent_cid, "agent_tasks", all_tasks)
                    from core.poll_scheduler import PollScheduler
                    PollScheduler.instance().cancel(f"{_parent_cid}::task::{task_id}")
                    cleanup_agent_task_context(
                        _parent_cid, task_id, task.get("agent", agent), store,
                        clear_runtime=True, reason="task_limit_cancel")
                    return f"Task {task_id} cancelled: {_cancel_reason}"
                task["status"] = "active"
                all_tasks[task_id] = task
                store.set_extra(_parent_cid, "agent_tasks", all_tasks)
                delay = AssignTaskHandler._get_task_delay(task)
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().schedule_delay(
                    _parent_cid, delay,
                    key=f"{_parent_cid}::task::{task_id}",
                    reason=f"[agent_task:{task_id}] recurring ({task.get('agent', agent)})",
                    user_id=task.get("assigned_by", ""),
                )
                return f"Task {task_id} is recurring (no completion criteria). Progress noted. Next in {delay}s."
            verifier = task.get("verifier", "")
            if verifier:
                task["status"] = "verifying"
                all_tasks[task_id] = task
                store.set_extra(_parent_cid, "agent_tasks", all_tasks)
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().schedule_delay(
                    _parent_cid, 0,
                    key=f"{_parent_cid}::task_verify::{task_id}",
                    reason=f"[task_verify:{task_id}] verify by {verifier} ({agent})",
                    user_id=task.get("assigned_by", ""),
                )
                return f"Task {task_id} marked done. Verifier '{verifier}' will check."
            else:
                # Remove completed task — trace is in chat history
                all_tasks.pop(task_id, None)
                store.set_extra(_parent_cid, "agent_tasks", all_tasks)
                # Cancel any pending schedule
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().cancel(
                    f"{_parent_cid}::task::{task_id}")
                cleanup_agent_task_context(
                    _parent_cid, task_id, task.get("agent", agent), store,
                    reason="task_completed")
                # Activate dependent tasks
                _activated = _activate_dependents(
                    _parent_cid, task_id, result=result,
                    user_id=task.get("assigned_by", ""))
                _act_msg = f" Activated: {', '.join(_activated)}." if _activated else ""
                return f"Task {task_id} completed.{_act_msg}"
        else:
            # Check limits before rescheduling
            from tasks.ai.agent_poller import _check_task_limits
            _cancel_reason = _check_task_limits(task, task_id)
            if _cancel_reason:
                task["status"] = "cancelled"
                task["cancel_reason"] = _cancel_reason
                all_tasks[task_id] = task
                store.set_extra(_parent_cid, "agent_tasks", all_tasks)
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().cancel(f"{_parent_cid}::task::{task_id}")
                cleanup_agent_task_context(
                    _parent_cid, task_id, task.get("agent", agent), store,
                    clear_runtime=True, reason="task_limit_cancel")
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        _parent_cid, "task_stopped", {
                            "task_id": task_id, "agent_name": agent,
                            "reason": _cancel_reason, "force": True})
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                return f"Task {task_id} cancelled: {_cancel_reason}"
            task["status"] = "active"
            all_tasks[task_id] = task
            store.set_extra(_parent_cid, "agent_tasks", all_tasks)
            delay = AssignTaskHandler._get_task_delay(task)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().schedule_delay(
                _parent_cid, delay,
                key=f"{_parent_cid}::task::{task_id}",
                reason=f"[agent_task:{task_id}] continue ({task.get('agent', agent)})",
                user_id=task.get("assigned_by", ""),
            )
            return f"Task {task_id} progress noted. Next in {delay}s."


class VerifyTaskHandler(ToolHandler):
    """Approve or reject a completed task (used by verifier agents)."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "verify_task"

    @property
    def description(self) -> str:
        return (
            "Approve or reject a task that another agent claims to have completed. "
            "You are the verifier — check the result against the criteria."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to verify",
                },
                "approved": {
                    "type": "boolean",
                    "description": "True if the task is satisfactorily completed",
                },
                "reason": {
                    "type": "string",
                    "description": "Explanation (required if rejecting)",
                },
            },
            "required": ["task_id", "approved"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        task_id = arguments.get("task_id", "")
        approved = arguments.get("approved", False)
        reason = arguments.get("reason", "")

        if not self._conversation_id or not task_id:
            return "Error: missing conversation or task_id"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        # Tasks are stored in the PARENT conv (sub-conv format: parent::task::tid)
        _parent_cid = self._conversation_id
        if "::task" in _parent_cid:
            _parent_cid = _parent_cid.split("::task")[0]
        all_tasks = store.get_extra(_parent_cid, "agent_tasks") or {}
        task = all_tasks.get(task_id)
        if not task:
            return f"Task '{task_id}' not found"
        target_agent = task.get("agent", "?")

        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                _parent_cid, "task_progress", {
                    "task_id": task_id, "agent": target_agent,
                    "verifier": self._agent_name,
                    "approved": approved, "reason": reason,
                    "stage": "verified",
                },
            )
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        try:
            _append_task_log(_parent_cid, task_id, {
                "type": "verified",
                "agent": target_agent,
                "verifier": self._agent_name,
                "approved": approved,
                "detail": reason[:200] if reason else ("approved" if approved else "rejected"),
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        if approved:
            # Remove completed task
            _result = task.get("last_result", "")
            all_tasks.pop(task_id, None)
            store.set_extra(_parent_cid, "agent_tasks", all_tasks)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().cancel(
                f"{_parent_cid}::task::{task_id}")
            PollScheduler.instance().cancel(
                f"{_parent_cid}::task_verify::{task_id}")
            cleanup_agent_task_context(
                _parent_cid, task_id, target_agent, store,
                reason="task_verified")
            # Activate dependent tasks
            _activated = _activate_dependents(
                _parent_cid, task_id, result=_result,
                user_id=task.get("assigned_by", ""))
            _act_msg = f" Activated: {', '.join(_activated)}." if _activated else ""
            return f"Task {task_id} approved and completed.{_act_msg}"
        else:
            task["status"] = "active"
            task["last_rejection"] = {
                "by": self._agent_name, "reason": reason, "at": _t.time(),
            }
            all_tasks[task_id] = task
            store.set_extra(_parent_cid, "agent_tasks", all_tasks)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().schedule_delay(
                _parent_cid, 0,
                key=f"{_parent_cid}::task::{task_id}",
                reason=f"[agent_task:{task_id}] rejected: {reason[:80]} ({target_agent})",
                user_id=task.get("assigned_by", ""),
            )
            return f"Task {task_id} rejected. Agent '{target_agent}' rescheduled."
