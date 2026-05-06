"""Single source of truth for PawFlow agent context gauge calculation."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


def _agent_key(agent_name: str) -> str:
    return (agent_name or "").lower()


def _active_context(conversation_id: str, agent_name: str) -> Optional[Dict[str, Any]]:
    """Return the live PawFlow context for this conversation/agent if running."""
    if not conversation_id or not agent_name:
        return None
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if not inst:
            return None
        wanted = _agent_key(agent_name)
        exact_key = f"{conversation_id}:{agent_name}"
        with inst._active_contexts_lock:
            exact = inst._active_contexts.get(exact_key)
            if exact and _agent_key(exact.get("active_agent_name", "")) == wanted:
                return dict(exact)
            for key, ctx in inst._active_contexts.items():
                if not key.startswith(conversation_id + ":"):
                    continue
                if "::task::" in key:
                    continue
                if _agent_key(ctx.get("active_agent_name", "")) == wanted:
                    return dict(ctx)
            for key, ctx in inst._active_contexts.items():
                if not key.startswith(conversation_id + ":"):
                    continue
                if _agent_key(ctx.get("active_agent_name", "")) == wanted:
                    return dict(ctx)
    except Exception:
        return None
    return None


def _service_config(conversation_id: str, agent_name: str, user_id: str,
                    active_ctx: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    """Return llm service config plus runtime provider context window."""
    if active_ctx:
        cfg = getattr(active_ctx.get("resolved_svc"), "config", None) or {}
        real = int(active_ctx.get("real_context_size") or 0)
        if real <= 0:
            client = active_ctx.get("client")
            cw_map = (getattr(client, "_cc_context_window_by_stream", None)
                      if client else None) or {}
            real = int(cw_map.get((conversation_id, agent_name), 0) or 0)
        return dict(cfg), real
    try:
        from core.conv_agent_config import get_agent_config
        from core.service_registry import ServiceRegistry
        svc_id = (get_agent_config(conversation_id, agent_name).get("llm_service")
                  or "")
        if not svc_id:
            return {}, 0
        registry = ServiceRegistry.get_instance()
        svc = registry.resolve(svc_id, user_id=user_id, conv_id=conversation_id)
        if svc:
            cfg = dict(getattr(svc, "config", {}) or {})
            client = svc.get_client() if hasattr(svc, "get_client") else None
            real = int(
                getattr(client, "_real_context_size", 0)
                or getattr(client, "_context_window", 0)
                or 0) if client else 0
            return cfg, real
        sdef = registry.resolve_definition(
            svc_id, user_id=user_id, conv_id=conversation_id)
        return dict(getattr(sdef, "config", {}) or {}) if sdef else {}, 0
    except Exception:
        return {}, 0


def _message_identity(msg: Any) -> Tuple[str, str, str]:
    if isinstance(msg, dict):
        return (
            str(msg.get("msg_id") or msg.get("id") or ""),
            str(msg.get("role") or ""),
            str(msg.get("content") or ""),
        )
    return (
        str(getattr(msg, "msg_id", "") or getattr(msg, "id", "") or ""),
        str(getattr(msg, "role", "") or ""),
        str(getattr(msg, "content", "") or ""),
    )


def _stored_context_messages(conversation_id: str, agent_name: str,
                             store: Any) -> Any:
    ctx_data = store.load_agent_context(conversation_id, agent_name)
    if ctx_data is None:
        ctx_data = store.load_transcript_for_agent(conversation_id, agent_name) or []
    return ctx_data or []


def _context_messages(conversation_id: str, agent_name: str, user_id: str,
                      store: Any, active_ctx: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[Dict[str, Any]], bool]:
    """Return messages, cache, and whether messages are already LLMMessage objects."""
    if active_ctx:
        live_messages = active_ctx.get("messages") or []
        if active_ctx.get("_is_cli_provider") and active_ctx.get("_cli_has_session"):
            stored = list(_stored_context_messages(
                conversation_id, agent_name, store) or [])
            seen = {_message_identity(msg) for msg in stored}
            merged = list(stored)
            for msg in live_messages:
                ident = _message_identity(msg)
                if ident not in seen:
                    merged.append(msg)
                    seen.add(ident)
            return merged, active_ctx.get("_context_usage_cache"), False
        return live_messages, active_ctx.get("_context_usage_cache"), True
    return _stored_context_messages(conversation_id, agent_name, store), None, False


def compute_context_usage(conversation_id: str, agent_name: str, *,
                          user_id: str = "", store: Any = None,
                          owner: Any = None, source: str = "context_usage") -> Dict[str, Any]:
    """Compute the authoritative gauge for one PawFlow agent context.

    used = size(active PawFlow agent context)
    max = effective_context_window(configured max_context_size, provider runtime window)
    pct = used / max
    """
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not agent_name:
        raise ValueError("agent_name is required")
    if store is None:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

    active_ctx = _active_context(conversation_id, agent_name)
    svc_cfg, real_window = _service_config(
        conversation_id, agent_name, user_id, active_ctx)
    configured = int(svc_cfg.get("max_context_size", 0) or 0)
    if active_ctx and int(active_ctx.get("max_context_size") or 0) > 0:
        configured = int(active_ctx.get("max_context_size") or 0)
    from core.context_window import effective_context_window
    max_ctx = effective_context_window(configured, real_window, fallback=0)
    if max_ctx <= 0:
        return {
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "used": 0,
            "max": 0,
            "pct": 0.0,
            "source": source,
            "updated_at": time.time(),
            "message_count": 0,
            "cache_mode": "none",
        }

    raw_messages, cache, _already_deserialized = _context_messages(
        conversation_id, agent_name, user_id, store, active_ctx)
    messages = raw_messages or []

    from core.token_counter import resolve_token_multiplier
    from tasks.ai.context_usage_cache import context_usage_from_cache
    usage = context_usage_from_cache(
        messages, max_ctx, cache, source=source,
        token_multiplier=resolve_token_multiplier(svc_cfg))
    usage.update({
        "conversation_id": conversation_id,
        "agent_name": agent_name,
        "used": int(usage.get("used", 0) or 0),
        "max": int(usage.get("max", 0) or 0),
        "pct": float(usage.get("pct", 0.0) or 0.0),
    })
    if active_ctx is not None:
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if inst:
                with inst._active_contexts_lock:
                    for key, ctx in inst._active_contexts.items():
                        if not key.startswith(conversation_id + ":"):
                            continue
                        if _agent_key(ctx.get("active_agent_name", "")) == _agent_key(agent_name):
                            ctx["_context_usage_cache"] = usage
                            break
        except Exception:
            pass
    return usage


def persist_context_usage(conversation_id: str, agent_name: str,
                          usage: Dict[str, Any], *, store: Any = None) -> None:
    if not conversation_id or not agent_name or not usage or int(usage.get("max", 0) or 0) <= 0:
        return
    if store is None:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
    usage_map = store.get_extra(conversation_id, "context_usage") or {}
    usage_map = dict(usage_map)
    usage_map[agent_name] = dict(usage)
    store.set_extra(conversation_id, "context_usage", usage_map)


def usage_event_payload(usage: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "conversation_id": usage.get("conversation_id", ""),
        "agent_name": usage.get("agent_name", ""),
        "context_used": int(usage.get("used", 0) or 0),
        "context_max": int(usage.get("max", 0) or 0),
        "context_pct": float(usage.get("pct", 0.0) or 0.0),
        "context_source": usage.get("source", "context_usage"),
        "context_message_count": usage.get("message_count", 0),
        "context_cache_mode": usage.get("cache_mode", ""),
        "updated_at": float(usage.get("updated_at", 0.0) or 0.0),
    }
