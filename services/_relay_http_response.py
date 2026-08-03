"""Disk-spooled relay HTTP response streaming.

Relay WebSocket callbacks must never accumulate an upstream response in a
Python list: a fast producer and slow HTTP client would recreate the same
unbounded-memory bug as a single ``resp.read()``. This adapter writes each
decoded relay chunk to a private temporary file and lets the HTTP server read
the file concurrently. Memory use is therefore bounded by one wire chunk.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import threading
from typing import Iterator


logger = logging.getLogger(__name__)

HOP_BY_HOP_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding",
    "upgrade", "content-length",
})


class RelayHttpResponseStream:
    """Run a relay HTTP request and expose a disk-backed byte iterator."""

    def __init__(self, runner, *, label: str = "relay-http"):
        self._runner = runner
        self._label = label
        fd, self._path = tempfile.mkstemp(prefix="pawflow-relay-http-")
        self._writer = os.fdopen(fd, "wb", buffering=0)
        self._condition = threading.Condition()
        self._ready = threading.Event()
        self._available = 0
        self._producer_done = False
        self._consumer_done = False
        self._cleaned = False
        self.status = 502
        self.reason = ""
        self.headers = {}
        self.error = ""

    @classmethod
    def for_local_port(cls, relay_service, *, port: int, method: str,
                       req_path: str, headers: dict, body: bytes,
                       timeout: int = 300, label: str = "relay-http"):
        """Build a stream for the relay container's localhost."""
        def _runner(on_output):
            return relay_service.http_proxy_stream(
                port=int(port), method=method or "GET",
                req_path=req_path or "/", req_headers=dict(headers or {}),
                body=bytes(body or b""), timeout=int(timeout),
                on_output=on_output)
        return cls(_runner, label=label)

    @classmethod
    def for_fetch(cls, relay_service, *, url: str, method: str,
                  headers: dict, body: bytes, local: bool,
                  timeout: int = 300, label: str = "relay-http-fetch"):
        """Build a stream for the relay-aware arbitrary URL fetch action."""
        def _runner(on_output):
            return relay_service.http_fetch_stream(
                url=url, method=method or "GET", headers=dict(headers or {}),
                body=bytes(body or b""), timeout=int(timeout),
                local=bool(local), on_output=on_output)
        return cls(_runner, label=label)

    @property
    def spool_path(self) -> str:
        """Temporary path, exposed for diagnostics and focused tests."""
        return self._path

    def start(self) -> "RelayHttpResponseStream":
        threading.Thread(
            target=self._run, name=self._label, daemon=True).start()
        return self

    def wait_ready(self) -> None:
        """Wait until upstream headers arrive or the request fails."""
        self._ready.wait()

    def _on_output(self, kind, data) -> None:
        if kind == "start":
            payload = data if isinstance(data, dict) else {}
            self.status = int(payload.get("status", 200))
            self.reason = str(payload.get("reason") or "")
            self.headers = {
                str(k): str(v)
                for k, v in dict(payload.get("headers") or {}).items()
                if str(k).lower() not in HOP_BY_HOP_RESPONSE_HEADERS
            }
            self._ready.set()
            return
        if kind == "chunk":
            try:
                raw = base64.b64decode(data, validate=True)
            except Exception:
                self._fail("Relay returned an invalid base64 HTTP chunk")
                return
            with self._condition:
                if self._consumer_done or self.error:
                    return
                self._writer.write(raw)
                self._available += len(raw)
                self._condition.notify_all()
            return
        if kind == "end":
            self._finish_producer()

    def _run(self) -> None:
        try:
            result = self._runner(self._on_output)
            detail = ""
            failed = False
            if isinstance(result, dict):
                detail = str(result.get("error") or "")
                failed = result.get("ok") is False
            if failed:
                self._fail(detail or "Relay HTTP proxy failed")
            elif not self._ready.is_set():
                self._fail(detail or "Relay HTTP proxy ended before headers")
        except Exception as exc:
            self._fail(str(exc))
        finally:
            self._finish_producer()

    def _fail(self, message: str) -> None:
        with self._condition:
            if not self.error:
                self.error = message or "Relay HTTP proxy failed"
            self._condition.notify_all()
        self._ready.set()

    def _finish_producer(self) -> None:
        with self._condition:
            if not self._producer_done:
                self._producer_done = True
                try:
                    self._writer.close()
                except Exception:
                    logger.debug("Could not close relay HTTP spool", exc_info=True)
                self._condition.notify_all()
        self._cleanup_if_finished()

    def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield bytes as the producer extends the spool file."""
        offset = 0
        try:
            with open(self._path, "rb", buffering=0) as reader:
                while True:
                    with self._condition:
                        self._condition.wait_for(
                            lambda: self._available > offset
                            or self._producer_done or bool(self.error))
                        available = self._available
                        done = self._producer_done
                        error = self.error
                    while offset < available:
                        reader.seek(offset)
                        chunk = reader.read(min(chunk_size, available - offset))
                        if not chunk:
                            break
                        offset += len(chunk)
                        yield chunk
                    if error and offset >= available:
                        raise RuntimeError(error)
                    if done and offset >= available:
                        return
        finally:
            with self._condition:
                self._consumer_done = True
                self._condition.notify_all()
            self._cleanup_if_finished()

    def discard(self) -> None:
        """Stop retaining bytes when the caller serves a fallback response."""
        with self._condition:
            self._consumer_done = True
            self._condition.notify_all()
        self._cleanup_if_finished()

    def _cleanup_if_finished(self) -> None:
        with self._condition:
            if (self._cleaned or not self._producer_done
                    or not self._consumer_done):
                return
            self._cleaned = True
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("Could not remove relay HTTP spool", exc_info=True)
