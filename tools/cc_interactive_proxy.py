#!/usr/bin/env python3
"""Transparent local TLS proxy for claude-code-interactive.

Claude Code connects to https://api.anthropic.com, but the container maps that
host to 127.0.0.1. This proxy presents a PawFlow leaf certificate, forwards the
HTTP request body byte-for-byte to the real Anthropic endpoint, streams the
response back unmodified, and sends scrubbed SSE observations to PawFlow over a
separate WebSocket.
"""

from __future__ import annotations
import logging

import base64
import hashlib
import os
import socket
import ssl
import queue
import threading
import uuid

try:  # standalone (/opt/pawflow on path) vs package (tools.cc_interactive_common)
    from cc_interactive_common import (  # noqa: F401
        UPSTREAM_HOST, UPSTREAM_PORT, UPSTREAM_SCHEME, LISTEN_HOST, LISTEN_PORT, CERT_FILE, KEY_FILE, SESSION_TOKEN, CONTAINER_ID, EVENT_URL, EVENT_TOKEN, WIRE_LOG_ENABLED, WIRE_LOG_ALL, WIRE_LOG_PATHS, SENSITIVE_HEADERS, EVENTS, _log, _preview, _scrub, _wire_path_allowed, _redact_header_block, _text_repr, _read_headers, _header_map, _content_length, _is_chunked, _is_quota_probe, _content_text, EventClient, HTTPExchangeTracker)
except ImportError:
    from tools.cc_interactive_common import (  # noqa: F401
        UPSTREAM_HOST, UPSTREAM_PORT, UPSTREAM_SCHEME, LISTEN_HOST, LISTEN_PORT, CERT_FILE, KEY_FILE, SESSION_TOKEN, CONTAINER_ID, EVENT_URL, EVENT_TOKEN, WIRE_LOG_ENABLED, WIRE_LOG_ALL, WIRE_LOG_PATHS, SENSITIVE_HEADERS, EVENTS, _log, _preview, _scrub, _wire_path_allowed, _redact_header_block, _text_repr, _read_headers, _header_map, _content_length, _is_chunked, _is_quota_probe, _content_text, EventClient, HTTPExchangeTracker)
try:  # standalone (/opt/pawflow on path) vs package (tools.cc_interactive_observers)
    from cc_interactive_observers import (  # noqa: F401
        SSEObserver, _emit_observed_tool_blocks, HTTPRequestObserver, ChunkedBodyObserver, DecodingObserver, HTTPResponseObserver, JSONResponseObserver, _NullObserver)
except ImportError:
    from tools.cc_interactive_observers import (  # noqa: F401
        SSEObserver, _emit_observed_tool_blocks, HTTPRequestObserver, ChunkedBodyObserver, DecodingObserver, HTTPResponseObserver, JSONResponseObserver, _NullObserver)


class WireLogger:
    def __init__(self, request_id: str, direction: str, request_context: dict):
        self.request_id = request_id
        self.direction = direction
        self.request_context = request_context
        self._seq = 0
        self._states = {}

    def log(self, stage: str, data: bytes) -> None:
        if self.direction == "upstream_to_client" and stage == "out" and data:
            self.request_context["upstream_to_client_bytes"] = (
                int(self.request_context.get("upstream_to_client_bytes", 0) or 0)
                + len(data)
            )
        if not WIRE_LOG_ENABLED or not data:
            return
        for payload in self._sanitize(stage, data):
            if not payload:
                continue
            self._seq += 1
            event = {
                "type": "wire",
                "request_id": self.request_id,
                "direction": self.direction,
                "stage": stage,
                "seq": self._seq,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "data_b64": base64.b64encode(payload).decode("ascii"),
                "text_repr": _text_repr(payload),
            }
            _log(
                f"wire {self.direction} {stage} request={self.request_id} seq={self._seq} "
                f"bytes={event['bytes']} sha256={event['sha256']} data_b64={event['data_b64']} "
                f"text={event['text_repr']}")
            EVENTS.emit(event)

    def _sanitize(self, stage: str, data: bytes) -> list[bytes]:
        state = self._states.setdefault(stage, {"header_done": False, "header_buf": b""})
        if state["header_done"]:
            if self.request_context.get("wire_enabled") is not True:
                return []
            return [data]
        state["header_buf"] += data
        marker = state["header_buf"].find(b"\r\n\r\n")
        if marker < 0:
            return []
        header = state["header_buf"][:marker + 4]
        rest = state["header_buf"][marker + 4:]
        state["header_buf"] = b""
        state["header_done"] = True
        if self.direction == "client_to_upstream":
            start, _headers = _header_map(header)
            parts = start.split(" ", 2)
            path = parts[1] if len(parts) > 1 else ""
            self.request_context["path"] = path
            self.request_context["wire_enabled"] = _wire_path_allowed(path)
        if self.request_context.get("wire_enabled") is not True:
            return []
        redacted_header = _redact_header_block(header)
        return [redacted_header + rest]


def _connect_upstream():
    ips = [ip.strip() for ip in os.environ.get("PAWFLOW_ANTHROPIC_UPSTREAM_IPS", "").split(",") if ip.strip()]
    if not ips:
        infos = socket.getaddrinfo(UPSTREAM_HOST, UPSTREAM_PORT, type=socket.SOCK_STREAM)
        ips = [info[4][0] for info in infos]
    last = None
    for ip in ips:
        try:
            raw = socket.create_connection((ip, UPSTREAM_PORT), timeout=10)
            raw.settimeout(None)
            # Disable Nagle: SSE flows token-by-token in small writes, and
            # Nagle + delayed-ACK would coalesce them into bursts, making the
            # stream feel choppy. We forward each chunk as it arrives.
            try:
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            if UPSTREAM_SCHEME in {"http", "ws"}:
                return raw
            ctx = ssl.create_default_context()
            wrapped = ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
            wrapped.settimeout(None)
            return wrapped
        except Exception as exc:
            last = exc
    raise ConnectionError(f"upstream connect failed: {last}")


