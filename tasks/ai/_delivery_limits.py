"""Context limits applied to messages delivered into a running agent turn.

Two rules (2026-09-26) that deliveries outside the agent loop must honour:

* Before every submission, check whether it must compact first. A message
  pasted into a live CLI whose context is at its threshold makes the context
  too large and the model fails. Such a message is not submitted: the running
  turn is asked to compact (which interrupts it and restarts the CLI on the
  compacted context) and the message is delivered after.
* When the messages the CLI accepted but its model has not read exceed 10 %
  of the agent's context and the oldest has waited more than 60 s, a tool call
  is blocking them: it is cancelled, so the CLI reaches its next step and
  reads them.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

BACKLOG_CONTEXT_FRACTION = 0.10
BACKLOG_MAX_WAIT_SECONDS = 60.0
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


def backlog_exceeded(pending: list, max_ctx: int, chars_per_token: float,
                     now: float | None = None) -> bool:
    """Whether accepted-but-unread submissions must unblock the CLI."""
    if not pending or max_ctx <= 0:
        return False
    now = time.time() if now is None else now
    oldest = min(submission.accepted_at for submission in pending)
    if now - oldest <= BACKLOG_MAX_WAIT_SECONDS:
        return False
    tokens = sum(len(submission.text or "") for submission in pending) / max(
        chars_per_token or 4.0, 1.0)
    return tokens > BACKLOG_CONTEXT_FRACTION * max_ctx


def unblock_backlog(pool, state, conversation_id: str, agent_name: str) -> bool:
    """Cancel the agent's running tool calls when its backlog is exceeded."""
    ctx = running_turn_context(conversation_id, agent_name)
    pending = pool.unprocessed_submissions(state)
    if not backlog_exceeded(pending, int(ctx.get("max_context_size") or 0),
                            float(ctx.get("chars_per_token") or 4.0)):
        return False
    logger.warning(
        "[delivery] %s/%s: %d unread message(s), oldest waiting %.0fs, over "
        "%.0f%% of the context — cancelling the blocking tool call",
        conversation_id[:8], agent_name, len(pending),
        time.time() - min(s.accepted_at for s in pending),
        BACKLOG_CONTEXT_FRACTION * 100)
    from services.tool_relay_service import ToolRelayService
    ToolRelayService.cancel_agent(conversation_id, agent_name)
    return True
