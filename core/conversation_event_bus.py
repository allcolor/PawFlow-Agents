"""Conversation Event Bus — pub/sub for SSE events by conversation_id.

Allows multiple producers (agent loops, timers) to publish events for a
conversation, and multiple consumers (SSE streams) to subscribe and receive
them in real time.

Includes a replay buffer so late subscribers (SSE connects after agent starts)
receive events that were published before they subscribed.

Thread-safe singleton.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from core.sse_writer import SSEEvent, SSEWriter

logger = logging.getLogger(__name__)

# Max events to buffer per conversation before a subscriber connects
_MAX_BUFFER = 200
# How long to keep buffered events (seconds)
_BUFFER_TTL = 60


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
        # Replay buffer: {conversation_id: [(timestamp, SSEEvent), ...]}
        self._buffer: Dict[str, List[Tuple[float, SSEEvent]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, conversation_id: str) -> SSEWriter:
        """Subscribe to events for a conversation. Returns an SSEWriter.

        Replays any buffered events that were published before this subscriber
        connected (handles the race between agent start and SSE connect).
        """
        writer = SSEWriter()
        with self._lock:
            if conversation_id not in self._subscribers:
                self._subscribers[conversation_id] = set()
            self._subscribers[conversation_id].add(writer)

            # Replay buffered events
            buffered = self._buffer.pop(conversation_id, [])

        # Replay outside the lock
        for _ts, event in buffered:
            if not writer.is_closed:
                writer.send(event)

        logger.debug(f"EventBus: new subscriber for conv={conversation_id} "
                     f"(replayed {len(buffered)} buffered events)")
        return writer

    def unsubscribe(self, conversation_id: str, writer: SSEWriter):
        """Remove a subscriber."""
        with self._lock:
            subs = self._subscribers.get(conversation_id)
            if subs:
                subs.discard(writer)
                if not subs:
                    del self._subscribers[conversation_id]
        writer.close()

    def publish(self, conversation_id: str, event: SSEEvent):
        """Publish an event to all subscribers of a conversation.

        If no live subscribers exist, the event is buffered for replay
        when a subscriber connects (up to _MAX_BUFFER events, _BUFFER_TTL seconds).
        """
        with self._lock:
            subs = self._subscribers.get(conversation_id)
            if subs:
                # Remove dead writers first to avoid sending to stale connections
                dead = {w for w in subs if w.is_closed}
                if dead:
                    subs -= dead
                    if not subs:
                        del self._subscribers[conversation_id]

            # Re-check after cleanup — all subscribers might have been dead
            subs = self._subscribers.get(conversation_id)
            if not subs:
                # No live subscribers — buffer for replay
                if event.event in ("done", "error_event"):
                    logger.info(f"EventBus: buffering '{event.event}' for "
                                f"conv={conversation_id[:8]} (no subscribers)")
                if conversation_id not in self._buffer:
                    self._buffer[conversation_id] = []
                buf = self._buffer[conversation_id]
                buf.append((time.time(), event))
                # Enforce size limit
                if len(buf) > _MAX_BUFFER:
                    buf[:] = buf[-_MAX_BUFFER:]
                # Cleanup expired buffers from other conversations
                self._cleanup_expired_buffers()
                return
            # Copy to avoid holding lock during send
            writers = list(subs)

        if event.event in ("done", "error_event"):
            logger.info(f"EventBus: publishing '{event.event}' to "
                        f"{len(writers)} subscriber(s) for conv={conversation_id[:8]}")
        for writer in writers:
            writer.send(event)

    def publish_event(self, conversation_id: str, event_type: str, data=None):
        """Convenience: create SSEEvent and publish."""
        self.publish(conversation_id, SSEEvent(event=event_type, data=data or ""))

    def subscriber_count(self, conversation_id: str) -> int:
        """Number of active subscribers for a conversation."""
        with self._lock:
            subs = self._subscribers.get(conversation_id)
            return len(subs) if subs else 0

    def active_conversations(self) -> List[str]:
        """List conversation IDs with active subscribers."""
        with self._lock:
            return list(self._subscribers.keys())

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
            self._buffer.clear()
