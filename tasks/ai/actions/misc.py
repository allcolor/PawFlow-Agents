"""AgentLoopTask actions — misc"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_misc(self, action, body, store, user_id, flowfile):
    """Handle misc actions. Returns [flowfile] or None."""


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
        svc_costs = {}
        for svc_id, svc_def in greg.get_all_definitions().items():
            if getattr(svc_def, "service_type", "") == "llmConnection":
                svc_costs[svc_id] = {
                    "cost_per_1m_input": float(svc_def.config.get("cost_per_1m_input", 0) or 0),
                    "cost_per_1m_output": float(svc_def.config.get("cost_per_1m_output", 0) or 0),
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
        now = time.time()
        active = []
        with self._interactions_lock:
            for key, info in list(self._active_interactions.items()):
                if info.get("conversation_id") != conv_id:
                    continue
                # Auto-cleanup stale entries (>10 min)
                if now - info.get("started_at", now) > 600:
                    self._active_interactions.pop(key, None)
                    continue
                active.append({
                    "agent_name": info.get("agent_name", ""),
                    "message_preview": info.get("message_preview", ""),
                    "duration_s": round(now - info.get("started_at", now), 1),
                    "iteration": info.get("iteration", 0),
                    "last_tool": info.get("last_tool", ""),
                    "status": info.get("status", "thinking"),
                })
        flowfile.set_content(json.dumps({"active": active}).encode())
        return [flowfile]

    if action == "model":
        model_value = body.get("model", "").strip()
        agent_name = body.get("agent", "").strip()
        conv_id = body.get("conversation_id", "")
        override_key = f"model_override:{agent_name}"
        if not model_value or model_value == "reset":
            # Clear override
            if conv_id:
                store.set_extra(conv_id, override_key, None, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model override cleared for '{agent_name}'. Using default model.",
            }).encode())
            return [flowfile]
        # Set override
        if conv_id:
            store.set_extra(conv_id, override_key, model_value, user_id=user_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Model override for '{agent_name}' set to: {model_value}",
            "model": model_value,
            "agent": agent_name,
        }).encode())
        return [flowfile]

    if action == "theme":
        conv_id = body.get("conversation_id", "")
        operation = body.get("operation", "set")  # set, get, delete
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if operation == "get":
            css = store.get_extra(conv_id, "custom_css", user_id=user_id) or ""
            flowfile.set_content(json.dumps({"ok": True, "css": css}).encode())
            return [flowfile]
        elif operation == "delete":
            store.set_extra(conv_id, "custom_css", None, user_id=user_id)
            # Push empty CSS via SSE to clear theme live
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conv_id, "theme", {"css": ""})
            except Exception:
                pass
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Theme removed",
            }).encode())
            return [flowfile]
        else:  # set
            css = body.get("css", "")
            if not css:
                flowfile.set_content(json.dumps({"error": "Missing 'css' parameter"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            store.set_extra(conv_id, "custom_css", css, user_id=user_id)
            # Push CSS via SSE for live update
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conv_id, "theme", {"css": css})
            except Exception:
                pass
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Theme applied",
                "css_length": len(css),
            }).encode())
            return [flowfile]

    return None  # Unknown action â€” treat as normal message

    return None
