"""Server-Sent Events (SSE) helpers.

Provides SSEEvent dataclass and SSEWriter for formatting and buffering
SSE events in a thread-safe manner.

SSE format (per spec):
    event: <type>\n
    data: <json>\n
    \n
"""

import json
import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SSEEvent:
    """A single SSE event."""
    event: str  # event type: thinking, tool_call, tool_result, token, done, error
    data: Any = ""  # will be JSON-serialized if dict/list
    id: Optional[str] = None

    def encode(self) -> bytes:
        """Format as SSE wire format."""
        lines = []
        if self.id:
            lines.append(f"id: {self.id}")
        lines.append(f"event: {self.event}")
        if isinstance(self.data, (dict, list)):
            data_str = json.dumps(self.data, ensure_ascii=False)
        else:
            data_str = str(self.data)
        # SSE data can be multi-line — each line needs "data: " prefix
        for line in data_str.split("\n"):
            lines.append(f"data: {line}")
        lines.append("")  # trailing blank line
        lines.append("")
        return "\n".join(lines).encode("utf-8")


_SENTINEL = object()


class SSEWriter:
    """Thread-safe SSE event writer with queue-based buffering.

    Producer threads call send(event) to enqueue events.
    Consumer reads via iterate() which yields encoded bytes.
    Call close() to signal end of stream.
    """

    _DEFAULT_MAX_QUEUE = 1000

    def __init__(self, max_queue: Optional[int] = None):
        if max_queue is None:
            try:
                max_queue = int(os.getenv("PAWFLOW_SSE_WRITER_MAX_QUEUE", "1000") or "1000")
            except ValueError:
                max_queue = self._DEFAULT_MAX_QUEUE
        self._max_queue = max(1, int(max_queue or self._DEFAULT_MAX_QUEUE))
        self._queue: queue.Queue = queue.Queue(maxsize=self._max_queue)
        self._closed = threading.Event()
        self._overflowed = False

    def send(self, event: SSEEvent) -> bool:
        """Enqueue an SSE event.

        Stale browser/EventSource connections can stop draining while the
        backend still publishes events. A bounded queue turns that into a
        closed subscriber instead of unbounded RAM growth.
        """
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self._overflowed = True
            logger.warning("SSE writer queue overflowed at %s event(s); closing stale subscriber", self._max_queue)
            self.close()
            return False

    def send_event(self, event_type: str, data: Any = "", event_id: str = None) -> bool:
        """Convenience: create and enqueue an SSEEvent."""
        return self.send(SSEEvent(event=event_type, data=data, id=event_id))

    def close(self):
        """Signal end of stream."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    @property
    def overflowed(self) -> bool:
        return self._overflowed

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    def iterate(self, timeout: float = 1.0):
        """Yield encoded SSE bytes. Blocks until events or close.

        Every `timeout` seconds without a real event we emit two signals:
        a `: keepalive` comment (proxy/browser level) AND a typed
        `sse_ping` event (JS level). The comment-only form does NOT
        trigger any JS handler, so a silently half-open socket was
        invisible to the client — the typed ping lets the UI run a
        watchdog and force-reconnect when pings stop arriving.

        Args:
            timeout: Max seconds to wait for each event before emitting
                     the ping/keepalive pair.
        """
        import time as _time
        while True:
            try:
                item = self._queue.get(timeout=timeout)
                if item is _SENTINEL:
                    return
                yield item.encode()
            except queue.Empty:
                if self._closed.is_set():
                    return
                yield b": keepalive\n\n"
                yield SSEEvent(event="sse_ping",
                               data={"ts": _time.time()}).encode()
