"""Shared config, event client and pure helpers for the CC interactive proxy
(split from cc_interactive_proxy.py for <=800 lines)."""
from __future__ import annotations
import logging

import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import queue
import threading
import time
from urllib.parse import urlparse

UPSTREAM_HOST = "api.anthropic.com"
UPSTREAM_PORT = 443
LISTEN_HOST = os.environ.get("PAWFLOW_CCI_PROXY_HOST", "0.0.0.0")  # nosec B104 - container-local TLS proxy bind.
LISTEN_PORT = int(os.environ.get("PAWFLOW_CCI_PROXY_PORT", "443"))
CERT_FILE = os.environ.get("PAWFLOW_CCI_LEAF_CERT", "/tmp/api-anthropic.crt")  # nosec B108 - container-local generated cert path.
KEY_FILE = os.environ.get("PAWFLOW_CCI_LEAF_KEY", "/tmp/api-anthropic.key")  # nosec B108 - container-local generated key path.
SESSION_TOKEN = os.environ.get("PAWFLOW_CCI_SESSION_TOKEN", "")
CONTAINER_ID = os.environ.get("HOSTNAME", "")
EVENT_URL = os.environ.get("PAWFLOW_CCI_EVENT_URL", "")
EVENT_TOKEN = os.environ.get("PAWFLOW_CCI_EVENT_TOKEN", "")

def _log(msg: str) -> None:
    sys.stderr.write(f"[cc-interactive-proxy] {msg}\n")
    sys.stderr.flush()

def _preview(text: str, limit: int = 80) -> str:
    text = (text or "").replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]

def _scrub(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in {"data_b64", "text_repr"}:
                out[k] = v
                continue
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

WIRE_LOG_ENABLED = os.environ.get("PAWFLOW_CCI_PROXY_WIRE_LOG", "0").lower() in {"1", "true", "yes"}
WIRE_LOG_ALL = os.environ.get("PAWFLOW_CCI_PROXY_WIRE_LOG_ALL", "0").lower() in {"1", "true", "yes"}
WIRE_LOG_PATHS = tuple(
    path.strip() for path in os.environ.get(
        "PAWFLOW_CCI_PROXY_WIRE_LOG_PATHS", "/v1/messages,/v1/complete"
    ).split(",") if path.strip()
)
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "anthropic-api-key",
}

def _wire_path_allowed(path: str) -> bool:
    if WIRE_LOG_ALL:
        return True
    return any(path == allowed or path.startswith(f"{allowed}?") for allowed in WIRE_LOG_PATHS)

def _redact_header_block(header_bytes: bytes) -> bytes:
    text = header_bytes.decode("latin-1", errors="replace")
    lines = text.split("\r\n")
    redacted = []
    for line in lines:
        if ":" not in line:
            redacted.append(line)
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() in SENSITIVE_HEADERS:
            redacted.append(f"{key}: <redacted:{len(value.strip())}>")
        else:
            redacted.append(line)
    return "\r\n".join(redacted).encode("latin-1", errors="replace")

def _text_repr(data: bytes) -> str:
    return repr(data.decode("utf-8", errors="replace"))

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
        sock.settimeout(None)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
            sock.settimeout(None)
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
            payload = _scrub(event) if event.get("type") == "wire" else event
            try:
                self._send({"type": "event", "event": payload})
                return
            except Exception as exc:
                _log(f"event send failed: {exc}")
                try:
                    self.sock.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                self.sock = None
            try:
                self.connect()
                if self.sock:
                    self._send({"type": "event", "event": payload})
                    return
            except Exception as exc:
                _log(f"event retry failed after reconnect: {exc}")
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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

class HTTPExchangeTracker:
    def __init__(self, connection_id: str):
        self.connection_id = connection_id
        self._next = 0
        self._pending = queue.Queue()

    def new_request_id(self) -> str:
        self._next += 1
        return self.connection_id if self._next == 1 else f"{self.connection_id}-{self._next}"

    def push(self, context: dict) -> None:
        self._pending.put(context)

    def pop(self) -> dict:
        return self._pending.get()

def _is_quota_probe(path: str, body: bytes) -> bool:
    if not path.startswith("/v1/messages"):
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return False
    messages = payload.get("messages") or []
    return (
        payload.get("max_tokens") == 1
        and isinstance(messages, list)
        and len(messages) == 1
        and messages[0].get("role") == "user"
        and messages[0].get("content") == "quota"
    )

def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)
