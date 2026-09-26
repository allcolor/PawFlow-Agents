"""Relay-wide record of commands, so a reconnect never runs one twice.

The server retries a request whose connection dropped, with the same
``request_id`` (``services/_filesystem_ops._request``). The relay used to
track in-flight commands per connection, so the retry arriving on the new
connection ran the command a second time while the first run was still going,
and the first run's result was sent on the dead socket and lost (2026-09-26:
a 45-minute ``pytest`` ran twice in parallel after a watchdog reconnect).

This ledger outlives connections:

* ``begin`` tells the caller whether to run a command, or that it is already
  running, or hands back the result of a finished run.
* ``finish`` records the result, bounded in count, bytes and age.
* ``attach`` names the connection results go out on. A result is always sent
  on the connection that is current when it is ready, not on the one the
  command arrived on.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

RUN = "run"
RUNNING = "running"

# A retry arrives within seconds of the reconnect; keep answers well past it.
_RESULT_TTL_SECONDS = 300.0
_MAX_RESULTS = 256
_MAX_RESULT_BYTES = 64 * 1024 * 1024


class _Link:
    def __init__(self, sock, send_fn: Callable, send_lock) -> None:
        self.sock = sock
        self.send_fn = send_fn
        self.send_lock = send_lock


class CommandLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set = set()
        self._results: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
        self._result_bytes = 0
        self._link: Optional[_Link] = None

    # ── connection ────────────────────────────────────────────────────

    def attach(self, sock, send_fn: Callable, send_lock) -> None:
        """Make this connection the one results and output go out on."""
        with self._lock:
            self._link = _Link(sock, send_fn, send_lock)

    def send(self, frame: bytes) -> None:
        """Send ``frame`` on the current connection; raises when there is none."""
        with self._lock:
            link = self._link
        if link is None:
            raise ConnectionError("no relay connection")
        with link.send_lock:
            link.send_fn(link.sock, frame)

    # ── commands ──────────────────────────────────────────────────────

    def begin(self, request_id: str):
        """``RUN``, ``RUNNING``, or the finished run's result frame (bytes)."""
        with self._lock:
            self._expire_locked(time.monotonic())
            if request_id in self._running:
                return RUNNING
            done = self._results.get(request_id)
            if done is not None:
                return done[1]
            self._running.add(request_id)
            return RUN

    def finish(self, request_id: str, frame: bytes) -> None:
        with self._lock:
            self._running.discard(request_id)
            now = time.monotonic()
            self._expire_locked(now)
            if len(frame) > _MAX_RESULT_BYTES:
                return
            previous = self._results.pop(request_id, None)
            if previous is not None:
                self._result_bytes -= len(previous[1])
            self._results[request_id] = (now, frame)
            self._result_bytes += len(frame)
            while (len(self._results) > _MAX_RESULTS
                   or self._result_bytes > _MAX_RESULT_BYTES):
                _rid, (_at, old) = self._results.popitem(last=False)
                self._result_bytes -= len(old)

    def abandon(self, request_id: str) -> None:
        """Forget a command that produced no result (it never ran)."""
        with self._lock:
            self._running.discard(request_id)

    def _expire_locked(self, now: float) -> None:
        while self._results:
            rid, (at, frame) = next(iter(self._results.items()))
            if now - at < _RESULT_TTL_SECONDS:
                return
            self._results.popitem(last=False)
            self._result_bytes -= len(frame)


#: The relay process's ledger, shared by every connection.
LEDGER = CommandLedger()