class AsyncObserver:
    def __init__(self, observer, label: str):
        self.observer = observer
        self.label = label
        self.q = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._run, name=f"cci-{label}", daemon=True)
        self.closed = False
        self.thread.start()

    def feed(self, data: bytes):
        if self.closed:
            return
        self.q.put(("feed", data))

    def finish(self):
        if self.closed:
            return
        self.closed = True
        self.q.put(("finish", b""))
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            _log(f"async {self.label} observer did not finish before timeout")

    def _run(self):
        while True:
            op, data = self.q.get()
            try:
                if op == "feed":
                    self.observer.feed(data)
                    continue
                finish = getattr(self.observer, "finish", None)
                if finish:
                    finish()
                return
            except Exception as exc:
                _log(f"async {self.label} observer failed without affecting proxy stream: {exc}")
                if op == "finish":
                    return


class AsyncWireLogger:
    def __init__(self, wire_logger):
        self.wire_logger = wire_logger
        self.direction = getattr(wire_logger, "direction", "")
        self.request_context = getattr(wire_logger, "request_context", {})
        self.q = queue.SimpleQueue()
        self.thread = threading.Thread(
            target=self._run,
            name=f"cci-wire-{self.direction or 'unknown'}",
            daemon=True)
        self.closed = False
        self.thread.start()

    def log(self, stage: str, data: bytes):
        if self.closed:
            return
        if self.direction == "upstream_to_client" and stage == "out" and data:
            self.request_context["upstream_to_client_forwarded"] = True
        self.q.put((stage, data))

    def finish(self):
        if self.closed:
            return
        self.closed = True
        self.q.put(("", b""))
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            _log(f"async {self.direction or 'wire'} logger did not finish before timeout")

    def _run(self):
        while True:
            stage, data = self.q.get()
            if stage == "":
                return
            try:
                self.wire_logger.log(stage, data)
            except Exception as exc:
                _log(f"async wire log failed without affecting proxy stream: {exc}")


def _pipe_exact(src, dst, observer=None, wire_logger=None, observer_before_send: bool = False):
    async_observer = None
    async_wire = None
    if observer:
        async_observer = observer if isinstance(observer, AsyncObserver) else AsyncObserver(observer, "http")
    if wire_logger:
        async_wire = wire_logger if isinstance(wire_logger, AsyncWireLogger) else AsyncWireLogger(wire_logger)
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                return
            if async_observer and observer_before_send:
                async_observer.feed(chunk)
            dst.sendall(chunk)
            if async_wire:
                async_wire.log("in", chunk)
                async_wire.log("out", chunk)
            if async_observer and not observer_before_send:
                async_observer.feed(chunk)
    finally:
        if async_observer:
            try:
                async_observer.finish()
            except Exception as exc:
                _log(f"observer finish failed without affecting proxy stream: {exc}")
        if async_wire:
            try:
                async_wire.finish()
            except Exception as exc:
                _log(f"wire logger finish failed without affecting proxy stream: {exc}")


def handle_client(client):
    request_id = uuid.uuid4().hex[:12]
    upstream = None
    try:
        upstream = _connect_upstream()
        _log(f"client_connected request={request_id}")
        errors = queue.Queue()

        def run_pipe(src, dst, observer, wire_logger, observer_before_send=False):
            try:
                _pipe_exact(src, dst, observer, wire_logger, observer_before_send)
            except Exception as exc:
                errors.put(exc)

        wire_context = {}
        tracker = HTTPExchangeTracker(request_id)
        c2u = threading.Thread(
            target=run_pipe,
            args=(client, upstream, HTTPRequestObserver(tracker),
                  WireLogger(request_id, "client_to_upstream", wire_context), True),
            daemon=True)
        u2c = threading.Thread(
            target=run_pipe,
            args=(upstream, client, HTTPResponseObserver(tracker),
                  WireLogger(request_id, "upstream_to_client", wire_context), False),
            daemon=True)
        c2u.start()
        u2c.start()
        c2u.join()
        u2c.join()
        if not errors.empty():
            exc = errors.get()
            if (wire_context.get("upstream_to_client_forwarded")
                    or int(wire_context.get("upstream_to_client_bytes", 0) or 0) > 0):
                _log(
                    "request_late_pipe_error_ignored request="
                    f"{request_id} error={exc}")
            else:
                raise exc
        _log(f"request_stop request={request_id}")
        EVENTS.emit({"type": "request_stop", "request_id": request_id})
    except Exception as exc:
        EVENTS.emit({"type": "request_error", "request_id": request_id, "error": str(exc)})
        _log(f"client handler failed: {exc}")
    finally:
        for sock in (upstream, client):
            if sock:
                try:
                    sock.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def main():
    if not SESSION_TOKEN:
        raise SystemExit("PAWFLOW_CCI_SESSION_TOKEN is required")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((LISTEN_HOST, LISTEN_PORT))
    lsock.listen(128)
    _log(f"listening on {LISTEN_HOST}:{LISTEN_PORT}")
    EVENTS.connect()
    while True:
        raw, _addr = lsock.accept()
        # Disable Nagle on the TUI-facing socket too, so forwarded SSE chunks
        # reach the Claude Code client without delayed-ACK coalescing.
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            client = ctx.wrap_socket(raw, server_side=True)
        except Exception as exc:
            _log(f"TLS handshake failed: {exc}")
            raw.close()
            continue
        threading.Thread(target=handle_client, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
