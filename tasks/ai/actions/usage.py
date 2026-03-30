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
        from gui.services.global_service_registry import GlobalServiceRegistry
        tracker = TokenTracker.instance()
        usage = tracker.get_usage(user_id)
        agents_data = usage.get("agents", {})
        req_agent = body.get("agent", "ALL")

        # Build service cost info from registry
        greg = GlobalServiceRegistry.get_instance()
        from core import safe_float

        svc_costs = {}
        for svc_id, svc_def in greg.get_all_definitions().items():
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

    if action == "list_active":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # An agent is active IFF its context is in the active stack.
        # Push on enter, pop on exit (finally). No ghosts possible.
        active = []
        with self._active_contexts_lock:
            ctx = self._active_contexts.get(conv_id)
            if ctx:
                active.append({
                    "agent_name": ctx.get("active_agent_name", ""),
                    "iteration": ctx.get("_iteration", 0),
                    "round": ctx.get("_round", 0),
                    "max_rounds": ctx.get("max_rounds", 0),
                    "last_tool": ctx.get("_last_tool", ""),
                })
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
