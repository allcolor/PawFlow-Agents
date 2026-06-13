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
_KEEPALIVE_CHUNK = b": keepalive\n\n"


def _encode_sse_ping(ts: float) -> bytes:
    return f"event: sse_ping\ndata: {{\"ts\":{ts:.3f}}}\n\n".encode("utf-8")


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

    def drain_nowait(self) -> list:
        """Pop and encode every already-queued event without blocking.

        Used at graceful teardown (e.g. SSE lifetime cap) to flush events that
        were accepted by send() but not yet yielded to the socket. Such events
        are NOT in the bus replay buffer (send() returned True, so publish()
        considered them delivered), so dropping them on close loses them for
        good. Returns a list of encoded chunks in FIFO order; stops at the
        close sentinel.
        """
        out = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SENTINEL:
                break
            out.append(item.encode())
        return out

    def iterate(self, timeout: float = 15.0, ping_interval: Optional[float] = None):
        """Yield encoded SSE bytes. Blocks until events or close.

        `timeout` controls the cheap comment keepalive used by proxies and
        browsers. Typed `sse_ping` events are only needed by JS watchdogs, so
        they are emitted less often and encoded directly instead of allocating
        an SSEEvent and dict on every idle tick.

        Args:
            timeout: Max seconds to wait for each event before emitting a
                     comment keepalive.
            ping_interval: Seconds between typed JS-visible pings. Defaults to
                           PAWFLOW_SSE_PING_INTERVAL or 15 seconds.
        """
        import time as _time
        if ping_interval is None:
            try:
                ping_interval = float(os.getenv("PAWFLOW_SSE_PING_INTERVAL", "15") or "15")
            except ValueError:
                ping_interval = 15.0
        ping_interval = max(0.0, float(ping_interval or 0.0))
        next_ping = _time.monotonic() + ping_interval if ping_interval else None
        while True:
            try:
                item = self._queue.get(timeout=timeout)
                if item is _SENTINEL:
                    return
                yield item.encode()
            except queue.Empty:
                if self._closed.is_set():
                    return
                yield _KEEPALIVE_CHUNK
                now = _time.monotonic()
                if next_ping is not None and now >= next_ping:
                    yield _encode_sse_ping(_time.time())
                    next_ping = now + ping_interval
