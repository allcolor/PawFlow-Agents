"""Per-turn git commit helper for conversations.

Why a dedicated module: the agent loop must commit a single, consistent
snapshot of the conversation AFTER the ConversationWriter queue has
drained, otherwise the commit captures a half-written transcript.

Usage (end of agent loop, in finally):
    from core.conversation_git import commit_turn
    commit_turn(conversation_id, reason="turn complete")

Blocking: waits for writer drain, then performs a best-effort git snapshot
outside the hot conversation lock.
"""
import logging

from core.conversation_store import ConversationStore
from core.conversation_writer import ConversationWriter

logger = logging.getLogger(__name__)


def commit_turn(cid: str, reason: str = "turn complete",
                drain_timeout: float = 10.0) -> None:
    """Snapshot the conversation after draining pending writes.

    Called once per agent turn (end of loop). Flushes the writer queue
    first so the commit reflects every message the turn produced.
    Silent no-op when the conversation has no .git dir (e.g. in-memory
    test stores).

    Failures are logged but not raised: a missing git snapshot is
    recoverable from the jsonl files; raising here would kill the
    agent's done-event path and lose the user-visible result.
    """
    if not cid:
        return
    try:
        writer = ConversationWriter.for_conversation(cid)
        writer.flush(timeout=drain_timeout)
    except Exception as e:
        logger.error("[commit_turn] writer drain failed cid=%s: %s",
                     cid[:8], e, exc_info=True)
        # Continue anyway — partial snapshot is better than none.
    try:
        ConversationStore.instance().git_snapshot(cid, reason)
    except Exception as e:
        logger.error("[commit_turn] git_snapshot failed cid=%s: %s",
                     cid[:8], e, exc_info=True)
