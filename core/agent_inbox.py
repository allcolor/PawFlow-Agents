"""Agent inbox: every non-user injection, read through ``read_inbox``.

Only the user's own messages are put inline into an agent's prompt. Every
other message (delegate messages and results, background tool results,
scheduled wake-ups, catch-up context, skill events, ...) is stored here as an
inbox item. The agent receives only an imperative notice (``build_notice``)
and must call the ``read_inbox`` tool, which returns the unread items in
order and marks them read: the read is the server-side acknowledgement.
See docs/inbox_delivery.md.

Storage: inbox items are PendingQueue entries (core/agent_inbox_store.py)
whose ``source`` is ``inbox:<kind>``. ``PendingQueue.drain`` never returns
them; only ``read_inbox`` consumes them.
"""

from __future__ import annotations

import logging
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

INBOX_SOURCE_PREFIX = "inbox:"
KIND_KEY = "_inbox_kind"
SENDER_KEY = "_inbox_sender"

KINDS = (
    "delegate_message",
    "delegate_reply",
    "delegate_result",
    "background_result",
    "scheduled_wakeup",
    "continuation",
    "task_checkin",
    "catch_up",
    "skill_event",
    "workflow_proposal",
)

_KIND_LABELS = {
    "delegate_message": "delegate message",
    "delegate_reply": "delegate reply",
    "delegate_result": "delegate result",
    "background_result": "background result",
    "scheduled_wakeup": "scheduled wake-up",
    "continuation": "continuation",
    "task_checkin": "task check-in",
    "catch_up": "catch-up of other participants",
    "skill_event": "skill event",
    "workflow_proposal": "workflow proposal",
}

DEFAULT_READ_CHARS = 40000
MIN_READ_CHARS = 2000
MAX_READ_CHARS = 100000
# Turns that may end with unread items before the agent stops being
# re-triggered and the conversation shows a warning.
MAX_UNREAD_TURNS = 3
MAX_NOTICE_ITEMS = 20
_read_locks: dict[tuple[str, str], threading.Lock] = {}
_read_locks_guard = threading.Lock()


def _store():
    from core.agent_inbox_store import AgentInboxStore
    return AgentInboxStore.instance()


