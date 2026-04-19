"""AgentLoopTask actions — usage"""

import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_usage(self, action, body, store, user_id, flowfile):
    """Handle usage actions. Returns [flowfile] or None."""

    if action == "cost":
        # Read persistent stats from TokenTracker (survives restarts)
        from core.token_tracker import TokenTracker
        from core.service_registry import ServiceRegistry
        tracker = TokenTracker.instance()
        usage = tracker.get_usage(user_id)
        agents_data = usage.get("agents", {})
        req_agent = body.get("agent", "ALL")

        # Build service cost info from registry
        greg = ServiceRegistry.get_instance()
        from core import safe_float

        svc_costs = {}
        for svc_id, svc_def in greg.get_all("global", "").items():
            if getattr(svc_def, "service_type", "") == "llmConnection":
                svc_costs[svc_id] = {
                    "cost_per_1m_input": safe_float(svc_def.config.get("cost_per_1m_input", 0)),
                    "cost_per_1m_output": safe_float(svc_def.config.get("cost_per_1m_output", 0)),
                }

        stats = []
        for key, agent_stats in agents_data.items():
            agent_name = agent_stats.get("agent", "")
            svc_id = agent_stats.get("llm_service", "default")
            # Filter by agent
            if req_agent.upper() != "ALL" and agent_name.lower() != req_agent.lower():
                continue
            tok_in = agent_stats.get("in", 0)
            tok_out = agent_stats.get("out", 0)
            calls = agent_stats.get("calls", 0)
            costs = svc_costs.get(svc_id, {})
            cost_in_1m = costs.get("cost_per_1m_input", 0)
            cost_out_1m = costs.get("cost_per_1m_output", 0)
            cost = 0.0
            if cost_in_1m or cost_out_1m:
                cost = round(tok_in / 1_000_000 * cost_in_1m +
                             tok_out / 1_000_000 * cost_out_1m, 6)
            stats.append({
                "agent": agent_name, "llm_service": svc_id,
                "tokens_in": tok_in, "tokens_out": tok_out,
                "calls": calls, "cost": cost,
                "cost_per_1m_input": cost_in_1m,
                "cost_per_1m_output": cost_out_1m,
            })

        flowfile.set_content(json.dumps({
            "services": stats,
            "total_in": usage.get("total_in", 0),
            "total_out": usage.get("total_out", 0),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_cost":
        # Per-conversation cost from CostTracker (in-memory, model-aware pricing)
        conv_id = body.get("conversation_id", "")
        try:
            from core.cost_tracker import CostTracker
            tracker = CostTracker.instance()
            if conv_id:
                data = tracker.get_conversation_cost(conv_id)
            else:
                data = {"total": tracker.get_total_cost(), "by_model": {}}
            flowfile.set_content(json.dumps({
                "total_usd": round(data.get("total", 0.0), 6),
                "by_model": data.get("by_model", {}),
                "conversation_id": conv_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_active":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # An agent is active IFF its context is in the active stack.
        # Push on enter, pop on exit (finally). No ghosts possible.
        # Use the execution instance (not self — self may be the
        # actions-only dispatcher which has its own empty dict).
        from tasks.ai.agent_loop import AgentLoopTask
        _exec = AgentLoopTask._live_instance or self
        active = []
        # Persisted per-agent context-fill gauge (set by agent_core on each
        # final message_meta). Attached to each active row so the floating
        # panel can render the gauge immediately on page reload, without
        # waiting for loadResources() to populate window._contextUsage.
        from core.conversation_store import ConversationStore as _CS_active
        _ctx_usage_map = _CS_active.instance().get_extra(conv_id, "context_usage") or {}
        with _exec._active_contexts_lock:
            import time as _time
            import re as _re_active
            for _k, ctx in _exec._active_contexts.items():
                if _k == conv_id or _k.startswith(conv_id + ":"):
                    _started = ctx.get("_started_at", 0)
                    # Extract task_id from key pattern conv::task::t_xxx:agent
                    _task_id = ""
                    _tm = _re_active.search(r'::task::([^:]+)', _k)
                    if _tm:
                        _task_id = _tm.group(1)
                    _aname = ctx.get("active_agent_name", "")
                    _row = {
                        "agent_name": _aname,
                        "task_id": _task_id,
                        "iteration": ctx.get("_iteration", 0),
                        "round": ctx.get("_round", 0),
                        "max_rounds": ctx.get("max_rounds", 0),
                        "last_tool": ctx.get("_last_tool", ""),
                        "duration_s": _time.time() - _started if _started else 0,
                    }
                    _cu = _ctx_usage_map.get(_aname)
                    if _cu:
                        _row["context_usage"] = _cu
                    active.append(_row)
        # Also include scheduled tasks (active but between turns)
        try:
            from core.conversation_store import ConversationStore
            all_tasks = ConversationStore.instance().get_extra(conv_id, "agent_tasks") or {}
            _active_task_ids = {a["task_id"] for a in active if a.get("task_id")}
            for tid, task in all_tasks.items():
                if tid not in _active_task_ids and task.get("status") == "active":
                    active.append({
                        "agent_name": task.get("agent", ""),
                        "task_id": tid,
                        "status": "scheduled",
                        "iteration": task.get("reschedule_count", 0),
                    })
        except Exception:
            pass
        flowfile.set_content(json.dumps({"active": active}).encode())
        return [flowfile]

    if action == "get_usage":
        try:
            from core.token_tracker import TokenTracker
            is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
            if is_admin:
                usage = TokenTracker.instance().get_all_usage()
            else:
                usage = {user_id: TokenTracker.instance().get_usage(user_id)}
            flowfile.set_content(json.dumps({
                "usage": usage,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return None
