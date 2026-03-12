"""Server-Sent Events (SSE) helpers.

Provides SSEEvent dataclass and SSEWriter for formatting and buffering
SSE events in a thread-safe manner.

SSE format (per spec):
    event: <type>\n
    data: <json>\n
    \n
"""

import json
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._closed = threading.Event()

    def send(self, event: SSEEvent):
        """Enqueue an SSE event (thread-safe)."""
        if not self._closed.is_set():
            self._queue.put(event)

    def send_event(self, event_type: str, data: Any = "", event_id: str = None):
        """Convenience: create and enqueue an SSEEvent."""
        self.send(SSEEvent(event=event_type, data=data, id=event_id))

    def close(self):
        """Signal end of stream."""
        self._closed.set()
        self._queue.put(_SENTINEL)

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    def iterate(self, timeout: float = 1.0):
        """Yield encoded SSE bytes. Blocks until events or close.

        Args:
            timeout: Max seconds to wait for each event (for keepalive).
        """
        while True:
            try:
                item = self._queue.get(timeout=timeout)
                if item is _SENTINEL:
                    return
                yield item.encode()
            except queue.Empty:
                if self._closed.is_set():
                    return
                # Send keepalive comment to prevent proxy/browser timeout
                yield b": keepalive\n\n"
