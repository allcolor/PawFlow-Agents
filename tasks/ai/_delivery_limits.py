"""Compaction limits for user messages submitted into a running agent turn.

A user submission that would cross the context threshold asks the running
turn to compact and restart before delivering the message.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Set on a running turn's context; its next append compacts (see
# _alc_maybe_auto_compact_after_append).
COMPACT_REQUEST_KEY = "_compact_before_submit"


def running_turn_context(conversation_id: str, agent_name: str) -> dict:
    """The context dict of the agent's running turn, or {}."""
    from tasks.ai.agent_loop import AgentLoopTask
    inst = AgentLoopTask._live_instance
    if inst is None or not conversation_id or not agent_name:
        return {}
    needle = f"{conversation_id}:{agent_name}".lower()
    with inst._active_contexts_lock:
        for key, ctx in inst._active_contexts.items():
            if key.lower() == needle and isinstance(ctx, dict):
                return ctx
    return {}


def _tokens(text: str, ctx: dict) -> int:
    chars_per_token = float(ctx.get("chars_per_token") or 4.0)
    return int(len(text or "") / max(chars_per_token, 1.0)) + 1


def submission_needs_compaction(conversation_id: str, agent_name: str,
                                text: str) -> bool:
    """True when submitting ``text`` would cross the compaction threshold.

    Asks the running turn to compact when it does. Unknown limits (no
    running turn, no threshold, no measured gauge) never block a message.
    """
    ctx = running_turn_context(conversation_id, agent_name)
    max_ctx = int(ctx.get("max_context_size") or 0)
    fraction = float(ctx.get("_compact_trigger_fraction") or 0.0)
    usage = (ctx.get("_context_usage_cache")
             or ctx.get("_auto_compact_usage_cache") or {})
    used = int(usage.get("used") or 0) if isinstance(usage, dict) else 0
    if max_ctx <= 0 or fraction <= 0 or used <= 0:
        return False
    trigger = int(max_ctx * fraction)
    projected = used + _tokens(text, ctx)
    if projected < trigger:
        return False
    ctx[COMPACT_REQUEST_KEY] = True
    logger.warning(
        "[delivery] %s/%s: submitting %d tokens would reach %d >= %d; "
        "compacting first", conversation_id[:8], agent_name,
        projected - used, projected, trigger)
    return True


def take_compact_request(conversation_id: str, agent_name: str) -> bool:
    """Consume a pending compaction request for this agent's running turn.

    Read by the CLI coordinator's idle probe: a turn that produces no message
    cannot wait for its next append to compact, so the request is served from
    the idle path instead (see ``_probe_compact_request``).
    """
    ctx = running_turn_context(conversation_id, agent_name)
    if not ctx:
        return False
    return bool(ctx.pop(COMPACT_REQUEST_KEY, False))
