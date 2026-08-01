"""Single source of truth for PawFlow agent context gauge calculation."""

from __future__ import annotations
import logging

import threading
import time
from typing import Any, Dict, Optional, Tuple


_CLI_CONTEXT_PROVIDERS = (
    "claude-code",
    "claude-code-interactive",
    "antigravity-interactive",
    "codex-app-server",
    "codex-interactive",
    "gemini",
)
_USAGE_CACHE_LOCK = threading.RLock()
_USAGE_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


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


def _client_real_window(client: Any, conversation_id: str,
                        agent_name: str) -> int:
    """The provider's own context window for this stream, or 0.

    One lookup, used whether or not a turn is running. Two providers can report
    a window, and both write it per (conversation, agent):

      * ``_cli_observed_context_window_by_stream`` -- Codex, derived from the
        TUI's "context left N%" status bar against the prompt size measured on
        the wire. Checked first: it describes the session actually running.
      * ``_cc_context_window_by_stream`` -- Claude Code, from its own
        authoritative ``modelUsage[model].contextWindow``.

    The CC map used to be consulted only while a turn was active. Between turns
    the code reached instead for ``client._real_context_size`` /
    ``client._context_window`` -- attributes assigned NOWHERE in PawFlow, so
    that branch always resolved to 0. The denominator was therefore
    min(configured, real) during a turn and plain `configured` after it: when
    the two differ, the gauge moved at the turn boundary with nothing at all
    behind the move.
    """
    if client is None:
        return 0
    key = (conversation_id, agent_name)
    for attr in ("_cli_observed_context_window_by_stream",
                 "_cc_context_window_by_stream"):
        cw_map = getattr(client, attr, None)
        if not isinstance(cw_map, dict):
            continue
        try:
            value = max(0, int(cw_map.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _service_config(conversation_id: str, agent_name: str, user_id: str,
                    active_ctx: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int, str]:
    """Return llm service config, runtime context window, and provider name."""
    if active_ctx:
        cfg = getattr(active_ctx.get("resolved_svc"), "config", None) or {}
        real = int(active_ctx.get("real_context_size") or 0)
        client = active_ctx.get("client")
        if real <= 0:
            real = _client_real_window(client, conversation_id, agent_name)
        provider = str(active_ctx.get("active_llm_provider", "")
                       or getattr(client, "provider", "") or "")
        return dict(cfg), real, provider
    try:
        from core.conv_agent_config import get_agent_config
        from core.service_registry import ServiceRegistry
        svc_id = (get_agent_config(conversation_id, agent_name).get("llm_service")
                  or "")
        if not svc_id:
            return {}, 0, ""
        registry = ServiceRegistry.get_instance()
        svc = registry.resolve(svc_id, user_id=user_id, conv_id=conversation_id)
        if svc:
            cfg = dict(getattr(svc, "config", {}) or {})
            client = svc.get_client() if hasattr(svc, "get_client") else None
            real = _client_real_window(client, conversation_id, agent_name)
            provider = str(getattr(client, "provider", "")
                           or cfg.get("provider", "") or "")
            return cfg, real, provider
        sdef = registry.resolve_definition(
            svc_id, user_id=user_id, conv_id=conversation_id)
        cfg = dict(getattr(sdef, "config", {}) or {}) if sdef else {}
        return cfg, 0, str(cfg.get("provider", "") or "")
    except Exception:
        return {}, 0, ""


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
        if active_ctx.get("_is_cli_provider"):
            if (not active_ctx.get("_cli_has_session")
                    and not active_ctx.get("_cli_bootstrap_read_seen")):
                # The PawFlow context is externalized in initial_context.md.
                # Before the provider reads that file, none of those stored
                # messages belongs to the new CLI context window.
                return [], None, False
            # Once the bootstrap has been read, merge persisted native blocks
            # with the in-flight delta. context_usage_cache uses the marked
            # read boundary to exclude the externalized PawFlow representation
            # and count the native tool result exactly once.
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


def _cli_bootstrap_tokens(active_ctx: Optional[Dict[str, Any]],
                          conversation_id: str, agent_name: str) -> int:
    if not active_ctx:
        return 0
    client = active_ctx.get("client")
    token_counts = getattr(client, "_cli_bootstrap_tokens_by_stream", None)
    if not isinstance(token_counts, dict):
        return 0
    return max(0, int(token_counts.get((conversation_id, agent_name), 0) or 0))


def _gauge_client(conversation_id: str, agent_name: str, user_id: str,
                  active_ctx: Optional[Dict[str, Any]]) -> Any:
    """Return the LLM client for this agent, active turn or not."""
    if active_ctx and active_ctx.get("client") is not None:
        return active_ctx.get("client")
    try:
        from core.conv_agent_config import get_agent_config
        from core.service_registry import ServiceRegistry
        svc_id = (get_agent_config(conversation_id, agent_name).get("llm_service")
                  or "")
        if not svc_id:
            return None
        svc = ServiceRegistry.get_instance().resolve(
            svc_id, user_id=user_id, conv_id=conversation_id)
        return svc.get_client() if hasattr(svc, "get_client") else None
    except Exception:
        return None


def _observed_cli_context_tokens(conversation_id: str, agent_name: str,
                                 user_id: str,
                                 active_ctx: Optional[Dict[str, Any]]) -> int:
    """Return the prompt size the provider itself reported, or 0.

    Only an observed CLI provider records this (see the Codex interactive
    provider): PawFlow sits on its wire and reads the prompt-token count the
    provider sends with every exchange. That number is the context window's
    real occupancy -- provider system prompt, tool schemas and session history
    included -- none of which PawFlow can enumerate from its own messages.

    Unlike the bootstrap counts this is read WITHOUT an active context too, so
    the gauge survives a conversation switch: the value lives on the resolved
    service client, which outlives any one turn.
    """
    client = _gauge_client(conversation_id, agent_name, user_id, active_ctx)
    counts = getattr(client, "_cli_observed_context_tokens_by_stream", None)
    if not isinstance(counts, dict):
        return 0
    return max(0, int(counts.get((conversation_id, agent_name), 0) or 0))


def context_usage_for_messages(conversation_id: str, agent_name: str,
                               messages: Any, *, svc_cfg: Optional[Dict[str, Any]] = None,
                               real_window: int = 0, provider: str = "",
                               source: str = "context_usage",
                               cache: Optional[Dict[str, Any]] = None,
                               bootstrap_prompt_tokens: int = 0,
                               cli_context_state: str = "") -> Dict[str, Any]:
    """Build a context gauge from an already-loaded message list."""
    svc_cfg = dict(svc_cfg or {})
    from core.token_counter import resolve_token_multiplier
    token_multiplier = resolve_token_multiplier(svc_cfg)
    overhead = max(0, int(bootstrap_prompt_tokens or 0))
    cache_params = cache.get("cache_params", {}) if isinstance(cache, dict) else {}
    if (overhead == 0 and str(provider) in _CLI_CONTEXT_PROVIDERS
            and cache_params.get("accounting_version") == 3):
        overhead = max(0, int(cache.get("overhead_tokens", 0) or 0))
    configured = int(svc_cfg.get("max_context_size", 0) or 0)
    from core.context_window import effective_context_window
    max_ctx = effective_context_window(configured, int(real_window or 0), fallback=0)
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
    from tasks.ai.context_usage_cache import context_usage_from_cache
    usage = context_usage_from_cache(
        messages or [], max_ctx, cache, source=source,
        token_multiplier=token_multiplier,
        overhead=overhead)
    usage.update({
        "conversation_id": conversation_id,
        "agent_name": agent_name,
        "used": int(usage.get("used", 0) or 0),
        "max": int(usage.get("max", 0) or 0),
        "pct": float(usage.get("pct", 0.0) or 0.0),
    })
    if cli_context_state:
        usage["cli_context_state"] = cli_context_state
    return usage


def compute_context_usage(conversation_id: str, agent_name: str, *,
                          user_id: str = "", store: Any = None,
                          owner: Any = None, source: str = "context_usage") -> Dict[str, Any]:
    """Compute the authoritative gauge for one PawFlow agent context.

    used = size(active provider context representation)
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
    svc_cfg, real_window, provider = _service_config(
        conversation_id, agent_name, user_id, active_ctx)
    is_cli = bool(
        (active_ctx and active_ctx.get("_is_cli_provider"))
        or provider in _CLI_CONTEXT_PROVIDERS)
    bootstrap_prompt_tokens = (
        _cli_bootstrap_tokens(active_ctx, conversation_id, agent_name)
        if is_cli else 0)
    observed_tokens = (
        _observed_cli_context_tokens(
            conversation_id, agent_name, user_id, active_ctx)
        if is_cli else 0)
    cli_context_state = ""
    if is_cli:
        if observed_tokens > 0:
            # The provider measured its own prompt. Whatever PawFlow believes
            # about session/bootstrap state, that window is demonstrably full
            # of something and the gauge is no longer an estimate.
            cli_context_state = "active"
        elif (active_ctx and not active_ctx.get("_cli_has_session")
                and not active_ctx.get("_cli_bootstrap_read_seen")):
            cli_context_state = (
                "bootstrap" if bootstrap_prompt_tokens > 0 else "cold")
        else:
            cli_context_state = "active"
    configured = int(svc_cfg.get("max_context_size", 0) or 0)
    if active_ctx and int(active_ctx.get("max_context_size") or 0) > 0:
        configured = int(active_ctx.get("max_context_size") or 0)

    raw_messages, cache, _already_deserialized = _context_messages(
        conversation_id, agent_name, user_id, store, active_ctx)
    if cache is None and active_ctx is None:
        with _USAGE_CACHE_LOCK:
            cached_usage = _USAGE_CACHE.get((conversation_id, agent_name))
            if isinstance(cached_usage, dict):
                cache = dict(cached_usage)
        try:
            if cache is None:
                usage_map = store.get_extra_snapshot(conversation_id, "context_usage", {})
                if not isinstance(usage_map, dict):
                    usage_map = {}
                if not usage_map and hasattr(store, "_read_extras"):
                    raw_extras = store._read_extras(conversation_id) or {}
                    raw_usage = raw_extras.get("context_usage")
                    usage_map = raw_usage if isinstance(raw_usage, dict) else {}
                    if usage_map:
                        with _USAGE_CACHE_LOCK:
                            for aname, entry in usage_map.items():
                                if isinstance(entry, dict):
                                    _USAGE_CACHE[(conversation_id, str(aname))] = dict(entry)
                cached_usage = usage_map.get(agent_name)
                if isinstance(cached_usage, dict):
                    cache = cached_usage
        except Exception:
            logging.getLogger(__name__).debug(
                "context usage snapshot lookup failed", exc_info=True)
    if (is_cli and active_ctx is None and isinstance(cache, dict)
            and cache.get("cli_context_state") == "cold"
            and observed_tokens <= 0):
        usage = dict(cache)
        usage.update({
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "source": source,
            "updated_at": time.time(),
        })
        return usage
    messages = raw_messages or []
    cfg_for_count = dict(svc_cfg)
    if configured > 0:
        cfg_for_count["max_context_size"] = configured
    usage = context_usage_for_messages(
        conversation_id, agent_name, messages, svc_cfg=cfg_for_count,
        real_window=real_window, provider=provider, source=source,
        cache=cache, bootstrap_prompt_tokens=bootstrap_prompt_tokens,
        cli_context_state=cli_context_state)
    if observed_tokens > 0:
        # Measured beats reconstructed. Everything above counted the messages
        # PawFlow holds, which for a CLI session is a subset of the window at
        # best: it cannot see the provider's system prompt or tool schemas,
        # and cannot see a context the provider read from inside a code body
        # at all -- that case counted 0, and a gauge stuck at 0 never trips
        # auto-compaction. Keep max/cache bookkeeping, replace the number.
        max_ctx = int(usage.get("max", 0) or 0)
        usage["used"] = observed_tokens
        usage["pct"] = (observed_tokens / max_ctx) if max_ctx > 0 else 0.0
        usage["context_source_measured"] = True
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return usage


def reset_cli_context_usage(conversation_id: str, agent_name: str, *,
                            user_id: str = "", store: Any = None,
                            source: str = "cli_context_reset") -> Optional[Dict[str, Any]]:
    """Return and install an empty gauge after CLI session invalidation."""
    active_ctx = _active_context(conversation_id, agent_name)
    svc_cfg, real_window, provider = _service_config(
        conversation_id, agent_name, user_id, active_ctx)
    if not ((active_ctx and active_ctx.get("_is_cli_provider"))
            or provider in _CLI_CONTEXT_PROVIDERS):
        return None
    configured = int(svc_cfg.get("max_context_size", 0) or 0)
    if active_ctx and int(active_ctx.get("max_context_size") or 0) > 0:
        configured = int(active_ctx.get("max_context_size") or 0)
    cfg_for_count = dict(svc_cfg)
    if configured > 0:
        cfg_for_count["max_context_size"] = configured
    usage = context_usage_for_messages(
        conversation_id, agent_name, [], svc_cfg=cfg_for_count,
        real_window=real_window, provider=provider, source=source,
        cli_context_state="cold")
    client = active_ctx.get("client") if active_ctx else None
    token_counts = getattr(client, "_cli_bootstrap_tokens_by_stream", None)
    if isinstance(token_counts, dict):
        token_counts.pop((conversation_id, agent_name), None)
    # The measured prompt size describes the session that was just
    # invalidated. Left behind it would hold the gauge at the old window's
    # occupancy until the next exchange reports a new one.
    observed_counts = getattr(
        _gauge_client(conversation_id, agent_name, user_id, active_ctx),
        "_cli_observed_context_tokens_by_stream", None)
    if isinstance(observed_counts, dict):
        observed_counts.pop((conversation_id, agent_name), None)
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if inst:
            with inst._active_contexts_lock:
                for key, ctx in inst._active_contexts.items():
                    if not key.startswith(conversation_id + ":"):
                        continue
                    if _agent_key(ctx.get("active_agent_name", "")) != _agent_key(agent_name):
                        continue
                    ctx["_cli_has_session"] = False
                    ctx["_cli_bootstrap_read_seen"] = False
                    ctx["_context_usage_cache"] = usage
                    ctx.pop("_auto_compact_usage_cache", None)
                    break
    except Exception:
        logging.getLogger(__name__).debug(
            "CLI context gauge reset failed", exc_info=True)
    return usage


def persist_context_usage(conversation_id: str, agent_name: str,
                          usage: Dict[str, Any], *, store: Any = None) -> None:
    if not conversation_id or not agent_name or not usage or int(usage.get("max", 0) or 0) <= 0:
        return
    if store is None:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
    with _USAGE_CACHE_LOCK:
        usage_map = {
            aname: dict(entry)
            for (cid, aname), entry in _USAGE_CACHE.items()
            if cid == conversation_id and isinstance(entry, dict)
        }
        if not usage_map:
            try:
                snap = store.get_extra_snapshot(
                    conversation_id, "context_usage", {})
                if isinstance(snap, dict):
                    usage_map.update({
                        str(aname): dict(entry)
                        for aname, entry in snap.items()
                        if isinstance(entry, dict)
                    })
            except Exception:
                logging.getLogger(__name__).debug(
                    "context usage snapshot merge failed", exc_info=True)
        usage_map[agent_name] = dict(usage)
        _USAGE_CACHE[(conversation_id, agent_name)] = dict(usage)
    if hasattr(store, "set_extra"):
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
        "cli_context_state": usage.get("cli_context_state", ""),
        "updated_at": float(usage.get("updated_at", 0.0) or 0.0),
    }
