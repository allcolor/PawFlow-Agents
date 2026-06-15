"""Conversation Event Bus — pub/sub for SSE events by conversation_id.

Allows multiple producers (agent loops, timers) to publish events for a
conversation, and multiple consumers (SSE streams) to subscribe and receive
them in real time.

Includes a replay buffer so late subscribers (SSE connects after agent starts)
receive events that were published before they subscribed.

Thread-safe singleton.
"""

import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.sse_writer import SSEEvent, SSEWriter

logger = logging.getLogger(__name__)

# Max events to buffer per conversation before a subscriber connects
_MAX_BUFFER = 200
# How long to keep buffered events (seconds)
_BUFFER_TTL = 60
# Max pending listener events buffered per conversation before the oldest are
# dropped. Guards memory when a sink (e.g. the Telegram bridge) hangs and its
# lane backs up faster than it drains.
_MAX_LISTENER_LANE = 2000


def _default_listener_threads() -> int:
    """Worker count for the listener fan-out pool.

    Listeners (the Telegram bridge, etc.) block on network I/O, not CPU, so the
    pool is sized above the core count. It stays bounded so a stuck sink cannot
    spawn unbounded threads. Override with PAWFLOW_EVENT_LISTENER_THREADS.
    """
    try:
        override = int(os.getenv("PAWFLOW_EVENT_LISTENER_THREADS") or 0)
        if override > 0:
            return override
    except (TypeError, ValueError):
        pass
    cpu = os.cpu_count() or 4
    return max(8, min(64, cpu * 4))


class _ListenerDispatcher:
    """Runs bus listeners off the publishing thread.

    Listeners may do blocking network I/O (the Telegram bridge POSTs to the
    Telegram API with a 60s socket timeout). Running them inline on the
    conversation-writer thread stalls SSE delivery to EVERY client whenever one
    sink is slow. This dispatcher hands listener work to a bounded thread pool
    while preserving per-conversation ordering: events for a single
    conversation drain serially (at most one in-flight drain per conv), but
    different conversations run concurrently. A slow push for one chat can never
    delay another conversation, the SSE stream, or the server.
    """

    def __init__(self, max_workers: int):
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="evt-listener")
        self._lanes: Dict[str, deque] = {}
        self._active: Set[str] = set()
        self._lock = threading.Lock()
        self._dropped = 0

    def dispatch(self, conversation_id: str, event_type: str, payload,
                 listeners) -> None:
        if not listeners:
            return
        with self._lock:
            lane = self._lanes.get(conversation_id)
            if lane is None:
                lane = deque()
                self._lanes[conversation_id] = lane
            lane.append((event_type, payload, listeners))
            if len(lane) > _MAX_LISTENER_LANE:
                overflow = len(lane) - _MAX_LISTENER_LANE
                for _ in range(overflow):
                    lane.popleft()
                self._dropped += overflow
                if self._dropped == overflow or self._dropped % 100 == 0:
                    logger.warning(
                        "Listener lane for conv=%s backed up; dropped %d "
                        "event(s) (total=%d) — a sink is slow",
                        conversation_id[:8], overflow, self._dropped)
            if conversation_id in self._active:
                return
            self._active.add(conversation_id)
        try:
            self._pool.submit(self._drain, conversation_id)
        except RuntimeError:
            # Pool already shut down (process/test teardown) — drop quietly.
            with self._lock:
                self._active.discard(conversation_id)

    def _drain(self, conversation_id: str) -> None:
        while True:
            with self._lock:
                lane = self._lanes.get(conversation_id)
                if not lane:
                    self._active.discard(conversation_id)
                    self._lanes.pop(conversation_id, None)
                    return
                event_type, payload, listeners = lane.popleft()
            for listener in listeners:
                try:
                    listener(conversation_id, event_type, payload)
                except Exception:
                    logger.debug(
                        "ConversationEventBus listener failed", exc_info=True)

    def shutdown(self) -> None:
        with self._lock:
            self._lanes.clear()
            self._active.clear()
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python < 3.9 has no cancel_futures
            self._pool.shutdown(wait=False)


