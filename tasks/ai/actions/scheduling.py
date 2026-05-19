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


def _generated_task_def_name(prefix: str, prompt: str,
                             existing: Optional[dict] = None) -> str:
    """Return a short conversation-scoped task definition name."""
    import re
    import uuid
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (prompt or "task").strip()).strip("_").lower()
    if not slug:
        slug = "task"
    slug = slug[:32].strip("_") or "task"
    existing = existing or {}
    for _ in range(20):
        name = f"{prefix}_{slug}_{uuid.uuid4().hex[:8]}"
        if name not in existing:
            return name
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _active_agent_for_conversation(store, conv_id: str) -> str:
    active = store.get_extra(conv_id, "active_resources") or {}
    return active.get("agent", "") or ""


def _create_and_assign_task_def(self, body: Dict[str, Any], store,
                                user_id: str, conv_id: str,
                                *, goal_mode: bool = False) -> Dict[str, Any]:
    """Create a conversation-scoped task definition and assign it immediately."""
    if not conv_id:
        return {"error": "Missing conversation_id"}

    prompt = (body.get("prompt") or body.get("task") or "").strip()
    if not prompt:
        return {"error": "Missing prompt"}

    agent = (body.get("agent_name") or body.get("agent") or "").strip()
    if agent.startswith("@"):
        agent = agent[1:]
    if not agent:
        agent = _active_agent_for_conversation(store, conv_id)
    if not agent:
        return {"error": "Missing agent_name and no selected agent in this conversation"}

    conv_defs = store.get_extra(conv_id, "conversation_task_defs") or {}
    name = (body.get("name") or "").strip()
    if not name:
        name = _generated_task_def_name("goal" if goal_mode else "task", prompt, conv_defs)
    if name in conv_defs:
        return {"error": f"Task definition '{name}' already exists in this conversation"}

    criteria = body.get("criteria", body.get("completion_criteria", ""))
    if criteria is None:
        criteria = ""
    criteria = str(criteria)
    if goal_mode and not criteria.strip():
        # /goal is an objective: the objective text is also the default stop
        # condition. Plain /task inline may intentionally stay open-ended.
        criteria = prompt

    interval = body.get("default_interval") or body.get("interval") or "6/1m"
    skills = body.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    data = {
        "prompt": prompt,
        "criteria": criteria,
        "default_interval": interval,
        "verifier": body.get("verifier", "") or "",
        "skills": skills,
        "description": body.get("description", "") or "",
        "interactive": bool(body.get("interactive", False)),
        "created_by": user_id,
        "inline": True,
        "kind": "goal" if goal_mode else body.get("kind", "inline_task"),
    }
    conv_defs[name] = data
    store.set_extra(conv_id, "conversation_task_defs", conv_defs)

    from core.tool_registry import AssignTaskHandler
    h = AssignTaskHandler()
    h.set_conversation_id(conv_id)
    h.set_agent_name("user")
    h.set_user_id(user_id)
    assign_args = {
        "agent": agent,
        "task_def_name": name,
        "interval": body.get("interval"),
        "max_iterations": int(body.get("max_iterations", 50) or 0),
        "verifier": body.get("verifier", ""),
        "variables": body.get("variables"),
        "context": body.get("context", "isolated"),
        "max_turn_time": body.get("max_turn_time", ""),
        "max_budget": body.get("max_budget", ""),
        "max_total_time": body.get("max_total_time", ""),
        "max_reschedules": body.get("max_reschedules", 0),
        "auto_allow": bool(body.get("auto_allow", False)),
        "interactive": bool(body.get("interactive", False)),
        "depends_on": body.get("depends_on") or [],
        "skills": skills,
    }
    result = h.execute(assign_args)

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
        logger.info("Agent poller started (triggered by inline task assignment)")

    return {"ok": True, "name": name, "agent": agent, "result": result,
            "task_def": data}


