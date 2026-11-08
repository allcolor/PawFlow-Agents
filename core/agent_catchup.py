"""Voluntary, bounded reads of one agent's public conversation output."""

import json

from core._conversation_store_base import TRANSCRIPT_GENERATION, ConversationLockedError
from core.agent_inbox import _content_text, _require

DEFAULT_READ_CHARS = 12000
MIN_READ_CHARS = 2000
MAX_READ_CHARS = 40000
_CURSORS_KEY = "agent_catchup_cursors"


def read_catchup(conversation_id: str, reader: str, agent: str, *,
                 max_chars: int = DEFAULT_READ_CHARS) -> str:
    """Advance only this reader/source cursor over text actually returned.

    The transcript is append-ordered by persisted sequence. Segment bounds
    skip older history, and partial messages resume at a durable text offset.
    Rewrites invalidate cursors through the conversation generation counter.
    This read never enqueues work, emits a notice, or changes an inbox.
    """
    from core.conversation_store import ConversationStore

    conversation_id = _require(conversation_id, "conversation_id")
    reader = _require(reader, "reader").lower()
    agent = _require(agent, "agent")
    if (not isinstance(max_chars, int) or isinstance(max_chars, bool)
            or not MIN_READ_CHARS <= max_chars <= MAX_READ_CHARS):
        raise ValueError("max_chars must be an integer between 2000 and 40000")
    store = ConversationStore.instance()
    if not store.exists(conversation_id):
        raise ValueError("conversation does not exist")
    source_key = agent.lower()

    # Match the writer's lock order. Concurrent readers cannot acknowledge
    # the same page, and a transcript rewrite cannot race a cursor commit.
    with store._get_conv_lock(conversation_id), store._get_extras_lock(conversation_id):
        if store._is_encryption_enabled(conversation_id) and store._codec_for(conversation_id) is None:
            raise ConversationLockedError("conversation is encrypted and locked; unlock before catchUp")
        generation = store.get_extra(conversation_id, TRANSCRIPT_GENERATION, 0)
        cursors = dict(store.get_extra(conversation_id, _CURSORS_KEY, {}) or {})
        own = dict(cursors.get(reader, {}))
        previous = own.get(source_key, {})
        state = previous if previous.get("generation") == generation else {}
        next_seq, offset = int(state.get("next_seq", 1)), int(state.get("offset", 0))
        messages = []
        has_more = False

        def encode(rows, more=False):
            return json.dumps({"agent": agent, "messages": rows, "has_more": more},
                              ensure_ascii=False)

        if len(encode([])) > max_chars:
            raise ValueError("catchUp agent metadata exceeds the read budget")

        rows = store._transcript_log(conversation_id).iter_rows_in_range(
            "seq", max(1, next_seq), float("inf"))
        for row in rows:
            seq = int(row.get("seq") or 0)
            if seq < next_seq:
                continue
            source = row.get("source") or {}
            if (row.get("role") != "assistant" or source.get("type") != "agent"
                    or str(source.get("name") or "").lower() != source_key
                    or source.get("task_id") or source.get("btw")
                    or source.get("visibility") == "target_only"
                    or row.get("display_only")):
                next_seq, offset = seq + 1, 0
                continue
            text = _content_text(row)
            start = offset if seq == next_seq else 0
            if not text[start:]:
                next_seq, offset = seq + 1, 0
                continue
            entry = {"msg_id": row["msg_id"], "ts": row.get("ts"),
                     "start": start, "total_chars": len(text), "text": text[start:]}
            if len(messages) < 100 and len(encode(messages + [entry])) <= max_chars:
                messages.append(entry)
                next_seq, offset = seq + 1, 0
                continue
            has_more = True
            if messages:
                break
            # JSON escaping can make the wire size exceed the text length.
            # Find the largest page whose complete serialized result fits.
            low, high = 0, len(text) - start
            while low < high:
                middle = (low + high + 1) // 2
                entry["text"] = text[start:start + middle]
                if len(encode([entry])) <= max_chars:
                    low = middle
                else:
                    high = middle - 1
            if not low:
                raise ValueError("catchUp message metadata exceeds the read budget")
            entry["text"] = text[start:start + low]
            messages.append(entry)
            next_seq, offset = seq, start + low
            break

        current = {"generation": generation, "next_seq": next_seq, "offset": offset}
        if current != previous:
            own[source_key] = current
            cursors[reader] = own
            if not store.set_extra(conversation_id, _CURSORS_KEY, cursors):
                raise RuntimeError("could not persist catchUp cursor")
        return encode(messages, has_more)