class ConversationEventBus:
    """Pub/sub event bus keyed by conversation_id."""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ConversationEventBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance._cleanup_all()
            cls._instance = None

    def __init__(self):
        self._subscribers: Dict[str, Set[SSEWriter]] = {}
        # Browser EventSource reconnects can leave a server-side stream alive
        # until the next write. Track explicit client ids so a reconnect from
        # the same tab replaces the old writer immediately instead of leaking
        # subscribers and queued events.
        self._clients: Dict[Tuple[str, str], SSEWriter] = {}
        # Replay buffer: {conversation_id: [(timestamp, SSEEvent), ...]}
        self._buffer: Dict[str, List[Tuple[float, SSEEvent]]] = {}
        self._listeners: Set[Callable[[str, str, object], None]] = set()
        self._lock = threading.Lock()
        # Listeners run off the publishing thread so a slow sink (Telegram)
        # can't stall SSE delivery. See _ListenerDispatcher.
        self._listener_dispatcher = _ListenerDispatcher(
            _default_listener_threads())

    def add_listener(self, listener: Callable[[str, str, object], None]) -> None:
        """Register an in-process event listener.

        Listeners observe the same broadcast stream as SSE clients. They do not
        affect delivery or filtering; failures are logged and ignored.
        """
        with self._lock:
            self._listeners.add(listener)

    def remove_listener(self, listener: Callable[[str, str, object], None]) -> None:
        with self._lock:
            self._listeners.discard(listener)

    def subscribe(self, conversation_id: str, replay: bool = True,
                  client_id: str = "") -> SSEWriter:
        """Subscribe to events for a conversation. Returns an SSEWriter.

        When ``replay=True`` (default), buffered events published before this
        subscriber connected are delivered first -- handles the race between
        agent start and SSE connect on initial page load or auto-reconnect
        after a transient network drop.

        When ``replay=False``, the buffer for this conversation is popped
        and discarded. Used by explicit reload/switch paths: the client has
        just refetched the authoritative transcript from disk, so any
        still-buffered events would only produce duplicates and dedup races
        in the UI. A reload means reload -- not replay.
        """
        writer = SSEWriter()
        client_id = str(client_id or "").strip()[:128]
        client_key = (conversation_id, client_id) if client_id else None
        replaced = False
        with self._lock:
            if conversation_id not in self._subscribers:
                self._subscribers[conversation_id] = set()
            if client_key is not None:
                previous = self._clients.get(client_key)
                if previous is not None:
                    subs = self._subscribers.get(conversation_id)
                    if subs:
                        subs.discard(previous)
                    previous.close()
                    replaced = True
                self._clients[client_key] = writer
            self._subscribers[conversation_id].add(writer)
            buffered = list(self._buffer.get(conversation_id, []) or [])
            if replay:
                # A replaying subscriber consumes the buffered tail. Explicit
                # no-replay reloads only skip delivery for that subscriber;
                # they must not discard events produced after the history
                # fetch but before the new SSE socket attaches.
                self._buffer.pop(conversation_id, None)

        if replay:
            for _ts, event in buffered:
                if not writer.is_closed:
                    writer.send(event)
            logger.debug(f"EventBus: new subscriber for conv={conversation_id} "
                         f"(replayed {len(buffered)} buffered events)")
        else:
            logger.debug(f"EventBus: new subscriber for conv={conversation_id} "
                         f"(skipped {len(buffered)} buffered events, replay=False)")
        if replaced:
            logger.info("EventBus: replaced stale SSE subscriber for conv=%s client=%s",
                        conversation_id[:8], client_id[:12])
        return writer

    def unsubscribe(self, conversation_id: str, writer: SSEWriter):
        """Remove a subscriber."""
        with self._lock:
            subs = self._subscribers.get(conversation_id)
            if subs:
                subs.discard(writer)
                if not subs:
                    del self._subscribers[conversation_id]
            for key, tracked in list(self._clients.items()):
                if tracked is writer:
                    del self._clients[key]
        writer.close()

    def publish(self, conversation_id: str, event: SSEEvent):
        """Publish an event to all subscribers of a conversation.

        If no live subscribers exist, the event is buffered for replay
        when a subscriber connects (up to _MAX_BUFFER events, _BUFFER_TTL seconds).
        """
        def _buffer_locked() -> None:
            if event.event in ("done", "error_event"):
                logger.info(f"EventBus: buffering '{event.event}' for "
                            f"conv={conversation_id[:8]} (no subscribers)")
            if conversation_id not in self._buffer:
                self._buffer[conversation_id] = []
            buf = self._buffer[conversation_id]
            buf.append((time.time(), event))
            if len(buf) > _MAX_BUFFER:
                buf[:] = buf[-_MAX_BUFFER:]
            self._cleanup_expired_buffers()

        with self._lock:
            subs = self._subscribers.get(conversation_id)
            if subs:
                # Remove dead writers first to avoid sending to stale connections
                dead = {w for w in subs if w.is_closed}
                if dead:
                    subs -= dead
                    for key, tracked in list(self._clients.items()):
                        if tracked in dead:
                            del self._clients[key]
                    if not subs:
                        del self._subscribers[conversation_id]

            # Re-check after cleanup — all subscribers might have been dead
            subs = self._subscribers.get(conversation_id)
            if not subs:
                _buffer_locked()
                return
            # Copy to avoid holding lock during send
            writers = list(subs)

        if event.event in ("done", "error_event"):
            logger.info(f"EventBus: publishing '{event.event}' to "
                        f"{len(writers)} subscriber(s) for conv={conversation_id[:8]}")
        dead: List[SSEWriter] = []
        for writer in writers:
            if not writer.send(event):
                dead.append(writer)
        if dead:
            with self._lock:
                subs = self._subscribers.get(conversation_id)
                if subs:
                    for writer in dead:
                        subs.discard(writer)
                    for key, tracked in list(self._clients.items()):
                        if tracked in dead:
                            del self._clients[key]
                    if not subs:
                        del self._subscribers[conversation_id]
                if len(dead) == len(writers):
                    # Every apparent subscriber rejected the event while we
                    # published. This is the common switch/reconnect race:
                    # EventSource.close() has happened client-side, but the
                    # server stream has not reached its finally/unsubscribe
                    # yet. The event reached nobody, so keep it for the next
                    # replaying subscriber instead of dropping it on the floor.
                    _buffer_locked()
            logger.warning("EventBus: removed %d stale SSE subscriber(s) for conv=%s",
                           len(dead), conversation_id[:8])

    def _log_context_gauge_update(self, conversation_id: str,
                                  event_type: str, data) -> None:
        if not isinstance(data, dict):
            return
        if "context_used" not in data or "context_max" not in data:
            return
        try:
            used = int(data.get("context_used") or 0)
            max_tokens = int(data.get("context_max") or 0)
            pct_calc = (used / max_tokens) if max_tokens > 0 else 0.0
            pct_payload = float(data.get("context_pct") or 0.0)
        except (TypeError, ValueError):
            logger.debug(
                "[context-gauge:%s] send event=%s invalid payload used=%r max=%r pct=%r",
                conversation_id[:8], event_type,
                data.get("context_used"), data.get("context_max"),
                data.get("context_pct"))
            return
        source = data.get("context_source") or ""
        src = data.get("source") if isinstance(data.get("source"), dict) else {}
        if not source:
            source = src.get("context_source") or src.get("provider") or src.get("type") or ""
        logger.debug(
            "[context-gauge:%s] send event=%s agent=%s msg_id=%s "
            "formula=used/max used=%d max=%d pct_calc=%.4f pct_payload=%.4f "
            "source=%s cache_mode=%s message_count=%s updated_at=%s ts=%s live=%s",
            conversation_id[:8], event_type,
            data.get("agent_name") or src.get("name") or "",
            data.get("msg_id") or "", used, max_tokens, pct_calc, pct_payload,
            source, data.get("context_cache_mode") or "",
            data.get("context_message_count", ""),
            data.get("updated_at", ""), data.get("ts", ""),
            data.get("live", ""))

    def publish_event(self, conversation_id: str, event_type: str, data=None):
        """Convenience: create SSEEvent and publish.

        Auto-stamps data with ts=time.time() if missing.
        """
        if isinstance(data, dict) and "ts" not in data:
            data["ts"] = time.time()
        self._log_context_gauge_update(conversation_id, event_type, data)
        payload = data or ""
        self.publish(conversation_id, SSEEvent(event=event_type, data=payload))
        with self._lock:
            listeners = tuple(self._listeners)
        # Listeners (Telegram bridge, etc.) may block on network I/O. Never run
        # them on this (conversation-writer) thread: dispatch off-thread so a
        # slow sink can't stall SSE delivery to this or any other client.
        # Ordering per conversation is preserved by the dispatcher.
        self._listener_dispatcher.dispatch(
            conversation_id, event_type, payload, listeners)

    def subscriber_count(self, conversation_id: str) -> int:
        """Number of active subscribers for a conversation."""
        with self._lock:
            subs = self._subscribers.get(conversation_id)
            if not subs:
                return 0
            dead = {w for w in subs if w.is_closed}
            if dead:
                subs -= dead
                for key, tracked in list(self._clients.items()):
                    if tracked in dead:
                        del self._clients[key]
                if not subs:
                    del self._subscribers[conversation_id]
                    return 0
            return len(subs)

    def active_conversations(self) -> List[str]:
        """List conversation IDs with active subscribers."""
        with self._lock:
            active = []
            for conversation_id, subs in list(self._subscribers.items()):
                dead = {w for w in subs if w.is_closed}
                if dead:
                    subs -= dead
                    for key, tracked in list(self._clients.items()):
                        if tracked in dead:
                            del self._clients[key]
                if subs:
                    active.append(conversation_id)
                else:
                    del self._subscribers[conversation_id]
            return active

    def _cleanup_expired_buffers(self):
        """Remove buffered events older than _BUFFER_TTL (called under lock)."""
        now = time.time()
        expired = [cid for cid, buf in self._buffer.items()
                   if not buf or buf[-1][0] < now - _BUFFER_TTL]
        for cid in expired:
            del self._buffer[cid]

    def _cleanup_all(self):
        """Close all writers and clear buffers."""
        with self._lock:
            for conv_id, subs in self._subscribers.items():
                for writer in subs:
                    writer.close()
            self._subscribers.clear()
            self._clients.clear()
            self._listeners.clear()
            self._buffer.clear()
        self._listener_dispatcher.shutdown()