def _require(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def is_inbox_source(source: str) -> bool:
    return str(source or "").startswith(INBOX_SOURCE_PREFIX)


def enqueue_item(conversation_id: str, agent_name: str, message: dict,
                 kind: str, sender: str) -> bool:
    """Store one stamped non-user message as an unread inbox item.

    False when the force-stop fence rejected it. The caller then notifies
    the agent (``notify``) or wakes it; nothing here touches a live CLI.
    """
    conversation_id = _require(conversation_id, "conversation_id")
    agent_name = _require(agent_name, "agent_name")
    if kind not in KINDS:
        raise ValueError(f"unknown inbox kind: {kind!r}")
    if not isinstance(message, dict):
        raise TypeError("inbox message must be a dict")
    entry = dict(message)
    entry[KIND_KEY] = kind
    entry[SENDER_KEY] = _require(sender, "sender")
    from core.pending_queue import PendingQueue
    stored = PendingQueue.for_agent(conversation_id, agent_name).enqueue(
        entry, source=INBOX_SOURCE_PREFIX + kind)
    if stored:
        # A new item ends a streak of turns that read nothing.
        _store().set_notice_state(conversation_id, agent_name, unread_turns=0)
    return stored


def unread_items(conversation_id: str, agent_name: str) -> list:
    """Unread inbox items, oldest first (AgentInboxItem)."""
    store = _store()
    count = store.pending_count(conversation_id, agent_name)
    return list(store.list_pending(
        conversation_id, agent_name, source_prefix=INBOX_SOURCE_PREFIX,
        limit=max(1, count)))


def unread_count(conversation_id: str, agent_name: str) -> int:
    return len(unread_items(conversation_id, agent_name))


class _SourceOnly:
    """What the turn-mode rule reads from a message: its source."""

    def __init__(self, source):
        self.source = source if isinstance(source, dict) else {}


def turn_mode_messages(conversation_id: str, agent_name: str) -> list:
    """Unread items as source-only messages for the turn-mode rule.

    A delegate request read in the next turn makes it a delegate_reply
    turn exactly as when its content was drained inline
    (``_alc_apply_queued_delegate_turn_mode``).
    """
    return [_SourceOnly(item.payload.get("source"))
            for item in unread_items(conversation_id, agent_name)
            if (item.payload.get(KIND_KEY) or item.source[len(INBOX_SOURCE_PREFIX):])
            in {"delegate_message", "delegate_reply"}
            and (item.payload.get("source") or {}).get("type")
            in {"agent_delegate", "a2a", "cross_conversation_delegate"}]


def inline_context_messages(messages: list, agent_name: str) -> list:
    """Exclude recipient copies left in contexts written before migration.

    The author's own output and human messages to this agent stay inline.
    Future participant activity is routed to catch-up inbox items at append.
    """
    agent = agent_name.lower()
    kept = []
    for message in messages:
        source = getattr(message, "source", None) or {}
        kind = source.get("type")
        role = getattr(message, "role", "")
        if kind == "agent_delegate":
            if str(source.get("from") or "").lower() != agent:
                continue
        elif kind == "agent":
            if str(source.get("name") or "").lower() != agent:
                continue
        elif role == "user":
            if kind == "scheduled_wakeup" or source.get("delegate"):
                continue
            if kind == "system" and source.get("name") in {
                    "background", "skills", "workflow_proposal", "catch_up"}:
                continue
            target = str(source.get("target_agent") or "").lower()
            if kind == "user" and target and target not in {agent, "all"}:
                continue
        kept.append(message)
    return kept


def prepare_context_messages(messages: list, conversation_id: str,
                             agent_name: str) -> list:
    """Move historical participant copies into an idempotent catch-up item.

    Targeted deliveries are already represented by the migrated inbox. Shared
    history supplied to a new/cold agent must remain readable as tool data.
    """
    kept = inline_context_messages(messages, agent_name)
    kept_ids = {id(message) for message in kept}
    history = []
    agent = agent_name.lower()
    for message in messages:
        if id(message) in kept_ids:
            continue
        source = getattr(message, "source", None) or {}
        if source.get("type") == "agent_delegate" and str(
                source.get("to") or "").lower() == agent:
            continue
        if source.get("type") not in {"agent", "agent_delegate", "user"}:
            continue
        if source.get("delegate"):
            continue
        history.append({"role": message.role, "content": message.content,
                        "source": source, "msg_id": message.msg_id,
                        "ts": message.timestamp})
    if history:
        identity = json.dumps([row["msg_id"] for row in history])
        message_id = uuid.uuid5(uuid.NAMESPACE_URL,
                               f"pawflow-inbox:{conversation_id}:{agent}:{identity}").hex
        from core.llm_client import stamp_message
        payload = stamp_message({
            "role": "user", "msg_id": message_id,
            "ts": max(row["ts"] for row in history),
            "content": json.dumps(history, ensure_ascii=False, sort_keys=True),
            "source": {"type": "system", "name": "catch_up"}}, conversation_id)
        payload[KIND_KEY] = "catch_up"
        payload[SENDER_KEY] = "conversation participants"
        if _store().enqueue_context_history(conversation_id, agent_name, payload):
            _store().set_notice_state(conversation_id, agent_name, unread_turns=0)
    return kept


def _content_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _size(chars: int) -> str:
    if chars < 10000:
        return f"{chars} chars"
    return f"{round(chars / 1024)} KB"


def _describe(item) -> str:
    payload = item.payload
    kind = payload.get(KIND_KEY) or item.source[len(INBOX_SOURCE_PREFIX):]
    label = _KIND_LABELS.get(kind, kind)
    sender = payload.get(SENDER_KEY) or "system"
    return f"{label} from {sender} ({_size(len(_content_text(payload)))})"


def build_notice(items: list) -> str:
    """The imperative notice for these unread items ('' when none).

    Kind, sender and size only: a content summary invites the model to skip
    the read. Never stored in the inbox itself.
    """
    if not items:
        return ""
    count = len(items)
    noun = "item" if count == 1 else "items"
    lines = [f"[PawFlow inbox: ACTION REQUIRED] You have {count} unread {noun}:"]
    for index, item in enumerate(items[:MAX_NOTICE_ITEMS], 1):
        lines.append(f"  {index}. {_describe(item)}")
    if count > MAX_NOTICE_ITEMS:
        lines.append(f"  ... and {count - MAX_NOTICE_ITEMS} more unread items.")
    lines.append("STOP. Call read_inbox() NOW, before any other tool call or answer.")
    lines.append("Their content is ONLY available through read_inbox(); do not guess it.")
    return "\n".join(lines)


def take_notice(conversation_id: str, agent_name: str, *,
                only_new: bool = False) -> str:
    """The notice to deliver now, recorded as delivered; '' when none.

    ``only_new`` (live delivery): no unread notice is outstanding.
    Otherwise (turn start, re-trigger): whenever anything is unread.
    """
    notice, newest = peek_notice(conversation_id, agent_name,
                                 only_new=only_new)
    if notice:
        record_notice(conversation_id, agent_name, newest)
    return notice


def peek_notice(conversation_id: str, agent_name: str, *,
                only_new: bool = False) -> tuple[str, int]:
    """``take_notice`` without recording it: (notice, newest sequence).

    For a delivery that can fail (a live paste): record it with
    ``record_notice`` only once it went through, so a failed paste leaves
    the next drain to deliver the notice.
    """
    items = unread_items(conversation_id, agent_name)
    if not items:
        return "", 0
    notified, _turns = _store().notice_state(conversation_id, agent_name)
    newest = max(item.sequence for item in items)
    if only_new and any(item.sequence <= notified for item in items):
        return "", newest
    return build_notice(items), newest


def record_notice(conversation_id: str, agent_name: str,
                  newest_sequence: int) -> None:
    _store().mark_notified(conversation_id, agent_name, newest_sequence)
    _store().set_notice_state(conversation_id, agent_name,
                              notified_sequence=int(newest_sequence))


def reset_unread_for_human_input(conversation_id: str, agent_name: str,
                                 messages: list, seen_ids: set) -> None:
    """Reset once per fresh human message, including arrivals during a turn."""
    human_ids = {m.msg_id for m in messages
                 if getattr(m, "_pawflow_current_user_message", False)}
    if human_ids - seen_ids:
        _store().set_notice_state(conversation_id, agent_name, unread_turns=0)
        seen_ids.update(human_ids)


def end_of_turn(conversation_id: str, agent_name: str) -> int:
    """Count a turn that ended with unread items; 0 when none remain.

    The caller re-triggers below ``MAX_UNREAD_TURNS`` and warns at the limit.
    """
    store = _store()
    notified, turns = store.notice_state(conversation_id, agent_name)
    if not unread_items(conversation_id, agent_name):
        if turns:
            store.set_notice_state(conversation_id, agent_name,
                                   unread_turns=0)
        return 0
    turns += 1
    store.set_notice_state(conversation_id, agent_name, unread_turns=turns)
    return turns


def _stamp(item) -> str:
    ts = item.payload.get("ts") or item.payload.get("timestamp")
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%SZ")
    except (TypeError, ValueError):
        return str(item.enqueued_at)


def _header(item, total: int, start: int = 0, end: int | None = None) -> str:
    span = ""
    if start or (end is not None and end < total):
        span = f", chars {start}-{end if end is not None else total} of {total}"
    return (f"--- #{item.sequence} {_describe(item)} at {_stamp(item)}"
            f" msg_id={item.msg_id}{span} ---")


def read_inbox(conversation_id: str, agent_name: str, *,
               max_chars: int = DEFAULT_READ_CHARS, item: str = "",
               offset: int = 0, read_call_id: str = "") -> str:
    """Serialize reads for one recipient so concurrent calls never replay data."""
    conversation_id = _require(conversation_id, "conversation_id")
    agent_name = _require(agent_name, "agent_name")
    with _read_locks_guard:
        lock = _read_locks.setdefault((conversation_id, agent_name.lower()),
                                      threading.Lock())
    with lock:
        return _read_inbox(conversation_id, agent_name, max_chars=max_chars,
                           item=item, offset=offset,
                           read_call_id=read_call_id or uuid.uuid4().hex)


def _read_inbox(conversation_id: str, agent_name: str, *,
                max_chars: int, item: str, offset: int, read_call_id: str) -> str:
    """Render unread items in order and mark the fully returned ones read.

    An item larger than the budget is paged: its page names the
    ``read_inbox(item=..., offset=...)`` call that continues it, and it is
    marked read when its last page has been returned.
    """
    conversation_id = _require(conversation_id, "conversation_id")
    agent_name = _require(agent_name, "agent_name")
    budget = max(MIN_READ_CHARS, min(MAX_READ_CHARS, int(max_chars or 0)
                                     or DEFAULT_READ_CHARS))
    store = _store()
    items = unread_items(conversation_id, agent_name)
    if item:
        items = [entry for entry in items if entry.msg_id == item]
        if not items:
            return f"No unread inbox item {item}."
    if not items:
        return "No unread items."
    parts: list[str] = []
    done: list[str] = []
    used = 0
    for index, entry in enumerate(items):
        text = _content_text(entry.payload)
        saved = store.read_offset(conversation_id, agent_name, entry.msg_id)
        start = max(0, int(offset)) if item else saved
        if start > saved:
            raise ValueError("offset cannot skip unread content")
        remaining = text[start:]
        header = _header(entry, len(text), start, len(text))
        block = header + "\n" + remaining
        more = (f"[{len(items) - index - 1} more unread item(s): call read_inbox() "
                "again now to read them.]") if index + 1 < len(items) else ""
        size = used + bool(parts) + len(block) + (len(more) + 1 if more else 0)
        if size > budget:
            if parts:
                parts.append(f"[{len(items) - index} more unread item(s): "
                             "call read_inbox() again now to read them.]")
                break
            # Reserve the largest header/footer before selecting page content.
            header = _header(entry, len(text), start, len(text) - 1)
            footer = (f'[Item continues: call read_inbox(item="{entry.msg_id}", '
                      f"offset={len(text)}) now to read the rest.]")
            available = min(len(remaining) - 1,
                            budget - len(header) - len(footer) - 2)
            if available <= 0:
                raise ValueError("inbox item metadata exceeds the read budget")
            end = start + available
            parts.extend([_header(entry, len(text), start, end), remaining[:available],
                          f'[Item continues: call read_inbox(item="{entry.msg_id}", '
                          f"offset={end}) now to read the rest.]"])
            store.set_read_offset(conversation_id, agent_name, entry.msg_id, end)
            store.set_notice_state(conversation_id, agent_name, unread_turns=0)
            break
        parts.extend([header, remaining])
        used += bool(used) + len(block)
        done.append(entry.msg_id)
        store.set_notice_state(conversation_id, agent_name, unread_turns=0)
    if done:
        store.mark_read(conversation_id, agent_name, done,
                        read_call_id=read_call_id)
        store.set_notice_state(conversation_id, agent_name, unread_turns=0)
        logger.info("[inbox] %s/%s read %d item(s)", conversation_id[:8],
                    agent_name, len(done))
    return "\n".join(parts)


__all__ = [
    "DEFAULT_READ_CHARS", "INBOX_SOURCE_PREFIX", "KINDS", "MAX_UNREAD_TURNS",
    "build_notice", "end_of_turn", "enqueue_item", "is_inbox_source",
    "peek_notice", "read_inbox", "record_notice", "take_notice",
    "turn_mode_messages", "unread_count", "unread_items",
]
