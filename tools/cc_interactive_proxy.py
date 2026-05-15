#!/usr/bin/env python3
"""Transparent local TLS proxy for claude-code-interactive.

Claude Code connects to https://api.anthropic.com, but the container maps that
host to 127.0.0.1. This proxy presents a PawFlow leaf certificate, forwards the
HTTP request body byte-for-byte to the real Anthropic endpoint, streams the
response back unmodified, and sends scrubbed SSE observations to PawFlow over a
separate WebSocket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import threading
import time
import uuid
from urllib.parse import urlparse


UPSTREAM_HOST = "api.anthropic.com"
UPSTREAM_PORT = 443
LISTEN_HOST = os.environ.get("PAWFLOW_CCI_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("PAWFLOW_CCI_PROXY_PORT", "443"))
CERT_FILE = os.environ.get("PAWFLOW_CCI_LEAF_CERT", "/tmp/api-anthropic.crt")
KEY_FILE = os.environ.get("PAWFLOW_CCI_LEAF_KEY", "/tmp/api-anthropic.key")
SESSION_TOKEN = os.environ.get("PAWFLOW_CCI_SESSION_TOKEN", "")
CONTAINER_ID = os.environ.get("HOSTNAME", "")
EVENT_URL = os.environ.get("PAWFLOW_CCI_EVENT_URL", "")
EVENT_TOKEN = os.environ.get("PAWFLOW_CCI_EVENT_TOKEN", "")


def _log(msg: str) -> None:
    sys.stderr.write(f"[cc-interactive-proxy] {msg}\n")
    sys.stderr.flush()


def _scrub(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in {"data", "source", "image", "content"} and isinstance(v, str) and len(v) > 512:
                out[k] = {"sha256": hashlib.sha256(v.encode()).hexdigest(), "length": len(v)}
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and len(value) > 4096:
        return {"sha256": hashlib.sha256(value.encode()).hexdigest(), "length": len(value)}
    return value


class EventClient:
    def __init__(self, url: str, token: str, session_token: str):
        self.url = url
        self.token = token
        self.session_token = session_token
        self.sock = None
        self.lock = threading.Lock()

    def connect(self):
        if not self.url or not self.token or not self.session_token:
            return
        parsed = urlparse(self.url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/ws/cc-interactive/events"
        sock = socket.create_connection((host, port), timeout=10)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        internal = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
        cookie_line = f"Cookie: pawflow_internal={internal}\r\n" if internal else ""
        ws_key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"{cookie_line}"
            "\r\n"
        )
        sock.sendall(req.encode("latin-1"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("event WS closed during upgrade")
            resp += chunk
        if b"101" not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError(resp[:200].decode("latin-1", errors="replace"))
        self.sock = sock
        self._send({
            "type": "register",
            "token": self.token,
            "session_token": self.session_token,
            "container_id": CONTAINER_ID,
            "client_kind": "proxy",
        })
        msg = self._recv()
        if msg.get("type") == "error":
            raise ConnectionError(msg.get("message", "registration failed"))
        _log("event WS registered")

    def emit(self, event: dict) -> None:
        if not self.sock:
            try:
                self.connect()
            except Exception as exc:
                _log(f"event WS unavailable: {exc}")
                return
        event.setdefault("session_token", self.session_token)
        event.setdefault("container_id", CONTAINER_ID)
        event.setdefault("timestamp", time.time())
        with self.lock:
            try:
                self._send({"type": "event", "event": _scrub(event)})
            except Exception as exc:
                _log(f"event send failed: {exc}")
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.extend([0x80 | 126, (n >> 8) & 0xFF, n & 0xFF])
        else:
            header.append(0x80 | 127)
            header.extend(n.to_bytes(8, "big"))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv(self) -> dict:
        hdr = self._recvn(2)
        length = hdr[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self._recvn(2), "big")
        elif length == 127:
            length = int.from_bytes(self._recvn(8), "big")
        payload = self._recvn(length)
        return json.loads(payload.decode("utf-8"))

    def _recvn(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("event WS closed")
            buf += chunk
        return buf


EVENTS = EventClient(EVENT_URL, EVENT_TOKEN, SESSION_TOKEN)


def _read_headers(sock) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("client closed before headers")
        data += chunk
        if len(data) > 1024 * 1024:
            raise ValueError("headers too large")
    head, rest = data.split(b"\r\n\r\n", 1)
    return head + b"\r\n\r\n", rest


def _header_map(header_bytes: bytes) -> tuple[str, list[tuple[str, str]]]:
    text = header_bytes.decode("latin-1")
    lines = text.split("\r\n")
    start = lines[0]
    headers = []
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers.append((k.strip(), v.strip()))
    return start, headers


def _content_length(headers) -> int:
    for k, v in headers:
        if k.lower() == "content-length":
            try:
                return int(v)
            except ValueError:
                return 0
    return 0


def _is_chunked(headers) -> bool:
    return any(k.lower() == "transfer-encoding" and "chunked" in v.lower()
               for k, v in headers)


def _read_body(sock, initial: bytes, headers) -> bytes:
    length = _content_length(headers)
    if length <= 0:
        return initial
    body = initial
    while len(body) < length:
        chunk = sock.recv(min(65536, length - len(body)))
        if not chunk:
            raise ConnectionError("client closed during body")
        body += chunk
    return body


def _connect_upstream():
    ips = [ip.strip() for ip in os.environ.get("PAWFLOW_ANTHROPIC_UPSTREAM_IPS", "").split(",") if ip.strip()]
    if not ips:
        infos = socket.getaddrinfo(UPSTREAM_HOST, UPSTREAM_PORT, type=socket.SOCK_STREAM)
        ips = [info[4][0] for info in infos]
    last = None
    for ip in ips:
        try:
            raw = socket.create_connection((ip, UPSTREAM_PORT), timeout=10)
            ctx = ssl.create_default_context()
            return ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
        except Exception as exc:
            last = exc
    raise ConnectionError(f"upstream connect failed: {last}")


def _rewrite_request(start: str, headers) -> bytes:
    skip = {"proxy-connection", "connection", "keep-alive", "te", "trailer", "upgrade"}
    out = [start]
    saw_host = False
    for k, v in headers:
        kl = k.lower()
        if kl in skip:
            continue
        if kl == "host":
            out.append(f"Host: {UPSTREAM_HOST}")
            saw_host = True
        else:
            out.append(f"{k}: {v}")
    if not saw_host:
        out.append(f"Host: {UPSTREAM_HOST}")
    out.append("Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


class SSEObserver:
    def __init__(self, base_event: dict):
        self.base_event = base_event
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        while b"\n\n" in self.buf or b"\r\n\r\n" in self.buf:
            if b"\r\n\r\n" in self.buf:
                raw, self.buf = self.buf.split(b"\r\n\r\n", 1)
            else:
                raw, self.buf = self.buf.split(b"\n\n", 1)
            self._emit(raw.decode("utf-8", errors="replace"))

    def _emit(self, raw: str):
        event_name = "message"
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return
        data = "\n".join(data_lines)
        if data == "[DONE]":
            payload = {"done": True}
        else:
            try:
                payload = json.loads(data)
            except Exception:
                payload = {"raw": data[:1000]}
        ev = dict(self.base_event)
        ev.update({"event": event_name, "payload": payload})
        EVENTS.emit(ev)


def _stream_response(upstream, client, base_event: dict):
    header, rest = _read_headers(upstream)
    start, headers = _header_map(header)
    client.sendall(header)
    ctype = "\n".join(v for k, v in headers if k.lower() == "content-type").lower()
    observer = SSEObserver(base_event) if "text/event-stream" in ctype else None

    if observer and _is_chunked(headers):
        buf = rest
        while True:
            while b"\r\n" not in buf:
                chunk = upstream.recv(65536)
                if not chunk:
                    return
                buf += chunk
            size_line, buf = buf.split(b"\r\n", 1)
            size = int(size_line.split(b";", 1)[0], 16)
            needed = size + 2
            while len(buf) < needed:
                chunk = upstream.recv(65536)
                if not chunk:
                    return
                buf += chunk
            chunk_data, buf = buf[:size], buf[needed:]
            client.sendall(size_line + b"\r\n" + chunk_data + b"\r\n")
            if chunk_data:
                observer.feed(chunk_data)
            if size == 0:
                return
    else:
        if rest:
            client.sendall(rest)
            if observer:
                observer.feed(rest)
        while True:
            chunk = upstream.recv(65536)
            if not chunk:
                return
            client.sendall(chunk)
            if observer:
                observer.feed(chunk)


def handle_client(client):
    request_id = uuid.uuid4().hex[:12]
    upstream = None
    try:
        header, initial = _read_headers(client)
        start, headers = _header_map(header)
        body = _read_body(client, initial, headers)
        method = start.split(" ", 1)[0]
        path = start.split(" ", 2)[1] if " " in start else ""
        EVENTS.emit({
            "type": "request_start",
            "request_id": request_id,
            "method": method,
            "path": path,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body),
        })
        upstream = _connect_upstream()
        upstream.sendall(_rewrite_request(start, headers) + body)
        _stream_response(upstream, client, {"type": "sse", "request_id": request_id})
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
                    pass


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
        try:
            client = ctx.wrap_socket(raw, server_side=True)
        except Exception as exc:
            _log(f"TLS handshake failed: {exc}")
            raw.close()
            continue
        threading.Thread(target=handle_client, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
