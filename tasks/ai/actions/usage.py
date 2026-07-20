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
        # Read persistent stats from the usage ledger (survives restarts)
        from core.usage_ledger import UsageLedger
        from core.service_registry import ServiceRegistry
        usage = UsageLedger.instance().user_usage(user_id)
        agents_data = usage.get("agents", {})
        req_agent = body.get("agent", "ALL")

        # Build service cost info from registry
        greg = ServiceRegistry.get_instance()
        from core import safe_float

        svc_costs = {}
        # Walk conv > user > global so user/conv-scoped LLM services get
        # their cost config too; disabled services keep their history costs.
        for svc_def in greg.resolve_by_type(
                "llmConnection", user_id=user_id,
                conv_id=body.get("conversation_id", ""),
                enabled_only=False):
            svc_id = svc_def.service_id
            cost_in = safe_float(svc_def.config.get("cost_per_1m_input", 0))
            cr_cfg = svc_def.config.get("cost_per_1m_cache_read")
            cw_cfg = svc_def.config.get("cost_per_1m_cache_write")
            svc_costs[svc_id] = {
                "cost_per_1m_input": cost_in,
                "cost_per_1m_output": safe_float(svc_def.config.get("cost_per_1m_output", 0)),
                "cost_per_1m_cache_read": (
                    safe_float(cr_cfg, cost_in * 0.1)
                    if cr_cfg not in (None, "") else cost_in * 0.1),
                "cost_per_1m_cache_write": (
                    safe_float(cw_cfg, cost_in * 1.25)
                    if cw_cfg not in (None, "") else cost_in * 1.25),
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
            cache_read = agent_stats.get("cache_read", 0)
            cache_write = agent_stats.get("cache_write", 0)
            calls = agent_stats.get("calls", 0)
            costs = svc_costs.get(svc_id, {})
            cost_in_1m = costs.get("cost_per_1m_input", 0)
            cost_out_1m = costs.get("cost_per_1m_output", 0)
            cost_cache_read_1m = costs.get("cost_per_1m_cache_read", 0)
            cost_cache_write_1m = costs.get("cost_per_1m_cache_write", 0)
            # Cost is the ledger's FROZEN per-event cost (rates in effect
            # at call time) — the per-1M rates below are the CURRENT
            # service config, shown for reference only.
            cost = round(agent_stats.get("cost", 0.0), 6)
            stats.append({
                "agent": agent_name, "llm_service": svc_id,
                "tokens_in": tok_in, "tokens_out": tok_out,
                "cache_read": cache_read, "cache_write": cache_write,
                "calls": calls, "cost": cost,
                "cost_per_1m_input": cost_in_1m,
                "cost_per_1m_output": cost_out_1m,
                "cost_per_1m_cache_read": cost_cache_read_1m,
                "cost_per_1m_cache_write": cost_cache_write_1m,
            })

        flowfile.set_content(json.dumps({
            "services": stats,
            "total_in": usage.get("total_in", 0),
            "total_out": usage.get("total_out", 0),
            "total_cache_read": usage.get("total_cache_read", 0),
            "total_cache_write": usage.get("total_cache_write", 0),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_cost":
        # Per-conversation cost from the ledger (persistent, cost frozen
        # at the service rates in effect when each turn ran)
        conv_id = body.get("conversation_id", "")
        try:
            from core.usage_ledger import UsageLedger
            ledger = UsageLedger.instance()
            if conv_id:
                data = ledger.conversation_cost(conv_id)
            else:
                data = {"total": ledger.total_cost(), "by_model": {}}
            flowfile.set_content(json.dumps({
                "total_usd": round(data.get("total", 0.0), 6),
                "by_model": data.get("by_model", {}),
                "conversation_id": conv_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_context_usage":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.conv_agent_config import get_all_agent_configs
        from tasks.ai.context_usage import compute_context_usage, persist_context_usage

        out = {}
        for agent_name in (get_all_agent_configs(conv_id) or {}).keys():
            usage = compute_context_usage(
                conv_id, agent_name, user_id=user_id, store=store,
                owner=self, source="list_context_usage")
            if int(usage.get("max", 0) or 0) > 0:
                out[agent_name] = usage
                persist_context_usage(conv_id, agent_name, usage, store=store)
        flowfile.set_content(json.dumps({"context_usage": out}, ensure_ascii=False).encode())
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
        # list_active is a status endpoint. It must not publish or hydrate the
        # context gauge: polling stale persisted usage was able to overwrite
        # the live `message_meta` gauge and make the UI alternate values.
        with _exec._active_contexts_lock:
            import time as _time
            import re as _re_active
            active_by_key = {}

            def _matches_conv(key: str) -> bool:
                return key == conv_id or key.startswith(conv_id + ":")

            def _task_id_from_key(key: str) -> str:
                _tm = _re_active.search(r'::task::([^:]+)', key)
                return _tm.group(1) if _tm else ""

            def _row_key(key: str, agent_name: str, task_id: str) -> str:
                if task_id:
                    return key
                return f"{conv_id}:{agent_name}" if agent_name else key

            # Provider-agnostic active turn metadata is created before context
            # preparation and compact, so the panel stays correct while
            # _active_contexts is temporarily empty.
            for _k, turn in getattr(_exec, "_active_turns", {}).items():
                if not _matches_conv(_k):
                    continue
                _aname = turn.get("agent_name", "")
                _task_id = turn.get("task_id", "") or _task_id_from_key(_k)
                _started = turn.get("started_at", 0)
                _row = {
                    "agent_name": _aname,
                    "task_id": _task_id,
                    "iteration": turn.get("iteration", 0),
                    "round": turn.get("round", 0),
                    "max_rounds": turn.get("max_rounds", 0),
                    "last_tool": turn.get("last_tool", ""),
                    "duration_s": _time.time() - _started if _started else 0,
                    "status": turn.get("status", "thinking"),
                    "message_preview": turn.get("message_preview", ""),
                }
                active_by_key[_row_key(_k, _aname, _task_id)] = _row

            for _k, ctx in _exec._active_contexts.items():
                if _k == conv_id or _k.startswith(conv_id + ":"):
                    _started = ctx.get("_started_at", 0)
                    # Extract task_id from key pattern conv::task::t_xxx:agent
                    _task_id = _task_id_from_key(_k)
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
                    active_by_key[_row_key(_k, _aname, _task_id)] = _row
            active.extend(active_by_key.values())
        # Live CLI sessions (Claude Code, Codex, Gemini). Enrich rows that
        # are currently in the active stack. Warm idle sessions are exposed in
        # the side-channel lists below, but must not create Active Agents rows.
        cc_live_list = []
        cci_live_list = []
        codex_live_list = []
        gemini_live_list = []

        def _apply_live(row, ent, prefix):
            reuse_count = int(ent.get("reuse_count", 0) or 0)
            row[f"{prefix}_live"] = bool(ent.get("live")) and reuse_count > 0
            row[f"{prefix}_idle_seconds"] = ent.get("idle_seconds", 0)
            row[f"{prefix}_reuse_count"] = reuse_count
            row[f"{prefix}_lived_seconds"] = ent.get("lived_seconds", 0)

        try:
            from core.cc_live_registry import LiveSessionRegistry
            _cc_entries = [
                e for e in LiveSessionRegistry.instance().status()
                if e.get("conv_id") == conv_id
            ]
            _by_agent = {e["agent_name"]: e for e in _cc_entries}
            for row in active:
                _ent = _by_agent.get(row.get("agent_name"))
                if _ent:
                    _apply_live(row, _ent, "cc")
            cc_live_list = _cc_entries
        except Exception:
            logger.debug("cc_live enrichment failed", exc_info=True)
        try:
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            _cci_entries = InteractiveClaudeCodePool.instance().list_sessions_snapshot(
                user_id, conv_id)
            _by_agent_cci = {e["agent_name"]: e for e in _cci_entries}
            for row in active:
                _ent = _by_agent_cci.get(row.get("agent_name"))
                if _ent:
                    _apply_live(row, _ent, "cci")
            cci_live_list = _cci_entries
        except Exception:
            logger.debug("cci_live enrichment failed", exc_info=True)
        try:
            from core.codex_live_registry import CodexLiveRegistry
            _cdx_entries = [
                e for e in CodexLiveRegistry.instance().status()
                if e.get("conv_id") == conv_id
            ]
            _by_agent_cdx = {e["agent_name"]: e for e in _cdx_entries}
            for row in active:
                _ent = _by_agent_cdx.get(row.get("agent_name"))
                if _ent:
                    _apply_live(row, _ent, "codex")
            codex_live_list = _cdx_entries
        except Exception:
            logger.debug("codex_live enrichment failed", exc_info=True)
        try:
            from core.gemini_live_registry import GeminiLiveRegistry
            _gem_entries = [
                e for e in GeminiLiveRegistry.instance().status()
                if e.get("conv_id") == conv_id
            ]
            _by_agent_gem = {e["agent_name"]: e for e in _gem_entries}
            for row in active:
                _ent = _by_agent_gem.get(row.get("agent_name"))
                if _ent:
                    _apply_live(row, _ent, "gemini")
            gemini_live_list = _gem_entries
        except Exception:
            logger.debug("gemini_live enrichment failed", exc_info=True)

        flowfile.set_content(json.dumps({
            "conversation_id": conv_id,
            "active": active,
            "cc_live": cc_live_list,
            "cci_live": cci_live_list,
            "codex_live": codex_live_list,
            "gemini_live": gemini_live_list,
        }).encode())
        return [flowfile]

    if action == "get_usage":
        try:
            from core.usage_ledger import UsageLedger
            is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
            if is_admin:
                usage = UsageLedger.instance().all_usage()
            else:
                usage = {user_id: UsageLedger.instance().user_usage(user_id)}
            flowfile.set_content(json.dumps({
                "usage": usage,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "usage_conversation":
        # Full cost/token picture of one conversation for the cost panel:
        # totals + by_agent/by_channel/by_model + recent turns, task
        # sub-conversations included.
        conv_id = str(body.get("conversation_id", "") or "")
        if not conv_id:
            flowfile.set_content(json.dumps(
                {"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.usage_ledger import UsageLedger
            out = UsageLedger.instance().conversation_breakdown(conv_id)
            flowfile.set_content(json.dumps(out, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in ("usage_summary", "usage_timeseries", "usage_top",
                  "usage_export"):
        return _handle_usage_query(action, body, user_id, flowfile)

    return None


def _usage_query_filters(body, user_id, flowfile):
    """Common ledger filters for the usage query actions.

    Non-admin callers are always scoped to their own user_id; admins may
    pass user="ALL" (no user filter) or a specific user id. `days` bounds
    the window (default 30).
    """
    is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
    req_user = str(body.get("user", "") or "")
    if is_admin and req_user:
        filter_user = "" if req_user.upper() == "ALL" else req_user
    else:
        filter_user = user_id
    try:
        days = float(body.get("days", 30) or 30)
    except (TypeError, ValueError):
        days = 30.0
    filters = {
        "user_id": filter_user,
        "since": time.time() - days * 86400,
        "conversation_id": str(body.get("conversation_id", "") or ""),
        "agent_name": str(body.get("agent", "") or ""),
        "llm_service": str(body.get("llm_service", "") or ""),
        "channel": str(body.get("channel", "") or ""),
    }
    if str(body.get("model", "") or ""):
        filters["model"] = str(body["model"])
    return filters


def _handle_usage_query(action, body, user_id, flowfile):
    """Ledger query actions: summary / timeseries / top / export."""
    from core.usage_ledger import UsageLedger
    ledger = UsageLedger.instance()
    try:
        filters = _usage_query_filters(body, user_id, flowfile)
        if action == "usage_summary":
            out = {"summary": ledger.summary(**filters)}
        elif action == "usage_timeseries":
            out = {"timeseries": ledger.timeseries(
                bucket=str(body.get("bucket", "day") or "day"),
                group_by=str(body.get("group_by", "") or ""),
                **filters)}
        elif action == "usage_top":
            try:
                limit = int(body.get("limit", 10) or 10)
            except (TypeError, ValueError):
                limit = 10
            out = {"top": ledger.top(
                dimension=str(body.get("dimension", "conversation_id")
                              or "conversation_id"),
                order_by=str(body.get("order_by", "cost_usd")
                             or "cost_usd"),
                limit=limit, **filters)}
        else:  # usage_export
            rows = ledger.export_rows(**filters)
            if str(body.get("format", "") or "").lower() == "csv":
                import csv
                import io
                buf = io.StringIO()
                if rows:
                    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                flowfile.set_content(buf.getvalue().encode("utf-8"))
                flowfile.set_attribute("mime.type", "text/csv")
                return [flowfile]
            out = {"events": rows}
        flowfile.set_content(json.dumps(out, ensure_ascii=False).encode())
    except ValueError as e:
        flowfile.set_content(json.dumps({"error": str(e)}).encode())
        flowfile.set_attribute("http.response.status", "400")
    except Exception as e:
        flowfile.set_content(json.dumps({"error": str(e)}).encode())
    return [flowfile]
