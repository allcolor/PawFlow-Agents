"""Conversation Writer — FIFO write queue for guaranteed message ordering.

All conversation message writes go through this queue. A single writer
thread per conversation dequeues and routes each message through
ConversationStore.append_message in order.

Usage:
    writer = ConversationWriter.for_conversation(conversation_id)
    writer.enqueue_message(msg, agent_name=..., user_id=..., ttl=...)
"""

import logging
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT = 300  # 5 minutes idle → writer thread exits
_WRITER_BATCH_MAX = 64
_WRITER_FIRST_DRAIN_DELAY_SECONDS = float(
    os.getenv("PAWFLOW_WRITER_FIRST_DRAIN_DELAY_MS", "10") or "10") / 1000.0


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
            if w and w._can_accept_writes():
                return w
            if w:
                logger.error(
                    "[conv-writer:%s] replacing dead writer with %d queued item(s)",
                    cid[:8], w._queue.qsize())
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
        if not self._can_accept_writes():
            return self._queue.empty()
        evt = threading.Event()
        self._queue.put({"_flush": True, "_done_event": evt})
        return evt.wait(timeout=wait_timeout)

    def __init__(self, cid: str):
        self._cid = cid
        self._queue: queue.Queue = queue.Queue()
        self._stop = False
        self._alive = True
        self._prewarmed_agents = set()
        self._first_drain_delay_applied = False
        self._thread = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"conv-writer-{cid[:8]}")
        self._thread.start()

    def _can_accept_writes(self) -> bool:
        return self._alive and self._thread.is_alive() and not self._stop

    def _ensure_can_accept_writes(self) -> None:
        if self._can_accept_writes():
            return
        self._alive = False
        with self._global_lock:
            if ConversationWriter._instances.get(self._cid) is self:
                ConversationWriter._instances.pop(self._cid, None)
        raise RuntimeError(
            f"ConversationWriter for {self._cid} is not running; "
            "call ConversationWriter.for_conversation() to get a live writer")

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
        self._ensure_can_accept_writes()
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
        self._ensure_can_accept_writes()
        evt = threading.Event()
        self._queue.put({"_flush": True, "_done_event": evt})
        evt.wait(timeout=timeout)

    def enqueue_sse_events(self, sse_events: List[Dict],
                           wait: bool = False) -> Optional[threading.Event]:
        """Publish SSE events after all previously queued writes drain.

        This is a FIFO barrier without blocking the caller. It preserves the
        visible => persisted ordering for turn-final events such as `done`,
        while keeping slow writer/store cleanup out of the agent hotpath.
        """
        self._ensure_can_accept_writes()
        evt = threading.Event() if wait else None
        self._queue.put({
            "op": "publish_events",
            "sse_events": sse_events or [],
            "_done_event": evt,
        })
        if wait and evt:
            evt.wait(timeout=30)
        return evt

    def _publish_sse_events(self, item: Dict[str, Any]) -> None:
        sse_events = item.get("sse_events")
        if not sse_events:
            return
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()
        for sse_evt in sse_events:
            # SSE target cid may differ from writer cid
            # (e.g. task sub-conv writes routed to parent).
            _evt_cid = sse_evt.get("cid") or self._cid
            _sub_count = -1
            try:
                _sub_count = bus.subscriber_count(_evt_cid)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            logger.info(
                "[conv-writer:%s] publish %s → cid=%s subs=%d",
                self._cid[:8], sse_evt["type"], _evt_cid[:8], _sub_count)
            try:
                bus.publish_event(
                    _evt_cid, sse_evt["type"], sse_evt.get("data"))
            except Exception as sse_err:
                logger.warning(
                    "[conv-writer:%s] SSE publish failed: %s",
                    self._cid[:8], sse_err)

    def _writer_loop(self):
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        def flush_before_sse() -> None:
            try:
                flush_handles = getattr(store, "flush_append_handles", None)
                if callable(flush_handles):
                    flush_handles(self._cid)
            except Exception:
                logger.debug("[conv-writer:%s] append-handle flush failed before SSE",
                             self._cid[:8], exc_info=True)

        def prewarm_before_write(agent_name: str = "") -> None:
            agent_name = agent_name or ""
            key = agent_name or "__conversation__"
            if not hasattr(self, "_prewarmed_agents"):
                self._prewarmed_agents = set()
            if key in self._prewarmed_agents:
                return
            try:
                prewarm = getattr(store, "prewarm_append_targets", None)
                if callable(prewarm):
                    prewarm(self._cid, agent_name)
                self._prewarmed_agents.add(key)
            except Exception:
                logger.debug("[conv-writer:%s] append prewarm failed",
                             self._cid[:8], exc_info=True)

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

            if item.get("_flush"):
                try:
                    flush_handles = getattr(store, "flush_append_handles", None)
                    if callable(flush_handles):
                        flush_handles(self._cid)
                    else:
                        from core.segmented_jsonl import SegmentedJsonl
                        SegmentedJsonl.flush_all_append_handles()
                except Exception:
                    logger.debug("[conv-writer:%s] append-handle flush failed",
                                 self._cid[:8], exc_info=True)
                evt = item.get("_done_event")
                if evt:
                    evt.set()
                continue

            if item.get("op") == "publish_events":
                flush_before_sse()
                _publish_started = time.monotonic()
                self._publish_sse_events(item)
                _publish_ms = ((time.monotonic() - _publish_started)
                               * 1000.0)
                logger.info(
                    "[conv-writer:%s] publish-events queued=%d "
                    "publish_ms=%.1f total_ms=%.1f",
                    self._cid[:8], self._queue.qsize(), _publish_ms,
                    _publish_ms)
                evt = item.get("_done_event")
                if evt:
                    evt.set()
                continue

            if not hasattr(self, "_first_drain_delay_applied"):
                self._first_drain_delay_applied = True
            if (not self._first_drain_delay_applied
                    and _WRITER_FIRST_DRAIN_DELAY_SECONDS > 0):
                self._first_drain_delay_applied = True
                time.sleep(_WRITER_FIRST_DRAIN_DELAY_SECONDS)

            batch = [item]
            flush_events = []
            post_events = []
            while len(batch) < _WRITER_BATCH_MAX and not item.get("sse_events"):
                try:
                    next_item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if next_item.get("_flush"):
                    evt = next_item.get("_done_event")
                    if evt:
                        flush_events.append(evt)
                    break
                if next_item.get("op") == "publish_events":
                    post_events.append(next_item)
                    break
                batch.append(next_item)
                if next_item.get("sse_events"):
                    break

            _batch_started = time.monotonic()
            written = []
            _prewarm_ms = 0.0
            _write_ms = 0.0
            _publish_ms = 0.0
            i = 0
            while i < len(batch):
                write_item = batch[i]
                try:
                    op = write_item.get("op", "append_message")
                    if op != "append_message":
                        raise ValueError(
                            f"[conv-writer] unknown op: {op!r} (only 'append_message' supported)")

                    can_batch = (
                        not write_item.get("sse_events")
                        and hasattr(store, "append_messages"))
                    if can_batch:
                        run = [write_item]
                        batch_agent = write_item.get("agent_name", "")
                        batch_user = write_item.get("user_id", "")
                        batch_ttl = write_item.get("ttl", 0)
                        j = i + 1
                        while j < len(batch):
                            next_item = batch[j]
                            next_op = next_item.get("op", "append_message")
                            if next_op != "append_message":
                                raise ValueError(
                                    f"[conv-writer] unknown op: {next_op!r} (only 'append_message' supported)")
                            if next_item.get("sse_events"):
                                break
                            if (next_item.get("agent_name", "") != batch_agent
                                    or next_item.get("user_id", "") != batch_user
                                    or next_item.get("ttl", 0) != batch_ttl):
                                break
                            run.append(next_item)
                            j += 1
                        _prewarm_started = time.monotonic()
                        prewarm_before_write(batch_agent)
                        _prewarm_ms += ((time.monotonic() - _prewarm_started)
                                        * 1000.0)
                        _write_started = time.monotonic()
                        store.append_messages(self._cid, run)
                        _write_ms += ((time.monotonic() - _write_started)
                                      * 1000.0)
                        written.extend(run)
                        i = j
                        continue

                    _prewarm_started = time.monotonic()
                    prewarm_before_write(write_item.get("agent_name", ""))
                    _prewarm_ms += ((time.monotonic() - _prewarm_started)
                                    * 1000.0)
                    _write_started = time.monotonic()
                    store.append_message(
                        self._cid, write_item["msg"],
                        agent_name=write_item.get("agent_name", ""),
                        user_id=write_item.get("user_id", ""),
                        ttl=write_item.get("ttl", 0))
                    _write_ms += ((time.monotonic() - _write_started)
                                  * 1000.0)
                    written.append(write_item)
                    flush_before_sse()
                    _publish_started = time.monotonic()
                    self._publish_sse_events(write_item)
                    _publish_ms += ((time.monotonic() - _publish_started)
                                    * 1000.0)
                    i += 1
                except Exception as e:
                    logger.error("[conv-writer:%s] write failed: %s",
                                 self._cid[:8], e, exc_info=True)
                    i += 1

            for event_item in post_events:
                flush_before_sse()
                _publish_started = time.monotonic()
                self._publish_sse_events(event_item)
                _publish_ms += ((time.monotonic() - _publish_started)
                                * 1000.0)

            logger.info(
                "[conv-writer:%s] batch size=%d written=%d queued=%d "
                "prewarm_ms=%.1f write_ms=%.1f publish_ms=%.1f total_ms=%.1f",
                self._cid[:8], len(batch), len(written), self._queue.qsize(),
                _prewarm_ms, _write_ms, _publish_ms,
                (time.monotonic() - _batch_started) * 1000.0)

            for write_item in batch:
                evt = write_item.get("_done_event")
                if evt:
                    evt.set()
            for evt in flush_events:
                evt.set()
            for event_item in post_events:
                evt = event_item.get("_done_event")
                if evt:
                    evt.set()

        self._alive = False