def _kill_running_task_agent(self, conv_id: str, task_id: str, agent_name: str, force: bool = True):
    """Kill the running agent thread for a task sub-conversation.

    force=True: kill Claude Code process immediately
    force=False: graceful interrupt, then force-kill after 10s
    """
    from tasks.ai.agent_loop import AgentLoopTask
    _exec = AgentLoopTask._live_instance or self
    sub_cid = f"{conv_id}::task::{task_id}"
    # 1. Bump generation so agent loop detects staleness
    with _exec._conv_gen_lock:
        for k in list(_exec._conv_generation):
            if k.startswith(sub_cid):
                _exec._conv_generation[k] += 1
    # 2. Set interrupt flag
    with _exec._interrupt_lock:
        _exec._conv_interrupt[sub_cid] = True
    # 3. Kill Claude Code subprocess
    _cc_key = f"{sub_cid}:{agent_name}" if agent_name else sub_cid
    with _exec._active_contexts_lock:
        _cc = _exec._active_claude_client.get(_cc_key)
    if _cc and hasattr(_cc, 'cancel_claude_code'):
        if force:
            _cc.cancel_claude_code(force=True)
        else:
            # Graceful interrupt first
            _cc.cancel_claude_code(force=False)
            # Schedule force-kill after 10s if still running
            def _escalate():
                import time as _t
                _t.sleep(10)
                with _exec._active_contexts_lock:
                    _cc2 = _exec._active_claude_client.get(_cc_key)
                if _cc2 and hasattr(_cc2, 'cancel_claude_code'):
                    logger.info("[task:%s] escalating to force-kill after 10s", task_id)
                    _cc2.cancel_claude_code(force=True)
            threading.Thread(target=_escalate, daemon=True,
                           name=f"task-kill-{task_id}").start()
    # 4. Cancel in-flight tool calls
    try:
        from services.tool_relay_service import ToolRelayService
        ToolRelayService.cancel_agent(sub_cid, agent_name)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    # 5. Publish done event to parent conv
    try:
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conv_id, "task_stopped", {
                "task_id": task_id,
                "agent_name": agent_name,
                "force": force,
            })
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    logger.info("[task:%s] agent killed (force=%s)", task_id, force)


