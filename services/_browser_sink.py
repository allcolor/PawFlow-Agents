"""Non-blocking delivery of relay stream bytes to one browser socket.

Relay stream frames (desktop, audio, code-server) are dispatched from the
relay connection's asyncio loop. Writing them to the browser with a blocking
``sendall`` there meant one slow viewer -- a paused tab, a weak link --
stalled every other frame of that relay: FUSE reads, tool results, the other
viewers. A ``BrowserSink`` owns one browser socket and one sender thread; the
loop only appends to its queue.

The queue is bounded in bytes. A viewer that falls further behind than the
cap is disconnected (the page reconnects) rather than letting memory grow or
latency pile up behind a stale backlog.
"""

import collections
import logging
import threading

from core.socket_teardown import shutdown_socket

logger = logging.getLogger(__name__)

#: Bytes a viewer may fall behind before it is disconnected: a few seconds of
#: a busy desktop stream.
DEFAULT_MAX_BACKLOG_BYTES = 32 * 1024 * 1024


class BrowserSink:
    """Queue bytes for one browser socket and write them on a thread."""

    def __init__(self, sock, name: str = "browser",
                 max_backlog_bytes: int = DEFAULT_MAX_BACKLOG_BYTES):
        self._sock = sock
        self._name = name
        self._max = int(max_backlog_bytes)
        self._queue = collections.deque()
        self._backlog = 0
        self._cond = threading.Condition()
        self._closing = False   # no new data; flush, then close the socket
        self._closed = False    # stop now
        self._thread = threading.Thread(
            target=self._run, name=f"browser-sink-{name}", daemon=True)
        self._thread.start()

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed or self._closing

    def send(self, data: bytes) -> bool:
        """Queue ``data``; never blocks. False when the viewer is gone."""
        with self._cond:
            if self._closed or self._closing:
                return False
            if self._backlog + len(data) > self._max:
                logger.warning(
                    "Browser %s is %d bytes behind; disconnecting it",
                    self._name, self._backlog)
                self._abort_locked()
                return False
            self._queue.append(data)
            self._backlog += len(data)
            self._cond.notify()
            return True

    def finish(self, final: bytes = b"") -> None:
        """Send what is queued, then ``final`` (a close frame), then close."""
        with self._cond:
            if self._closed or self._closing:
                return
            if final:
                self._queue.append(final)
                self._backlog += len(final)
            self._closing = True
            self._cond.notify()

    def abort(self) -> None:
        """Drop the backlog and close the socket now."""
        with self._cond:
            self._abort_locked()

    def _abort_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.clear()
        self._backlog = 0
        self._cond.notify()
        # Unblock a sendall in progress on the sender thread.
        shutdown_socket(self._sock)

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._closed and not self._closing:
                        self._cond.wait()
                    if self._closed:
                        return
                    if not self._queue:  # closing and fully flushed
                        return
                    data = self._queue.popleft()
                    self._backlog -= len(data)
                try:
                    self._sock.sendall(data)
                except Exception as exc:
                    logger.debug("Browser %s send failed: %s", self._name, exc)
                    with self._cond:
                        self._abort_locked()
                    return
        finally:
            with self._cond:
                self._closed = True
                self._queue.clear()
                self._backlog = 0
            try:
                self._sock.close()
            except Exception:
                logger.debug("Browser %s close failed", self._name, exc_info=True)
