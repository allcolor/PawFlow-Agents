"""User-visible notices for the shared summary pyramid and compaction.

The background bucket builder and hot-path compaction used to fail quietly: a
summarizer that kept refusing calls (quota, rate limit) only wrote log lines,
and every compaction meanwhile rebuilt agent contexts from a stale pyramid
header plus a short raw tail, silently dropping everything in between.

Notices are persisted ``display_only`` system rows: they keep their place in
the transcript for the user and never enter an agent context or shared.jsonl.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_ERROR_PREVIEW_CHARS = 300


def publish_notice(conversation_id: str, user_id: str, text: str,
                   event: str, **details: Any) -> None:
    """Persist one transcript-only system notice and publish it live."""
    if not conversation_id:
        raise ValueError("publish_notice requires a conversation_id")
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message
    message = stamp_message({
        "role": "system", "content": text, "display_only": True,
        "source": {"type": "system", "name": "compaction", "event": event,
                   **details},
    }, conversation_id)
    ConversationWriter.for_conversation(conversation_id).enqueue_message(
        message, user_id=user_id,
        sse_events=[{"type": "new_message", "data": dict(message)}])


class SummaryHealth:
    """Tracks failing background summaries per conversation.

    One notice when a conversation's summaries start failing, one when they
    succeed again; the attempts in between only count.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, int] = {}

    def failed(self, cid: str, user_id: str, first_seq: int, last_seq: int,
               error: str) -> None:
        with self._lock:
            count = self._failures.get(cid, 0) + 1
            self._failures[cid] = count
        if count != 1:
            return
        detail = " ".join(str(error or "no summary returned").split())
        if len(detail) > _ERROR_PREVIEW_CHARS:
            detail = detail[:_ERROR_PREVIEW_CHARS] + "..."
        try:
            publish_notice(
                cid, user_id,
                "Background summaries are failing for this conversation "
                f"(messages seq {first_seq}..{last_seq}): {detail}\n"
                "Compaction keeps working, but until summaries resume each "
                "compacted agent context loses the messages the summary does "
                "not cover yet. Check the summarizer service.",
                "summary_failing", first_seq=first_seq, last_seq=last_seq)
        except Exception:
            logger.error("[bg-bucket] summary failure notice failed cid=%s",
                         cid[:8], exc_info=True)

    def succeeded(self, cid: str, user_id: str) -> None:
        with self._lock:
            count = self._failures.pop(cid, 0)
        if not count:
            return
        try:
            publish_notice(
                cid, user_id,
                f"Background summaries resumed after {count} failed "
                "attempt(s); the summary history is catching up.",
                "summary_resumed", failed_attempts=count)
        except Exception:
            logger.error("[bg-bucket] summary recovery notice failed cid=%s",
                         cid[:8], exc_info=True)


@dataclass(frozen=True)
class HistoryGap:
    """Shared rows that neither the pyramid header nor the raw tail cover."""
    first_seq: int
    last_seq: int
    rows: int
    tokens: int

    def agent_note(self) -> str:
        return (
            "\n[History gap]\nMessages seq "
            f"{self.first_seq}..{self.last_seq} ({self.rows} conversation "
            f"messages, ~{self.tokens} tokens) are neither in the summary "
            "above nor in the recent messages below: background summaries "
            "were behind when this context was compacted. Call read_history "
            "for that period before relying on what happened in it.\n")

    def user_notice(self, agent_name: str) -> str:
        return (
            f"Compaction of {agent_name or 'the agent'}'s context dropped "
            f"{self.rows} conversation messages (seq {self.first_seq}.."
            f"{self.last_seq}, ~{self.tokens} tokens) that background "
            "summaries had not covered yet. The agent was told to use "
            "read_history for that period.")


def find_history_gap(conversation_id: str, user_id: str, pyramid_last_seq: int,
                     tail_seqs: Iterable[int]) -> Optional[HistoryGap]:
    """Shared rows strictly between the pyramid and the kept tail.

    Every persisted row takes its seq from one per-conversation counter, so
    seq order is write order across shared.jsonl and agent contexts. A gap is
    reported only when it is larger than what the background builder leaves
    unbucketed in normal operation (it builds once both its message trigger
    and its minimum input size are reached); below that, the gap is the
    builder's normal working set, not lost history.
    """
    positive = [int(s) for s in tail_seqs if int(s or 0) > 0]
    if not conversation_id or not positive:
        return None
    tail_first = min(positive)
    if tail_first <= pyramid_last_seq + 1:
        return None
    from core.bg_bucket_builder import BgBucketBuilder
    from core.conversation_store import ConversationStore
    from core.segmented_jsonl import SegmentedJsonl
    cfg = BgBucketBuilder.instance()._bg_compact_config(conversation_id, user_id)
    log = SegmentedJsonl(
        ConversationStore.instance()._shared_ctx_path(conversation_id))
    if not log.exists():
        return None
    rows = chars = 0
    first = last = 0
    for row in log.iter_rows_in_range("seq", pyramid_last_seq + 1, tail_first - 1):
        seq = int(row.get("seq") or 0)
        if not pyramid_last_seq < seq < tail_first:
            continue
        rows += 1
        first = seq if not first else min(first, seq)
        last = max(last, seq)
        content = row.get("content")
        chars += len(content) if isinstance(content, str) else len(str(content or ""))
    tokens = int(chars / float(cfg["chars_per_token"]))
    normal_rows = int(cfg["l1_trigger_msgs"]) + int(cfg["tail_reserve_msgs"])
    normal_tokens = int(float(cfg["min_input_multiplier"])
                        * int(cfg["bucket_target_tokens"]))
    if rows < normal_rows or tokens < normal_tokens:
        return None
    return HistoryGap(first_seq=first, last_seq=last, rows=rows, tokens=tokens)


def report_history_gap(conversation_id: str, user_id: str, agent_name: str,
                       pyramid_last_seq: int, tail_seqs: Iterable[int]) -> str:
    """Notify the user of a lossy compaction; return the agent's note.

    Returns "" when the compaction loses nothing beyond the normal builder
    lag. Detection or notice failures are logged, never raised: they must not
    abort the compaction they describe.
    """
    try:
        gap = find_history_gap(conversation_id, user_id, pyramid_last_seq,
                               tail_seqs)
    except Exception:
        logger.error("[compact] history gap check failed cid=%s",
                     conversation_id[:8], exc_info=True)
        return ""
    if gap is None:
        return ""
    logger.warning(
        "[compact] history gap cid=%s agent=%s seq %d..%d rows=%d ~%d tokens",
        conversation_id[:8], agent_name, gap.first_seq, gap.last_seq,
        gap.rows, gap.tokens)
    try:
        publish_notice(conversation_id, user_id, gap.user_notice(agent_name),
                       "history_gap", agent=agent_name,
                       first_seq=gap.first_seq, last_seq=gap.last_seq,
                       rows=gap.rows)
    except Exception:
        logger.error("[compact] history gap notice failed cid=%s",
                     conversation_id[:8], exc_info=True)
    return gap.agent_note()
