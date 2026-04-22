"""Conversation Writer — FIFO write queue for guaranteed message ordering.

All conversation message writes go through this queue. A single writer
thread per conversation dequeues and routes each message through
ConversationStore.append_message in order.

Usage:
    writer = ConversationWriter.for_conversation(conversation_id)
    writer.enqueue_message(msg, agent_name=..., user_id=..., ttl=...)
"""

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT = 300  # 5 minutes idle → writer thread exits


def _require_ts_seq(m: Dict) -> None:
    """Hard invariant: every persisted message MUST carry creation `ts`,
    set at the moment the message was minted (LLMMessage.__post_init__
    or equivalent producer code path). The writer is forbidden to
    invent `ts` — defaulting here would silently cover a producer bug
    and corrupt (ts, seq) ordering on disk. Same rule as msg_id:
    minted once at creation, never substituted. `seq` is NOT required
    here — it is the on-disk line index, assigned at write time by
    ConversationStore._stamp_line under the conv lock.
    """
    if not m.get("ts") and not m.get("timestamp"):
        raise ValueError(
            f"ConversationWriter: missing 'ts' on message — every "
            f"message must have a CREATION timestamp set by its "
            f"producer (no enqueue-time fallback). msg_id="
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
    def shutdown_all(cls, wait_timeout = 30.0):
        """Drain every writer queue, then stop its thread.

        MUST be called before process exit (e.g. in the signal handler
        right before os._exit) - otherwise in-flight writes sitting in
        the queue are silently dropped with the daemon thread.

        Returns True iff every instance drained within the shared
        wait budget. False = at least one write was abandoned; the
        caller should log it loudly (data loss).
        """
        with cls._global_lock:
            instances = list(cls._instances.values())
        deadline = time.monotonic() + max(0.0, float(wait_timeout))
        ok = True
        for w in instances:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                ok = False
                logger.error(
                    "[conv-writer:%s] shutdown drain: out of budget with "
                    "%d item(s) still queued - MESSAGES LOST",
                    w._cid[:8], w._queue.qsize())
                continue
            if not w._drain(remaining):
                ok = False
                logger.error(
                    "[conv-writer:%s] shutdown drain timed out with %d "
                    "item(s) still queued - MESSAGES LOST",
                    w._cid[:8], w._queue.qsize())
        # Only stop threads AFTER draining - setting _stop first would
        # make the writer loop exit while items may still be queued.
        with cls._global_lock:
            for w in cls._instances.values():
                w._stop = True
            cls._instances.clear()
        return ok

    def _drain(self, wait_timeout):
        """Block until every item enqueued so far has been written.

        Uses a flush sentinel: since the writer thread is strictly FIFO,
        the sentinel's event fires only after every prior item has been
        processed. Returns False on timeout.
        """
        if not self._alive:
            return True
        evt = threading.Event()
        self._queue.put({"_flush": True, "_done_event": evt})
        return evt.wait(timeout=wait_timeout)

    def __init__(self, cid: str):
        self._cid = cid
        self._queue: queue.Queue = queue.Queue()
        self._stop = False
        self._alive = True
        self._thread = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"conv-writer-{cid[:8]}")
        self._thread.start()

    def enqueue_message(self, msg: Dict, agent_name: str = "",
                        user_id: str = "", ttl: int = 0,
                        sse_events: List[Dict] = None,
                        wait: bool = False) -> Optional[threading.Event]:
        """Enqueue ONE message through the unified append_message router.

        Preferred API. Routes a single message to every file it belongs
        in (transcript, shared, own ctx, other agents' ctx, delegate
        from/to) based on role+source -- see ConversationStore.
        append_message for routing rules.

        Guarantees: message is fully persisted to disk BEFORE any
        sse_events fire (visible => persisted invariant).
        """
        _require_ts_seq(msg)
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "append_message",
            "msg": msg,
            "agent_name": agent_name,
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
                op = item.get("op", "append_message")
                if op != "append_message":
                    raise ValueError(
                        f"[conv-writer] unknown op: {op!r} (only 'append_message' supported)")
                store.append_message(
                    self._cid, item["msg"],
                    agent_name=item.get("agent_name", ""),
                    user_id=item.get("user_id", ""),
                    ttl=item.get("ttl", 0))
                # Publish SSE events AFTER successful write
                sse_events = item.get("sse_events")
                if sse_events:
                    try:
                        from core.conversation_event_bus import ConversationEventBus
                        bus = ConversationEventBus.instance()
                        for sse_evt in sse_events:
                            # SSE target cid may differ from writer cid
                            # (e.g. task sub-conv writes routed to parent).
                            _evt_cid = sse_evt.get("cid") or self._cid
                            _sub_count = bus.subscriber_count(_evt_cid)
                            logger.info(
                                "[conv-writer:%s] publish %s → cid=%s "
                                "subs=%d",
                                self._cid[:8], sse_evt["type"],
                                _evt_cid[:8], _sub_count)
                            bus.publish_event(
                                _evt_cid, sse_evt["type"], sse_evt.get("data"))
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
