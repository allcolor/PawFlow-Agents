"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


def _append_task_log(conversation_id: str, task_id: str, entry: dict):
    """Append an entry to the persistent task timeline log (standalone helper)."""
    import time
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    key = f"task_log:{task_id}"
    log = store.get_extra(conversation_id, key) or []
    entry["ts"] = time.time()
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    store.set_extra(conversation_id, key, log)





class AssignTaskHandler(ToolHandler):
    """Assign a task to an agent (self or another agent)."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""
        self._user_id = ""

    @property
    def name(self) -> str:
        return "assign_task"

    @property
    def description(self) -> str:
        return (
            "Assign a task to yourself or another agent. The assigned agent "
            "will work on it autonomously, rescheduling at regular intervals "
            "until the task is complete."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent to assign the task to (name, or 'self' for yourself)",
                },
                "task_def_name": {
                    "type": "string",
                    "description": "Name of the task definition to run. Must exist (conversation, user, or global scope).",
                },
                "completion_criteria": {
                    "type": "string",
                    "description": "Override completion criteria from the task definition",
                },
                "interval": {
                    "type": "string",
                    "description": "Schedule frequency. Examples: '60' (every 60s), '3/5m' (3 times per 5min), '2-4/h' (2-4 per hour). Default: 6/1m (same as autoconv)",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Max work sessions before auto-fail (default: 50)",
                },
                "verifier": {
                    "type": "string",
                    "description": "Agent that verifies completion (optional)",
                },
                "variables": {
                    "type": "object",
                    "description": "Variables to substitute in prompt/criteria. E.g. {\"nbr_images\": \"20\"} replaces ${nbr_images} in the task definition. Use \\${...} in definitions to keep literal ${...} unresolved.",
                },
                "context": {
                    "type": "string",
                    "description": "Context mode: 'isolated' (default), 'last:N' (last N messages), 'summary:N' (summary of N tokens), 'full' (entire parent context)",
                },
                "timeout": {
                    "type": "string",
                    "description": "DEPRECATED — use max_turn_time instead.",
                },
                "max_turn_time": {
                    "type": "string",
                    "description": "Max duration per work session. Examples: '300' (300s), '5m', '1h'. If exceeded the turn is interrupted (not cancelled) and rescheduled normally. Default: no limit.",
                },
                "max_budget": {
                    "type": "string",
                    "description": "Max total cost for this task. Examples: '0.50', '$2', '1.00'. Task is cancelled if cumulative cost exceeds this. Default: no limit.",
                },
                "max_total_time": {
                    "type": "string",
                    "description": "Max total elapsed time across all reschedules. Examples: '30m', '1h', '2h'. Task is cancelled if exceeded. Default: no limit.",
                },
                "max_reschedules": {
                    "type": "integer",
                    "description": "Max number of reschedules (work sessions) before the task is cancelled. Default: no limit (0).",
                },
                "auto_allow": {
                    "type": "boolean",
                    "description": "Auto-approve all tool calls and commands for this task (no permission prompts). Default: false.",
                },
            },
            "required": ["agent", "task_def_name"],
        }

    @staticmethod
    def _parse_interval(spec: str, fallback: int = 10) -> dict:
        """Parse interval spec → {min: seconds, max: seconds, spec: original}.

        Formats:
          '60'       → fixed 60s
          '3/5m'     → 3 times per 5 minutes
          '2-4/h'    → 2-4 times per hour
        """
        import re
        spec = spec.strip()
        # Plain seconds
        try:
            secs = int(spec)
            return {"min": secs, "max": secs, "spec": spec}
        except ValueError:
            pass
        # Frequency spec: count[-count]/[num]unit
        m = re.match(r'^(\d+)(?:-(\d+))?/(\d*)([smhd])$', spec)
        if not m:
            return {"min": fallback, "max": fallback, "spec": spec}
        count_min = int(m.group(1))
        count_max = int(m.group(2) or count_min)
        if count_min <= 0 or count_max < count_min:
            return {"min": fallback, "max": fallback, "spec": spec}
        duration_num = int(m.group(3) or 1)
        unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(4)]
        period = duration_num * unit
        max_interval = period // count_min
        min_interval = period // count_max
        return {"min": max(1, min_interval), "max": max(1, max_interval), "spec": spec}

    @staticmethod
    def _get_task_delay(task_data: dict) -> int:
        """Get the next delay in seconds from a task's interval config."""
        import random
        iv = task_data.get("interval", {})
        if isinstance(iv, int):
            return iv
        if isinstance(iv, dict):
            return random.randint(iv.get("min", 60), iv.get("max", 60))
        return 60

    @staticmethod
    def _parse_timeout(spec: str) -> int:
        """Parse timeout spec → seconds. Returns 0 for no timeout.

        Formats: '300' (seconds), '5m', '1h', '2h30m'
        """
        if not spec:
            return 0
        import re
        spec = spec.strip()
        try:
            return int(spec)
        except ValueError:
            pass
        total = 0
        for m in re.finditer(r'(\d+)([smhd])', spec):
            val = int(m.group(1))
            unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(2)]
            total += val * unit
        return total

    @staticmethod
    def _resolve_task_vars(text: str, variables: dict, user_id: str = "") -> str:
        """Resolve variables in task prompt/criteria.

        Resolution order:
        1. Escaped \\${...} → preserved as literal ${...}
        2. Custom variables from 'variables' dict: ${key} → value
        3. Unified cascade: secrets → params → env
        """
        # Step 1: protect escaped \${...} with placeholder
        _esc = "\x00ESC\x00"
        text = text.replace("\\${", _esc)
        # Step 2: replace custom variables ${key}
        if variables:
            for key, val in variables.items():
                text = text.replace(f"${{{key}}}", str(val))
        # Step 3: resolve remaining ${key} via unified cascade
        if "${" in text:
            from core.expression import resolve_expression
            text = resolve_expression(text, owner=user_id)
        # Step 4: restore escaped expressions
        text = text.replace(_esc, "${")
        return text

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _t
        target = arguments.get("agent", "")
        if target == "self":
            target = self._agent_name or "assistant"
        task_def_name = arguments.get("task_def_name", "")

        if not task_def_name:
            return "Error: task_def_name is required. Create a task definition first."
        if not self._conversation_id:
            return "Error: no conversation context"

        # Resolve task_def_name → prompt + criteria + interval
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        definition = rs.get_any("task_def", task_def_name, self._user_id,
                                 conversation_id=self._conversation_id)
        if not definition:
            return f"Error: task definition '{task_def_name}' not found"
        task_desc = definition.get("prompt", "")
        if not task_desc:
            return f"Error: task definition '{task_def_name}' has no prompt"
        if not arguments.get("completion_criteria"):
            arguments["completion_criteria"] = definition.get("criteria", "")
        if not arguments.get("interval"):
            arguments["interval"] = definition.get("default_interval", "6/1m")

        # Variable substitution in prompt and criteria
        _vars = arguments.get("variables") or {}
        if _vars or "${" in task_desc:
            task_desc = self._resolve_task_vars(task_desc, _vars, self._user_id)
        criteria = arguments.get("completion_criteria", "")
        if criteria and (_vars or "${" in criteria):
            criteria = self._resolve_task_vars(criteria, _vars, self._user_id)
        _raw_iv = arguments.get("interval")
        interval_spec = str(_raw_iv) if _raw_iv else "6/1m"
        max_iter = int(arguments.get("max_iterations", 0))
        verifier = arguments.get("verifier", "")
        # max_turn_time supersedes deprecated timeout
        _turn_time_raw = arguments.get("max_turn_time", "") or arguments.get("timeout", "")
        timeout_secs = self._parse_timeout(_turn_time_raw)
        # New limit params
        _max_budget_raw = str(arguments.get("max_budget", "") or "").strip().lstrip("$")
        max_budget = float(_max_budget_raw) if _max_budget_raw else 0.0
        max_total_time = self._parse_timeout(arguments.get("max_total_time", "") or "")
        max_reschedules = int(arguments.get("max_reschedules", 0) or 0)
        auto_allow = bool(arguments.get("auto_allow", False))
        # Also check task definition for auto_allow default
        if not auto_allow and definition.get("auto_allow"):
            auto_allow = True

        # Parse interval: plain seconds or frequency spec (3/5m, 2-4/h)
        interval_data = self._parse_interval(interval_spec)

        import uuid as _uuid
        task_id = "t_" + _uuid.uuid4().hex[:8]

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        # Store in agent_tasks dict (multiple tasks per agent)
        all_tasks = store.get_extra(self._conversation_id, "agent_tasks") or {}
        context_mode = arguments.get("context", "isolated")
        task_data = {
            "task_id": task_id,
            "agent": target,
            "task": task_desc,
            "completion_criteria": criteria,
            "status": "active",
            "interval": interval_data,
            "max_iterations": max_iter,
            "iterations_done": 0,
            "verifier": verifier,
            "assigned_by": self._agent_name or self._user_id or "unknown",
            "created_by": self._agent_name or self._user_id or "unknown",
            "task_def_name": task_def_name,
            "created_at": _t.time(),
            "last_result": "",
            "context_mode": context_mode,
            "timeout": timeout_secs,
            "max_budget": max_budget,
            "max_total_time": max_total_time,
            "max_reschedules": max_reschedules,
            "total_cost": 0.0,
            "reschedule_count": 0,
            "auto_allow": auto_allow,
        }
        all_tasks[task_id] = task_data
        store.set_extra(self._conversation_id, "agent_tasks", all_tasks)

        # Schedule first wake-up
        first_delay = self._get_task_delay(task_data)
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().schedule_delay(
            self._conversation_id, first_delay,
            key=f"{self._conversation_id}::task::{task_id}",
            reason=f"[agent_task:{task_id}] assigned task ({target})",
            user_id=self._user_id,
        )

        # Publish SSE
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "task_progress", {
                    "task_id": task_id, "agent": target, "stage": "assigned",
                    "task": task_desc[:200], "verifier": verifier,
                    "assigned_by": self._agent_name or "user",
                },
            )
        except Exception:
            pass

        try:
            _append_task_log(self._conversation_id, task_id, {
                "type": "assigned",
                "agent": target,
                "task": task_desc[:200],
                "detail": f"Assigned by {self._agent_name or 'user'}, verifier={verifier or 'none'}",
            })
        except Exception:
            pass

        v_info = f" (verifier: {verifier})" if verifier else ""
        iv_label = interval_data.get("spec", str(first_delay))
        return f"Task {task_id} assigned to '{target}'{v_info}. Interval: {iv_label}. First in {first_delay}s."


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

        agent = self._agent_name or "assistant"
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        # Tasks are stored in the PARENT conversation's extras.
        # Sub-conv IDs have format: parent_id::task::task_id
        _parent_cid = self._conversation_id
        if "::task::" in _parent_cid:
            _parent_cid = _parent_cid.split("::task::")[0]
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
            ConversationEventBus.instance().publish_event(
                _parent_cid, "task_progress", {
                    "task_id": task_id, "agent": agent, "done": done,
                    "progress": progress, "result": result,
                    "iterations": task["iterations_done"],
                },
            )
        except Exception:
            pass

        try:
            _log_type = "completed" if done else "progress"
            _log_detail = result[:200] if done else progress[:200]
            _append_task_log(_parent_cid, task_id, {
                "type": _log_type,
                "agent": agent,
                "detail": _log_detail,
            })
        except Exception:
            pass

        if done:
            # Don't touch cancelled/paused tasks — user cancelled intentionally
            if task.get("status") in ("cancelled", "paused"):
                return f"Task {task_id} was {task['status']} — ignoring completion."
            # Recurring tasks (no criteria) cannot be completed — ignore done=true
            if not task.get("completion_criteria"):
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
                return f"Task {task_id} completed."
        else:
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
            pass

        try:
            _append_task_log(_parent_cid, task_id, {
                "type": "verified",
                "agent": target_agent,
                "verifier": self._agent_name,
                "approved": approved,
                "detail": reason[:200] if reason else ("approved" if approved else "rejected"),
            })
        except Exception:
            pass

        if approved:
            # Remove completed task
            all_tasks.pop(task_id, None)
            store.set_extra(_parent_cid, "agent_tasks", all_tasks)
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().cancel(
                f"{_parent_cid}::task::{task_id}")
            PollScheduler.instance().cancel(
                f"{_parent_cid}::task_verify::{task_id}")
            return f"Task {task_id} approved and completed."
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
