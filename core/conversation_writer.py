"""Conversation Writer — FIFO write queue for guaranteed message ordering.

All conversation message writes go through this queue. A single writer
thread per conversation dequeues and writes to ConversationStore in order.

Usage:
    writer = ConversationWriter.for_conversation(conversation_id)
    writer.enqueue(messages, user_id=..., context_agent=..., status=...)
"""

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT = 300  # 5 minutes idle → writer thread exits


def _require_ts_seq(m: Dict) -> None:
    """Hard invariant: every persisted message MUST carry creation `ts`
    AND `seq`, set at the moment the message was minted (LLMMessage.
    __post_init__ or equivalent producer code path). The writer is
    forbidden to invent these — defaulting them here would silently
    cover a producer bug and corrupt the (ts, seq) ordering on disk.
    Same rule as msg_id: minted once at creation, never substituted.
    """
    if not m.get("ts") and not m.get("timestamp"):
        raise ValueError(
            f"ConversationWriter: missing 'ts' on message — every "
            f"message must have a CREATION timestamp set by its "
            f"producer (no enqueue-time fallback). msg_id="
            f"{m.get('msg_id')!r}, role={m.get('role')!r}")
    if not m.get("seq"):
        raise ValueError(
            f"ConversationWriter: missing 'seq' on message — every "
            f"message must have a CREATION seq set by its producer "
            f"(no enqueue-time fallback). msg_id="
            f"{m.get('msg_id')!r}, role={m.get('role')!r}")


class ConversationWriter:

    _instances: Dict[str, 'ConversationWriter'] = {}
    _global_lock = threading.Lock()

    @classmethod
    def for_conversation(cls, cid: str) -> 'ConversationWriter':
        with cls._global_lock:
            w = cls._instances.get(cid)
            if w and w._alive:
                return w
            w = cls(cid)
            cls._instances[cid] = w
            return w

    @classmethod
    def shutdown_all(cls):
        with cls._global_lock:
            for w in cls._instances.values():
                w._stop = True
            cls._instances.clear()

    def __init__(self, cid: str):
        self._cid = cid
        self._queue: queue.Queue = queue.Queue()
        self._stop = False
        self._alive = True
        self._thread = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"conv-writer-{cid[:8]}")
        self._thread.start()

    def enqueue(self, messages: List[Dict], user_id: str = "",
                context_agent: str = "", status: str = "",
                sse_events: List[Dict] = None,
                wait: bool = False) -> Optional[threading.Event]:
        """Add messages to the write queue. Non-blocking unless wait=True.

        Stamps each message with ts=now if not already present.
        sse_events: list of {"type": str, "data": dict} to publish AFTER write.
        """
        for m in messages:
            _require_ts_seq(m)
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "append",
            "messages": messages,
            "user_id": user_id,
            "context_agent": context_agent,
            "status": status,
            "sse_events": sse_events,
            "_done_event": evt,
        })
        if wait and evt:
            evt.wait(timeout=30)
        return evt

    def enqueue_agent_flush(self, agent_name: str,
                            public_messages: List[Dict],
                            private_messages: List[Dict],
                            user_id: str = "", ttl: int = 0,
                            sse_events: List[Dict] = None,
                            wait: bool = False) -> Optional[threading.Event]:
        """Enqueue an agent_flush operation (transcript + all contexts atomically)."""
        for m in public_messages + private_messages:
            _require_ts_seq(m)
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "agent_flush",
            "agent_name": agent_name,
            "public": public_messages,
            "private": private_messages,
            "user_id": user_id,
            "ttl": ttl,
            "sse_events": sse_events,
            "_done_event": evt,
        })
        if wait and evt:
            evt.wait(timeout=30)
        return evt

    def flush(self, timeout: float = 10.0):
        """Block until all queued messages are written."""
        evt = threading.Event()
        self._queue.put({"_flush": True, "_done_event": evt})
        evt.wait(timeout=timeout)

    def _writer_loop(self):
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        while not self._stop:
            try:
                item = self._queue.get(timeout=_IDLE_TIMEOUT)
            except queue.Empty:
                # Idle timeout — exit thread
                with self._global_lock:
                    if self._queue.empty():
                        self._alive = False
                        ConversationWriter._instances.pop(self._cid, None)
                        return
                continue

            evt = item.get("_done_event")
            if item.get("_flush"):
                if evt:
                    evt.set()
                continue

            try:
                op = item.get("op", "append")
                if op == "agent_flush":
                    store.agent_flush(
                        self._cid, item["agent_name"],
                        public_messages=item.get("public", []),
                        private_messages=item.get("private", []),
                        user_id=item.get("user_id", ""),
                        ttl=item.get("ttl", 0))
                else:
                    msgs = item.get("messages", [])
                    if msgs:
                        store.append_messages(
                            self._cid, msgs,
                            user_id=item.get("user_id", ""),
                            status=item.get("status", ""))
                # Publish SSE events AFTER successful write
                sse_events = item.get("sse_events")
                if sse_events:
                    try:
                        from core.conversation_event_bus import ConversationEventBus
                        bus = ConversationEventBus.instance()
                        for sse_evt in sse_events:
                            bus.publish_event(
                                self._cid, sse_evt["type"], sse_evt.get("data"))
                    except Exception as sse_err:
                        logger.warning("[conv-writer:%s] SSE publish failed: %s",
                                       self._cid[:8], sse_err)
            except Exception as e:
                logger.error("[conv-writer:%s] write failed: %s",
                             self._cid[:8], e, exc_info=True)
            finally:
                if evt:
                    evt.set()

        self._alive = False