def _handle_scheduling(self, action, body, store, user_id, flowfile):
    """Handle scheduling actions. Returns [flowfile] or None."""

    # Most actions below need conv_id; hoisting it here avoids UnboundLocalError
    # when a branch checks `conv_id` without having assigned it locally
    # (previous regression: actions referenced conv_id before it was set).
    conv_id = body.get("conversation_id", "")

    if action == "random_thought":
        return self._handle_random_thought(body, conv_id, user_id, flowfile)

    # Task management

    if action == "list_schedules":
        from core.poll_scheduler import PollScheduler
        all_scheds = PollScheduler.instance().list_all()
        # Filter to current conversation
        scheds = [s for s in all_scheds if s["conversation_id"] == conv_id]
        flowfile.set_content(json.dumps({"schedules": scheds}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "add_schedule":
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
            flowfile.set_content(json.dumps({
                "scheduled": True, "recurring": True,
                "interval": int(loop_seconds), "key": loop_key,
                "reason": sched_reason,
            }).encode())
        else:
            scheduler.schedule(conv_id, recheck_at, user_id, sched_reason)
            flowfile.set_content(json.dumps({"scheduled": True, "at": recheck_at}).encode())
        return [flowfile]

    if action == "delete_schedule":
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
        uid = user_id
        data["created_by"] = uid
        requested_scope = data.pop("scope", body.get("scope", "user"))
        scope = "conversation" if conv_id else requested_scope
        try:
            if scope == "conversation" and conv_id:
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[name] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                from core.resource_store import ResourceStore
                rs = ResourceStore.instance()
                rs.create("task_def", name, uid, data)
            flowfile.set_content(json.dumps(
                {"ok": True, "name": name, "scope": scope}).encode())
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
        uid = user_id
        # Try conversation scope first
        deleted = False
        if conv_id:
            from core.conversation_store import ConversationStore
            cs = ConversationStore.instance()
            conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
            if name in conv_defs:
                del conv_defs[name]
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
                deleted = True
        if not deleted:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            deleted = rs.delete("task_def", name, uid)
        flowfile.set_content(json.dumps(
            {"ok": True, "deleted": deleted}).encode())
        return [flowfile]

    if action in ("create_and_assign_task_def", "goal"):
        result = _create_and_assign_task_def(
            self, body, store, user_id, conv_id, goal_mode=(action == "goal"))
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        if result.get("error"):
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "promote_task_def":
        name = body.get("name", "").strip()
        target_scope = body.get("target_scope", "user")
        if not name:
            flowfile.set_content(json.dumps(
                {"error": "Missing name"}).encode())
            return [flowfile]
        uid = user_id
        # Read from conversation scope
        if not conv_id:
            flowfile.set_content(json.dumps(
                {"error": "No conversation context"}).encode())
            return [flowfile]
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
        if name not in conv_defs:
            flowfile.set_content(json.dumps(
                {"error": f"Task def '{name}' not found in conversation scope"}).encode())
            return [flowfile]
        data = dict(conv_defs[name])
        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            target_uid = "__global__" if target_scope == "global" else uid
            rs.create("task_def", name, target_uid, data)
            # Remove from conversation scope
            del conv_defs[name]
            cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            flowfile.set_content(json.dumps(
                {"ok": True, "name": name, "scope": target_scope}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps(
                {"error": str(e)}).encode())
        return [flowfile]


    if action == "assign_task":
        agent = body.get("agent_name", "")
        task_def_name = body.get("task_def_name", "")
        if not conv_id or not agent or not task_def_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or task_def_name"}).encode())
            return [flowfile]
        from core.tool_registry import AssignTaskHandler
        h = AssignTaskHandler()
        h.set_conversation_id(conv_id)
        h.set_agent_name("user")
        h.set_user_id(user_id)
        result = h.execute({
            "agent": agent,
            "task_def_name": task_def_name,
            "completion_criteria": body.get("completion_criteria", ""),
            "interval": body.get("interval"),
            "max_iterations": body.get("max_iterations", 50),
            "verifier": body.get("verifier", ""),
            "variables": body.get("variables"),
            "context": body.get("context", "isolated"),
            "max_turn_time": body.get("max_turn_time", ""),
            "max_budget": body.get("max_budget", ""),
            "max_total_time": body.get("max_total_time", ""),
            "max_reschedules": body.get("max_reschedules", 0),
            "auto_allow": bool(body.get("auto_allow", False)),
            "interactive": bool(body.get("interactive", False)),
            "depends_on": body.get("depends_on") or [],
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
                "iterations": t.get("reschedule_count", 0),
                "max_iterations": t.get("max_iterations", 50),
                "last_result": t.get("last_result", ""),
                "verifier": t.get("verifier", ""),
                "interval": t.get("interval", 60),
                "task_def_name": t.get("task_def_name", ""),
                "created_by": t.get("created_by", ""),
                "timeout": t.get("timeout", 0),
                "max_budget": t.get("max_budget", 0),
                "max_total_time": t.get("max_total_time", 0),
                "max_reschedules": t.get("max_reschedules", 0),
                "total_cost": t.get("total_cost", 0.0),
                "reschedule_count": t.get("reschedule_count", 0),
                "depends_on": t.get("depends_on", []),
            })
        # Include library definitions if requested
        defs_out = []
        if body.get("include_library"):
            from core.resource_store import ResourceStore
            uid = user_id
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

    if action == "list_templates":
        # Templates are just global task_defs in the repository
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        from core.resource_store import GLOBAL_USER_ID
        all_tasks = rs.list("task_def", GLOBAL_USER_ID)
        templates_out = [{
            "name": t["name"],
            "description": t.get("description", ""),
            "default_interval": t.get("default_interval", ""),
            "has_criteria": bool(t.get("criteria")),
        } for t in all_tasks]
        flowfile.set_content(json.dumps({"templates": templates_out}).encode())
        return [flowfile]

    if action == "task_history":
        task_id = body.get("task_id", "")
        if not conv_id or not task_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or task_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        log = store.get_extra(conv_id, f"task_log:{task_id}") or []
        # Compute aggregates
        total_cost = sum(e.get("cost", 0) for e in log)
        total_duration = sum(e.get("duration_secs", 0) for e in log)
        total_tokens_in = sum(e.get("tokens_in", 0) for e in log)
        total_tokens_out = sum(e.get("tokens_out", 0) for e in log)
        iterations = sum(1 for e in log if e.get("type") in ("progress", "completed"))
        flowfile.set_content(json.dumps({
            "task_id": task_id,
            "log": log,
            "aggregates": {
                "total_cost": round(total_cost, 6),
                "total_duration_secs": round(total_duration, 1),
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "iterations": iterations,
            },
        }).encode())
        return [flowfile]

    if action in ("pause_task", "resume_task", "cancel_task", "delete_task"):
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
                scheduler.cancel(f"{conv_id}::task::{tid}")
                scheduler.cancel(f"{conv_id}::task_verify::{tid}")
                # Force-stop the running task agent immediately
                _kill_running_task_agent(self, conv_id, tid, task.get("agent", ""), force=True)
                # Cleanup sub-conv context + CC session
                _sub_cid = f"{conv_id}::task::{tid}"
                try:
                    store.invalidate_claude_sessions(_sub_cid)
                    store.delete(_sub_cid)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                # Remove instance from agent_tasks — only task_def + log remain
                del all_tasks[tid]
                continue  # skip all_tasks[tid] = task below
            elif action == "pause_task":
                task["status"] = "paused"
                scheduler.cancel(f"{conv_id}::task::{tid}")
                # Interrupt the running task agent, force-stop after 10s
                _kill_running_task_agent(self, conv_id, tid, task.get("agent", ""), force=False)
            elif action == "resume_task":
                task["status"] = "active"
                from core.tool_registry import AssignTaskHandler as _ATH
                scheduler.schedule_delay(
                    conv_id, _ATH._get_task_delay(task),
                    key=f"{conv_id}::task::{tid}",
                    reason=f"[agent_task:{tid}] resumed ({task.get('agent', '?')})",
                    user_id=user_id,
                )
            elif action == "delete_task":
                # Cancel schedules & kill agent first
                scheduler.cancel(f"{conv_id}::task::{tid}")
                scheduler.cancel(f"{conv_id}::task_verify::{tid}")
                _kill_running_task_agent(self, conv_id, tid, task.get("agent", ""), force=True)
                # Cleanup sub-conv context + CC session
                _sub_cid = f"{conv_id}::task::{tid}"
                try:
                    store.invalidate_claude_sessions(_sub_cid)
                    store.delete(_sub_cid)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                # Remove from dict entirely
                del all_tasks[tid]
                # Also delete task log
                store.set_extra(conv_id, f"task_log:{tid}", None)
                continue  # skip all_tasks[tid] = task below
            all_tasks[tid] = task
        store.set_extra(conv_id, "agent_tasks", all_tasks)
        flowfile.set_content(json.dumps({
            "ok": True, "affected": list(matched.keys()),
        }).encode())
        return [flowfile]

    # Image service management
    if action == "edit_task":
        task_id = body.get("task_id", "")
        if not conv_id or not task_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or task_id"}).encode())
            return [flowfile]
        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
        task = all_tasks.get(task_id)
        if not task:
            flowfile.set_content(json.dumps({"error": f"Task '{task_id}' not found"}).encode())
            return [flowfile]
        from core.tool_registry import AssignTaskHandler as _ATH_edit
        _parse_t = _ATH_edit._parse_timeout
        _editable = {
            "max_turn_time": lambda v: _parse_t(str(v)) if v else 0,
            "max_budget": lambda v: float(str(v).strip().lstrip("$")) if v else 0.0,
            "max_total_time": lambda v: _parse_t(str(v)) if v else 0,
            "max_reschedules": lambda v: int(v) if v else 0,
            "max_iterations": lambda v: int(v) if v else 0,
            "interval": lambda v: v,
        }
        _field_map = {"max_turn_time": "timeout"}
        changed = []
        for field, parser in _editable.items():
            if field in body:
                _store_key = _field_map.get(field, field)
                task[_store_key] = parser(body[field])
                changed.append(field)
        if changed:
            all_tasks[task_id] = task
            store.set_extra(conv_id, "agent_tasks", all_tasks)
        flowfile.set_content(json.dumps({"ok": True, "changed": changed}).encode())
        return [flowfile]

    if action == "msg_task":
        task_id = body.get("task_id", "")
        message = body.get("message", "")
        if not conv_id or not task_id or not message:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, task_id, or message"}).encode())
            return [flowfile]
        all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
        task = all_tasks.get(task_id)
        if not task:
            flowfile.set_content(json.dumps({"error": f"Task '{task_id}' not found"}).encode())
            return [flowfile]
        if task.get("status") not in ("active", "paused"):
            flowfile.set_content(json.dumps({"error": f"Task '{task_id}' is {task.get('status', 'unknown')}, cannot send message"}).encode())
            return [flowfile]
        sub_cid = f"{conv_id}::task::{task_id}"
        import uuid as _sched_uuid
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        ConversationWriter.for_conversation(sub_cid).enqueue_message(
            stamp_message({"role": "user", "content": message,
                           "msg_id": _sched_uuid.uuid4().hex[:12]},
                          sub_cid))
        if task.get("status") == "paused":
            task["status"] = "active"
            all_tasks[task_id] = task
            store.set_extra(conv_id, "agent_tasks", all_tasks)
        from core.poll_scheduler import PollScheduler
        sched_key = f"{conv_id}::task::{task_id}"
        PollScheduler.instance().schedule_delay(
            conv_id, 1,
            key=sched_key,
            reason=f"[agent_task:{task_id}] user message injected",
            user_id=user_id,
        )
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conv_id, "task_msg",
            {"task_id": task_id, "message": message, "from": "user"},
        )
        flowfile.set_content(json.dumps({"ok": True, "task_id": task_id}).encode())
        return [flowfile]

    # ── Image service management ──────────────────────────────────

    return None
