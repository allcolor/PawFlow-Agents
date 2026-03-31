"""AgentLoopTask actions — scheduling"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_scheduling(self, action, body, store, user_id, flowfile):
    """Handle scheduling actions. Returns [flowfile] or None."""


    if action == "random_thought":
        return self._handle_random_thought(body, body.get("conversation_id", ""), user_id, flowfile)

    # â”€â”€ Task management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if action == "list_schedules":
        conv_id = body.get("conversation_id", "")
        from core.poll_scheduler import PollScheduler
        all_scheds = PollScheduler.instance().list_all()
        # Filter to current conversation
        scheds = [s for s in all_scheds if s["conversation_id"] == conv_id]
        flowfile.set_content(json.dumps({"schedules": scheds}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "add_schedule":
        conv_id = body.get("conversation_id", "")
        at_str = body.get("at", "")
        reason = body.get("reason", "manual schedule")
        agent = body.get("agent", "")
        loop_seconds = body.get("loop_seconds", 0)
        if not conv_id or not at_str:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or at"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from datetime import datetime, timezone as tz
        from core.poll_scheduler import PollScheduler
        try:
            dt = datetime.strptime(at_str, "%Y%m%d%H%M%S")
            dt = dt.replace(tzinfo=tz.utc)
            recheck_at = dt.timestamp()
        except ValueError:
            flowfile.set_content(json.dumps({"error": f"Invalid date: {at_str}"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        scheduler = PollScheduler.instance()
        sched_reason = reason
        if agent:
            sched_reason = f"[@{agent}] {reason}"
        if loop_seconds and int(loop_seconds) > 0:
            loop_key = scheduler.schedule_loop(
                conversation_id=conv_id,
                interval_seconds=int(loop_seconds),
                prompt=sched_reason,
                user_id=user_id,
            )
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({
                "scheduled": True, "recurring": True,
                "interval": int(loop_seconds), "key": loop_key,
                "reason": sched_reason,
            }).encode())
        else:
            scheduler.schedule(conv_id, recheck_at, user_id, sched_reason)
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({"scheduled": True, "at": recheck_at}).encode())
        return [flowfile]

    if action == "delete_schedule":
        conv_id = body.get("conversation_id", "")
        key = body.get("key", "").strip()
        from core.poll_scheduler import PollScheduler
        scheduler = PollScheduler.instance()
        if key == "all":
            # Delete all schedules for this conversation
            all_scheds = scheduler.list_all()
            count = 0
            for s in all_scheds:
                if s.get("conversation_id") == conv_id:
                    scheduler.cancel(s.get("key", s.get("conversation_id", "")))
                    count += 1
            flowfile.set_content(json.dumps({"cancelled": count}).encode())
        elif key:
            # Delete by exact key
            cancelled = scheduler.cancel(key)
            if not cancelled:
                # Try matching by index (1-based) from conversation's schedules
                all_scheds = scheduler.list_all()
                conv_scheds = [s for s in all_scheds if s.get("conversation_id") == conv_id]
                try:
                    idx = int(key) - 1
                    if 0 <= idx < len(conv_scheds):
                        actual_key = conv_scheds[idx].get("key", conv_scheds[idx].get("conversation_id", ""))
                        cancelled = scheduler.cancel(actual_key)
                except (ValueError, IndexError):
                    pass
            flowfile.set_content(json.dumps({"cancelled": cancelled}).encode())
        else:
            flowfile.set_content(json.dumps({"error": "Missing key. Use '/schedules del <key>' or '/schedules del all'"}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "create_task_def":
        name = body.get("name", "").strip()
        data = body.get("data", {})
        if not name or not data.get("prompt"):
            flowfile.set_content(json.dumps(
                {"error": "Missing name or prompt"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        data["created_by"] = uid
        try:
            rs.create("task_def", name, uid, data)
            flowfile.set_content(json.dumps(
                {"ok": True, "name": name}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps(
                {"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_task_def":
        name = body.get("name", "").strip()
        if not name:
            flowfile.set_content(json.dumps(
                {"error": "Missing name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        deleted = rs.delete("task_def", name, uid)
        flowfile.set_content(json.dumps(
            {"ok": True, "deleted": deleted}).encode())
        return [flowfile]

    if action == "assign_task":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        task_desc = body.get("task", "") or body.get("task_def_name", "")
        if not conv_id or not agent or not task_desc:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or task"}).encode())
            return [flowfile]
        from core.tool_registry import AssignTaskHandler
        h = AssignTaskHandler()
        h.set_conversation_id(conv_id)
        h.set_agent_name("user")
        h.set_user_id(user_id)
        result = h.execute({
            "agent": agent,
            "task": body.get("task", ""),
            "task_def_name": body.get("task_def_name", ""),
            "completion_criteria": body.get("completion_criteria", ""),
            "interval": body.get("interval"),
            "max_iterations": body.get("max_iterations", 50),
            "verifier": body.get("verifier", ""),
            "variables": body.get("variables"),
        })
        # Ensure poller is running (task needs it for scheduled wake-ups)
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            poller_thread = threading.Thread(
                target=self._poll_conversations,
                args=(poll_interval,),
                daemon=True,
                name="agent-poller",
            )
            poller_thread.start()
            logger.info("Agent poller started (triggered by task assignment)")
        flowfile.set_content(json.dumps({"ok": True, "result": result}).encode())
        return [flowfile]

    if action == "task_status":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
        agent_filter = body.get("agent_name", "")
        tasks_out = []
        for tid, t in all_tasks.items():
            if not isinstance(t, dict):
                continue
            if agent_filter and t.get("agent") != agent_filter:
                continue
            tasks_out.append({
                "task_id": tid, "agent": t.get("agent", ""),
                "task": t.get("task", ""), "status": t.get("status", ""),
                "iterations": t.get("iterations_done", 0),
                "max_iterations": t.get("max_iterations", 50),
                "last_result": t.get("last_result", ""),
                "verifier": t.get("verifier", ""),
                "interval": t.get("interval", 60),
                "task_def_name": t.get("task_def_name", ""),
                "created_by": t.get("created_by", ""),
            })
        # Include library definitions if requested
        defs_out = []
        if body.get("include_library"):
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            all_defs = ResourceStore.instance().list_all("task_def", uid)
            for d in all_defs:
                defs_out.append({
                    "name": d.get("name", ""),
                    "prompt": d.get("prompt", ""),
                    "criteria": d.get("criteria", ""),
                    "default_interval": d.get("default_interval", "6/1m"),
                    "description": d.get("description", ""),
                    "created_by": d.get("created_by", ""),
                })
        flowfile.set_content(json.dumps({
            "tasks": tasks_out, "definitions": defs_out,
        }).encode())
        return [flowfile]

    if action == "task_log":
        task_name = body.get("name", body.get("task_id", ""))
        conv_id = body.get("conversation_id", "")
        if not task_name:
            # Return all task logs
            extras = store.get_extras(conv_id) or {}
            all_logs = {}
            for k, v in extras.items():
                if k.startswith("task_log:") and isinstance(v, list):
                    all_logs[k[9:]] = v  # strip "task_log:" prefix
            flowfile.set_content(json.dumps({"logs": all_logs}).encode())
        else:
            log = store.get_extra(conv_id, f"task_log:{task_name}") or []
            flowfile.set_content(json.dumps({"task": task_name, "log": log}).encode())
        return [flowfile]

    if action in ("pause_task", "resume_task", "cancel_task"):
        conv_id = body.get("conversation_id", "")
        target = body.get("task_id", "") or body.get("agent_name", "")
        if not conv_id or not target:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or task_id/agent_name"}).encode())
            return [flowfile]
        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
        # Find tasks: by task_id or by agent_name (all tasks of that agent)
        matched = {}
        if target in all_tasks:
            matched[target] = all_tasks[target]
        else:
            for tid, t in all_tasks.items():
                if isinstance(t, dict) and t.get("agent") == target:
                    matched[tid] = t
        if not matched:
            flowfile.set_content(json.dumps({"error": f"No task found for '{target}'"}).encode())
            return [flowfile]
        from core.poll_scheduler import PollScheduler
        scheduler = PollScheduler.instance()
        for tid, task in matched.items():

            if action == "cancel_task":
                task["status"] = "cancelled"
                scheduler.cancel(f"{conv_id}::task::{tid}")
                scheduler.cancel(f"{conv_id}::task_verify::{tid}")
            elif action == "pause_task":
                task["status"] = "paused"
                scheduler.cancel(f"{conv_id}::task::{tid}")
            elif action == "resume_task":
                task["status"] = "active"
                scheduler.schedule_delay(
                    conv_id, task.get("interval", 60),
                    key=f"{conv_id}::task::{tid}",
                    reason=f"[agent_task:{tid}] resumed ({task.get('agent', '?')})",
                    user_id=user_id,
                )
            all_tasks[tid] = task
        store.set_extra(conv_id, "agent_tasks", all_tasks)
        flowfile.set_content(json.dumps({
            "ok": True, "affected": list(matched.keys()),
        }).encode())
        return [flowfile]

    # â”€â”€ Image service management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    return None
