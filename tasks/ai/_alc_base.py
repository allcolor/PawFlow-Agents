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


def _svc_config(ctx):
    """Resolved LLM service config dict for the current turn (cost keys)."""
    client = ctx.get("client")
    if client is not None and hasattr(client, "get_cost_config"):
        return client.get_cost_config()
    return getattr(ctx.get("resolved_svc"), 'config', {}) or {}


def _svc_subscription(ctx) -> bool:
    """True when the turn's LLM service is flat-rate/subscription — its
    computed cost is recorded as virtual, never as real spend."""
    val = _svc_config(ctx).get("subscription", False)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _svc_rates(ctx):
    """Extract per-1M token pricing from the resolved LLM service config.

    Returns (cost_in, cost_out, cost_cache_read, cost_cache_write).
    Cache rates default to Anthropic-standard ratios of cost_in when
    not set (read = input * 0.1, write = input * 1.25). All rates are
    $/1M tokens, parsed via safe_float to accept French decimals.
    """
    from core import safe_float
    svc_cfg = _svc_config(ctx)
    cost_in = safe_float(svc_cfg.get("cost_per_1m_input", 0), 0.0)
    cost_out = safe_float(svc_cfg.get("cost_per_1m_output", 0), 0.0)
    cr_cfg = svc_cfg.get("cost_per_1m_cache_read")
    cw_cfg = svc_cfg.get("cost_per_1m_cache_write")
    cost_cache_read = safe_float(cr_cfg, cost_in * 0.1) if cr_cfg not in (None, "") else cost_in * 0.1
    cost_cache_write = safe_float(cw_cfg, cost_in * 1.25) if cw_cfg not in (None, "") else cost_in * 1.25
    return cost_in, cost_out, cost_cache_read, cost_cache_write


def _record_response_usage(st, response):
    """Record one completed provider call and refresh the live usage gauge.

    The outer agent turn may contain many LLM calls separated by tool calls.
    Recording here keeps the ledger live during long-running turns and matches
    its one-event-per-LLM-call contract.  States built by narrow unit tests may
    omit ``_turn_cost_ref``; they still receive token accumulation but do not
    touch the process-wide ledger.
    """
    tokens_in = int(getattr(response, "tokens_in", 0) or 0)
    tokens_out = int(getattr(response, "tokens_out", 0) or 0)
    cache_read = int(getattr(response, "cache_read_tokens", 0) or 0)
    cache_write = int(getattr(response, "cache_creation_tokens", 0) or 0)

    st.total_tokens_in += tokens_in
    st.total_tokens_out += tokens_out
    st.total_cache_read += cache_read
    st.total_cache_write += cache_write

    raw = getattr(response, "raw", None)
    raw = raw if isinstance(raw, dict) else {}
    aggregation_usage = raw.get("_pawflow_aggregation", {}) or {}
    aggregation_usage = (
        aggregation_usage if isinstance(aggregation_usage, dict) else {})
    st._aggregation_usage = aggregation_usage
    advisor_cost_delta = float(
        aggregation_usage.get("advisor_cost_usd_delta", 0) or 0)
    st.ctx["_additional_usage_cost_usd"] = (
        float(st.ctx.get("_additional_usage_cost_usd", 0) or 0)
        + advisor_cost_delta
    )

    turn_cost_ref = getattr(st, "_turn_cost_ref", None)
    if turn_cost_ref is None:
        return response

    try:
        from core.usage_ledger import UsageLedger

        ci, co, ccr, ccw = _svc_rates(st.ctx)
        route_plan = getattr(st.client, "route_plan", None)
        physical_ref = getattr(st.client, "active_service_ref", None)
        call_cost = UsageLedger.instance().record(
            user_id=st.user_id or "system",
            channel=("task" if "::task::" in st.conversation_id else "chat"),
            conversation_id=st.conversation_id,
            agent_name=st.ctx.get("active_agent_name", "") or "",
            llm_service=st.ctx.get("active_llm_service", ""),
            model=(getattr(response, "model", "") or st._client_model),
            provider=getattr(st, "_client_provider", "") or "",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read=cache_read,
            cache_write=cache_write,
            cost_per_1m_input=ci,
            cost_per_1m_output=co,
            cost_per_1m_cache_read=ccr,
            cost_per_1m_cache_write=ccw,
            subscription=_svc_subscription(st.ctx),
            physical_llm_service=(
                physical_ref.service_id if physical_ref
                else st.ctx.get("active_llm_service", "")),
            logical_service_scope=(
                route_plan.router.scope if route_plan else ""),
            logical_service_scope_id=(
                route_plan.router.scope_id if route_plan else ""),
            physical_service_scope=(
                physical_ref.scope if physical_ref else ""),
            physical_service_scope_id=(
                physical_ref.scope_id if physical_ref else ""),
            route_plan_id=(route_plan.plan_id if route_plan else ""),
            route_attempt_id=getattr(
                st.client, "route_attempt_id", "") or "",
            route_attempt_index=getattr(
                st.client, "route_attempt_index", -1),
        )
        turn_cost_ref[0] += call_cost + advisor_cost_delta

        from core.service_registry import _parent_conversation_id
        from core.conversation_event_bus import ConversationEventBus

        gauge_cid = (_parent_conversation_id(st.conversation_id)
                     or st.conversation_id)
        totals = UsageLedger.instance().summary(
            conversation_prefix=gauge_cid)
        ConversationEventBus.instance().publish_event(
            gauge_cid, "usage.updated", {
                "conversation_id": gauge_cid,
                "agent_name": st.ctx.get("active_agent_name", "") or "",
                "turn_cost_usd": turn_cost_ref[0],
                "turn_tokens_in": st.total_tokens_in,
                "turn_tokens_out": st.total_tokens_out,
                "total_usd": totals["cost_usd"],
                "total_virtual_usd": totals["virtual_cost_usd"],
                "total_tokens_in": totals["tokens_in"],
                "total_tokens_out": totals["tokens_out"],
            })
    except Exception as exc:
        logger.debug(
            "[agent:%s] usage tracking error: %s",
            st.conversation_id[:8], exc)
    return response


def _usage_cost_usd(ctx, total_in, total_out,
                    total_cache_read=0, total_cache_write=0):
    """Return REAL cost using the same cache-aware rates as the ledger.

    Subscription services record their rates as VIRTUAL cost — real spend
    is 0, so budgets (`max_budget_usd`) must not count them.
    """
    if _svc_subscription(ctx):
        return float(ctx.get("_additional_usage_cost_usd", 0) or 0)
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
