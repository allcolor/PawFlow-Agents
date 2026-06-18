"""Per-conversation on-disk seq counter + message stamping for the LLM client.

Split out of llm_client.py. The _msg_seq_persisted dict is re-exported from
core.llm_client and mutated in place by tests/conftest, so it must stay a
single shared object.
"""
from __future__ import annotations

from typing import Any, Dict

# Per-conversation on-disk seq counter.
#
# seq is the on-disk line index — assigned at WRITE time by
# ConversationStore._stamp_line, which reads+advances this counter
# under the per-conv lock. One counter per cid, bootstrapped from the
# store's hot metadata + transcript tail so monotony survives process
# restarts without scanning a long transcript in the append lock.
import threading as _threading
_msg_seq_persisted: Dict[str, int] = {}   # cid -> last seq written to disk
_msg_seq_lock = _threading.Lock()


def _bootstrap_seq_for(conversation_id: str) -> int:
    """Return the max seq already persisted for ``conversation_id``.

    The conversation store keeps `_meta_max_seq` in extras.json and can read
    the latest transcript row from the tail. That is enough for the next
    append because seq is monotonically increasing in disk order; scanning the
    entire transcript here would run under ConversationStore's append lock on
    the first post-restart write.
    """
    if not conversation_id:
        return 0
    try:
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().peek_persisted_max_seq(conversation_id)
    except Exception:
        return 0


def _peek_persisted_seq(conversation_id: str) -> int:
    """Return the highest seq already written to disk for this conv.

    _stamp_line uses ``_peek + 1`` as the next line's seq, then calls
    _record_persisted_seq to advance the counter. Bootstraps from the
    transcript on first access so monotony holds across restarts.
    """
    if not conversation_id:
        return 0
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is not None:
            return cur
    bootstrapped = _bootstrap_seq_for(conversation_id)
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or bootstrapped > cur:
            cur = bootstrapped
            _msg_seq_persisted[conversation_id] = cur
        return cur


def _record_persisted_seq(conversation_id: str, seq: int) -> None:
    """Mark ``seq`` as the latest seq written to disk for this conv."""
    if not conversation_id or not isinstance(seq, int):
        return
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or seq > cur:
            _msg_seq_persisted[conversation_id] = seq


def _next_persisted_seq(conversation_id: str) -> int:
    """Reserve and return the next on-disk seq for a conversation.

    Disk bootstrap can be slow on large conversations, so it must never run
    while the process-wide seq lock is held. The caller still serializes this
    per conversation with ConversationStore's append lock.
    """
    if not conversation_id:
        return 1
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is not None:
            nxt = cur + 1
            _msg_seq_persisted[conversation_id] = nxt
            return nxt
    bootstrapped = _bootstrap_seq_for(conversation_id)
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or bootstrapped > cur:
            cur = bootstrapped
        nxt = cur + 1
        _msg_seq_persisted[conversation_id] = nxt
        return nxt


def _has_persisted_seq(conversation_id: str) -> bool:
    """True when this process already bootstrapped the persisted seq."""
    if not conversation_id:
        return False
    with _msg_seq_lock:
        return conversation_id in _msg_seq_persisted


def _seed_persisted_seq(conversation_id: str, seq: int) -> None:
    """Seed the persisted seq cache from a caller that already scanned disk."""
    if not conversation_id or not isinstance(seq, int):
        return
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or seq > cur:
            _msg_seq_persisted[conversation_id] = seq


def stamp_message(msg: Dict[str, Any],
                   conversation_id: str) -> Dict[str, Any]:
    """Set ts + msg_id on a message dict at CREATION time.

    ``conversation_id`` is required: a message only exists inside a
    conversation.

    Every non-system message MUST have ts + msg_id by the time it
    reaches the writer. seq is NOT stamped here — it is the on-disk
    line index, assigned at write time by
    ConversationStore._stamp_line under the conv lock.

    Producer rule: stamp msg_id + ts at the moment the message is
    conceptually created, NOT at enqueue. The creation timestamp is
    what drives sort order on disk (seq breaks ts ties).
    """
    if not conversation_id:
        raise ValueError(
            "stamp_message requires a non-empty conversation_id")
    import time as _time
    import uuid as _uuid
    if not (msg.get("ts") or msg.get("timestamp")):
        msg["ts"] = _time.time()
    if not msg.get("msg_id"):
        msg["msg_id"] = _uuid.uuid4().hex[:12]
    return msg

