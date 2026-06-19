"""AgentLoopTask actions — context ops"""

import logging

from tasks.ai.actions._ctxops_base import (  # noqa: F401  (re-exported helpers)
    _UNHANDLED,
    _estimate_unavailable,
    _find_cc_session_jsonl,
    _rewrite_cc_session,
    _read_jsonl_tail,
    _cc_session_entry_to_msg,
    _load_cc_session_context,
    _text_from_cli_content,
    _load_codex_session_context,
    _load_gemini_session_context,
)
from tasks.ai.actions._ctxops_k1 import _handle_ctxops_k1
from tasks.ai.actions._ctxops_k2 import _handle_ctxops_k2
from tasks.ai.actions._ctxops_k3 import _handle_ctxops_k3
from tasks.ai.actions._ctxops_k4 import _handle_ctxops_k4

logger = logging.getLogger(__name__)


def _handle_context_ops(self, action, body, store, user_id, flowfile):
    """Handle context ops actions. Returns [flowfile] or None."""

    def _ctx_agent_name(agent_name=""):
        """Normalize UI context selectors to store agent names."""
        return "" if agent_name in ("", "ALL", "shared") else agent_name

    def _ctx_load(conv_id, agent_name=""):
        """Load the context the agent actually sees right now.

        Same precedence as agent_loop.py at runtime: agent_context
        first (compacted/diverged view), fallback to the personalized
        transcript only if no per-agent context exists yet (fresh
        conversation). This is the view the Context Editor must show.

        Compaction doesn't use this function — it needs the full
        transcript as source and calls load_transcript_for_agent
        directly.
        """
        if agent_name == "transcript":
            return store.load(conv_id, user_id=user_id) or []
        _name = _ctx_agent_name(agent_name)
        if _name:
            ctx = store.load_agent_context(conv_id, _name)
            if ctx is not None:
                return ctx
            return store.load_transcript_for_agent(conv_id, _name) or []
        shared = store.load_context(conv_id, user_id=user_id)
        return shared if shared is not None else (store.load(conv_id, user_id=user_id) or [])

    def _ctx_save(conv_id, data, agent_name=""):
        """Save context for an agent (or shared if no agent)."""
        if agent_name == "transcript":
            raise ValueError(
                "Transcript is read-only here; delete transcript messages "
                "with delete_message or switch to Shared/an agent context.")
        # "shared" or "" both mean the shared context (agent="")
        _name = _ctx_agent_name(agent_name)
        store.save_agent_context(conv_id, _name, data)
        if _name:
            store.invalidate_claude_session_for_agent(conv_id, _name)
        else:
            store.invalidate_claude_sessions(conv_id)

    def _ctx_cached_usage(conv_id, agent_name=""):
        """Read persisted context gauge without recomputing the full context."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return None
        usage_map = store.get_extra(conv_id, "context_usage", user_id=user_id) or {}
        usage = usage_map.get(_name) if isinstance(usage_map, dict) else None
        if not isinstance(usage, dict) or int(usage.get("max", 0) or 0) <= 0:
            return None
        return {
            "used": int(usage.get("used", 0) or 0),
            "max": int(usage.get("max", 0) or 0),
            "pct": float(usage.get("pct", 0.0) or 0.0),
            "source": usage.get("source", "context_usage_cache"),
            "message_count": usage.get("message_count", 0),
            "cache_mode": usage.get("cache_mode", ""),
            "updated_at": usage.get("updated_at", 0),
            "computed_from": "persisted_context_usage",
        }

    def _ctx_visible_contexts(conv_id, raw_map, selected_agent=""):
        """Return context selector entries that represent real agents/sessions."""
        if not isinstance(raw_map, dict):
            raw_map = {}
        try:
            from core.conv_agent_config import get_all_agent_configs
            active_agents = set((get_all_agent_configs(conv_id) or {}).keys())
        except Exception:
            active_agents = set()
        hidden = {"background", "notification", "system"}
        if user_id:
            hidden.add(user_id)
        try:
            owner = (store._load_cache(conv_id) or {}).get("user_id", "")
            if owner:
                hidden.add(owner)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        visible = {"*": raw_map.get("*", "messages")}
        for name, status in raw_map.items():
            if not name or name == "*" or name in hidden:
                continue
            if active_agents and name not in active_agents and name != selected_agent:
                continue
            visible[name] = status
        return visible

    def _ctx_llm_service_config(conv_id, agent_name=""):
        """Return the llm_service config associated with the selected agent."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return {}
        try:
            from core.conv_agent_config import get_agent_config
            llm_service = (get_agent_config(conv_id, _name).get("llm_service")
                           or "")
            if not llm_service:
                return {}
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            svc = reg.resolve(llm_service, user_id=user_id, conv_id=conv_id)
            if svc:
                return dict(getattr(svc, "config", {}) or {})
            sdef = reg.resolve_definition(
                llm_service, user_id=user_id, conv_id=conv_id)
            return dict(getattr(sdef, "config", {}) or {}) if sdef else {}
        except Exception:
            logger.exception(
                "Failed to resolve llm_service config for compact agent %s",
                _name)
            return {}

    def _ctx_real_context_size(conv_id, agent_name=""):
        """Return provider/CLI real context window when the client exposes it."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return 0
        try:
            from core.conv_agent_config import get_agent_config
            llm_service = (get_agent_config(conv_id, _name).get("llm_service")
                           or "")
            if not llm_service:
                return 0
            from core.service_registry import ServiceRegistry
            svc = ServiceRegistry.get_instance().resolve(
                llm_service, user_id=user_id, conv_id=conv_id)
            client = svc.get_client() if svc and hasattr(svc, "get_client") else None
            if not client:
                return 0
            return int(
                getattr(client, "_real_context_size", 0)
                or getattr(client, "_context_window", 0)
                or 0)
        except Exception:
            return 0

    def _ctx_max_tokens(conv_id, agent_name=""):
        """Get effective max from agent llm_service config capped by provider real window."""
        flow_default = int(self.config.get("max_context_size", 64000))
        cfg = _ctx_llm_service_config(conv_id, agent_name)
        try:
            configured = int((cfg or {}).get("max_context_size", 0) or 0)
        except Exception:
            configured = 0
        from core.context_window import effective_context_window
        return effective_context_window(
            configured, _ctx_real_context_size(conv_id, agent_name),
            fallback=flow_default)
    _helpers = (
        _ctx_agent_name, _ctx_load, _ctx_save, _ctx_cached_usage,
        _ctx_visible_contexts, _ctx_llm_service_config, _ctx_real_context_size, _ctx_max_tokens)
    for _handler in (_handle_ctxops_k1, _handle_ctxops_k2,
                     _handle_ctxops_k3, _handle_ctxops_k4):
        _res = _handler(self, action, body, store, user_id, flowfile, _helpers)
        if _res is not _UNHANDLED:
            return _res
    return None
