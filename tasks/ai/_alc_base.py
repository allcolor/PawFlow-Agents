"""Shared base for the agent_core split: state bag + module-level helpers."""
import logging


logger = logging.getLogger(__name__)

# loop-control sentinels for the _run_agent_loop_inner split
_ALC_BREAK = object()
_ALC_CONTINUE = object()

_CONTEXT_ACK_PATTERNS = (
    "Understood. I'll continue from where I left off.",
    "Understood. I have the summary and will continue from the recent messages.",
    "Understood. I'll read the conversation history file to get full context, then continue from the recent messages.",
    "Understood, continuing.",
    "Understood.",
    "I'll re-read these files now to restore my working context.",
    "I'll re-read these files now to restore context.",
)

def _strip_context_ack(text: str) -> str:
    """Remove known context-ack prefixes that the LLM may echo."""
    if not text:
        return text
    stripped = text.strip()
    for pat in _CONTEXT_ACK_PATTERNS:
        if stripped == pat:
            return ""
        if stripped.startswith(pat):
            after = stripped[len(pat):].lstrip()
            if after:
                return after
    return text


def _preempt_rescue_requires_retrigger(
    message, provider_completed_at: float, provider: str = "",
    preempt_proven_handled: bool = False,
) -> bool:
    """Return True when a drained preempt rescue still needs a real turn.

    Providers suppress a rescue only after their own session log proves the
    preempt was handled by the completed provider turn. A provider timestamp
    alone only proves the old turn ended, not that the preempting message was
    answered.
    """
    if getattr(message, "_pending_source", "") != "preempt_rescue":
        return True
    if not provider_completed_at:
        return True
    return not preempt_proven_handled


def _apply_bg_results(messages, conversation_id):
    """Apply completed background tool results to in-memory messages."""
    import core.background_tool as _bg
    for m in messages:
        if (m.role == "tool" and isinstance(m.content, str)
                and "Running in background" in m.content
                and getattr(m, 'tool_call_id', None)):
            result = _bg.pop_completed(conversation_id, m.tool_call_id)
            if result is not None:
                m.content = result
                logger.info("[bg-tool] applied result for %s in-memory",
                            m.tool_call_id)


def _svc_rates(ctx):
    """Extract per-1M token pricing from the resolved LLM service config.

    Returns (cost_in, cost_out, cost_cache_read, cost_cache_write).
    Cache rates default to Anthropic-standard ratios of cost_in when
    not set (read = input * 0.1, write = input * 1.25). All rates are
    $/1M tokens, parsed via safe_float to accept French decimals.
    """
    from core import safe_float
    client = ctx.get("client")
    if client is not None and hasattr(client, "get_cost_config"):
        svc_cfg = client.get_cost_config()
    else:
        svc_cfg = getattr(ctx.get("resolved_svc"), 'config', {}) or {}
    cost_in = safe_float(svc_cfg.get("cost_per_1m_input", 0), 0.0)
    cost_out = safe_float(svc_cfg.get("cost_per_1m_output", 0), 0.0)
    cr_cfg = svc_cfg.get("cost_per_1m_cache_read")
    cw_cfg = svc_cfg.get("cost_per_1m_cache_write")
    cost_cache_read = safe_float(cr_cfg, cost_in * 0.1) if cr_cfg not in (None, "") else cost_in * 0.1
    cost_cache_write = safe_float(cw_cfg, cost_in * 1.25) if cw_cfg not in (None, "") else cost_in * 1.25
    return cost_in, cost_out, cost_cache_read, cost_cache_write


def _usage_cost_usd(ctx, total_in, total_out,
                    total_cache_read=0, total_cache_write=0):
    """Return cost using the same cache-aware rates as CostTracker."""
    cost_in, cost_out, cost_cache_read, cost_cache_write = _svc_rates(ctx)
    return (
        total_in / 1_000_000 * cost_in
        + total_out / 1_000_000 * cost_out
        + total_cache_read / 1_000_000 * cost_cache_read
        + total_cache_write / 1_000_000 * cost_cache_write
        + float(ctx.get("_additional_usage_cost_usd", 0) or 0)
    )


def _check_budget(ctx, total_in, total_out,
                  total_cache_read=0, total_cache_write=0):
    """Raise RuntimeError if conversation cost exceeds max_budget_usd."""
    budget = ctx.get("max_budget_usd", 0)
    if not budget:
        return  # no cap
    spent = _usage_cost_usd(
        ctx, total_in, total_out, total_cache_read, total_cache_write)
    if spent >= budget:
        raise RuntimeError(f"Budget exceeded: ${spent:.4f} >= ${budget:.2f} limit")


class _ALCState:
    """Per-call mutable state for _run_agent_loop_inner (split for <=800 lines)."""
    pass
