"""AssignTaskHandler — extracted from task_management.py to keep files <=800 lines.

Depends downward on core.handlers._task_helpers; re-exported from
core.handlers.task_management.
"""

import logging
import re
from typing import Any, Dict

from core.tool_handler import ToolHandler
from core.handlers._task_helpers import _append_task_log, schedule_agent_task_wake

logger = logging.getLogger(__name__)


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
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs that must complete before this task starts. The task will be in 'waiting' status until all dependencies are met.",
                },
            },
            "required": ["agent", "task_def_name"],
        }

    @staticmethod
    def _has_cycle(all_tasks: dict, new_id: str, new_deps: list) -> bool:
        """Check if adding new_id with new_deps would create a cycle."""
        # Build adjacency: task → its depends_on
        graph = {}
        for tid, t in all_tasks.items():
            if isinstance(t, dict):
                graph[tid] = t.get("depends_on") or []
        graph[new_id] = new_deps
        # DFS cycle detection
        visited = set()
        in_stack = set()
        def _dfs(node):
            if node in in_stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            in_stack.add(node)
            for dep in graph.get(node, []):
                if _dfs(dep):
                    return True
            in_stack.discard(node)
            return False
        return _dfs(new_id)

    @staticmethod
    def _parse_interval(spec: str, fallback: int = 10) -> dict:
        """Parse interval spec → {min: seconds, max: seconds, spec: original}.

        Formats:
          '60'       → fixed 60s
          '3/5m'     → 3 times per 5 minutes
          '2-4/h'    → 2-4 times per hour
        """
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
            return random.randint(iv.get("min", 60), iv.get("max", 60))  # nosec B311
        return 60

    @staticmethod
    def _parse_timeout(spec: str) -> int:
        """Parse timeout spec → seconds. Returns 0 for no timeout.

        Formats: '300' (seconds), '5m', '1h', '2h30m'
        """
        if not spec:
            return 0
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
    def _resolve_task_vars(text: str, variables: dict, user_id: str = "",
                           conversation_id: str = "") -> str:
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
            text = resolve_expression(text, owner=user_id,
                                      conversation_id=conversation_id)
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
            target = self._agent_name or ""
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
            task_desc = self._resolve_task_vars(task_desc, _vars, self._user_id,
                                                self._conversation_id)
        criteria = arguments.get("completion_criteria", "")
        if criteria and (_vars or "${" in criteria):
            criteria = self._resolve_task_vars(criteria, _vars, self._user_id,
                                               self._conversation_id)
        _raw_iv = arguments.get("interval")
        interval_spec = str(_raw_iv) if _raw_iv else "6/1m"
        max_iter = int(arguments.get("max_iterations", 0))
        verifier = arguments.get("verifier", "") or definition.get("verifier", "")
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
        interactive = bool(arguments.get("interactive", False) or definition.get("interactive"))

        # Parse interval: plain seconds or frequency spec (3/5m, 2-4/h)
        interval_data = self._parse_interval(interval_spec)

        import uuid as _uuid
        task_id = "t_" + _uuid.uuid4().hex[:8]

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        # Store in agent_tasks dict (multiple tasks per agent)
        all_tasks = store.get_extra(self._conversation_id, "agent_tasks") or {}

        # Dependency handling
        from core.handlers._arg_normalize import normalize_string_list
        depends_on = normalize_string_list(arguments.get("depends_on"))
        if depends_on:
            # Validate: all deps must exist in the same conversation
            for dep_id in depends_on:
                if dep_id not in all_tasks:
                    return f"Error: dependency '{dep_id}' not found in this conversation's tasks"
            # Cycle detection: DFS from this task through depends_on graph
            if self._has_cycle(all_tasks, task_id, depends_on):
                return "Error: circular dependency detected"

        context_mode = arguments.get("context", "isolated")
        # Determine initial status based on dependencies
        _deps_met = not depends_on or all(
            all_tasks.get(d, {}).get("status") in (None, "completed")
            or d not in all_tasks
            for d in depends_on
        )
        _initial_status = "active" if _deps_met else "waiting"
        task_data = {
            "task_id": task_id,
            "agent": target,
            "task": task_desc,
            "completion_criteria": criteria,
            "status": _initial_status,
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
            "interactive": interactive,
            "skills": self._merge_task_skills(
                definition.get("skills") or [],
                arguments.get("skills") or [],
                target, self._conversation_id),
            "depends_on": depends_on,
        }
        all_tasks[task_id] = task_data
        store.set_extra(self._conversation_id, "agent_tasks", all_tasks)

        # Schedule first wake-up (only if not waiting on deps)
        if _initial_status == "active":
            first_delay = 0
            schedule_agent_task_wake(
                self._conversation_id, task_id,
                reason=f"[agent_task:{task_id}] assigned task ({target})",
                user_id=self._user_id,
                delay_seconds=0,
            )
        else:
            first_delay = 0

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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        try:
            _append_task_log(self._conversation_id, task_id, {
                "type": "assigned",
                "agent": target,
                "task": task_desc[:200],
                "detail": f"Assigned by {self._agent_name or 'user'}, verifier={verifier or 'none'}",
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        v_info = f" (verifier: {verifier})" if verifier else ""
        iv_label = interval_data.get("spec", str(first_delay))
        dep_info = f" Waiting on: {', '.join(depends_on)}." if _initial_status == "waiting" else ""
        sched_info = " Starting now." if _initial_status == "active" else ""
        return f"Task {task_id} assigned to '{target}'{v_info}. Status: {_initial_status}. Interval: {iv_label}.{sched_info}{dep_info}"


    @staticmethod
    def _merge_task_skills(def_skills, explicit_skills, agent_name, conv_id):
        """Merge skills: agent's conv skills (inherited) + task_def skills + explicit.

        Deduplicates by name, preserving order.
        """
        seen = set()
        merged = []
        # 1. Agent's conv-level skills (inherited baseline)
        if conv_id and agent_name:
            try:
                from core.conv_agent_config import get_agent_config
                agent_skills = get_agent_config(conv_id, agent_name).get("skills") or []
                for s in agent_skills:
                    n = s if isinstance(s, str) else s.get("name", "")
                    if n and n not in seen:
                        seen.add(n)
                        merged.append(s)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        # 2. Task definition skills
        for s in (def_skills or []):
            n = s if isinstance(s, str) else s.get("name", "")
            if n and n not in seen:
                seen.add(n)
                merged.append(s)
        # 3. Explicitly passed skills (override/additions)
        for s in (explicit_skills or []):
            n = s if isinstance(s, str) else s.get("name", "")
            if n and n not in seen:
                seen.add(n)
                merged.append(s)
        return merged


