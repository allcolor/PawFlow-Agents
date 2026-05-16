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
import gzip
import hashlib
import json
import os
import socket
import ssl
import sys
import queue
import threading
import time
import uuid
import zlib
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
            try:
                payload = _scrub(event) if event.get("type") == "wire" else event
                self._send({"type": "event", "event": payload})
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
            ctx = ssl.create_default_context()
            wrapped = ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
            wrapped.settimeout(None)
            return wrapped
        except Exception as exc:
            last = exc
    raise ConnectionError(f"upstream connect failed: {last}")


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
        if isinstance(payload, dict):
            ptype = payload.get("type") or event_name
            if ptype == "content_block_delta":
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                text = delta.get("text", "") if dtype == "text_delta" else ""
                _log(
                    f"emit sse request={self.base_event.get('request_id', '')} "
                    f"event={event_name} type={ptype} delta={dtype} "
                    f"text_len={len(text)} preview={_preview(text)!r}")
            else:
                _log(
                    f"emit sse request={self.base_event.get('request_id', '')} "
                    f"event={event_name} type={ptype} keys={sorted(payload.keys())[:8]}")
        else:
            _log(
                f"emit sse request={self.base_event.get('request_id', '')} "
                f"event={event_name} payload_type={type(payload).__name__}")
        EVENTS.emit(ev)


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
        try:
            return self._pending.get_nowait()
        except queue.Empty:
            return {"request_id": self.connection_id, "path": "", "ignore_response": False}


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


def _is_title_json_text(text: str) -> bool:
    try:
        payload = json.loads(text)
    except Exception:
        return False
    return isinstance(payload, dict) and set(payload.keys()) == {"title"}


def _emit_observed_tool_blocks(request_id: str, path: str, body: bytes) -> None:
    if not path.startswith("/v1/messages"):
        return
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if role == "assistant" and btype == "tool_use":
                tool_use_id = block.get("id") or ""
                if not tool_use_id:
                    continue
                args = block.get("input") or {}
                _log(
                    f"emit tool_use request={request_id} path={path} "
                    f"tool_use_id={tool_use_id} name={block.get('name', '')}")
                EVENTS.emit({
                    "type": "tool_use",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": tool_use_id,
                    "name": block.get("name", ""),
                    "arguments": args if isinstance(args, dict) else {},
                })
            elif role == "user" and btype == "tool_result":
                tool_use_id = block.get("tool_use_id") or block.get("id") or ""
                if not tool_use_id:
                    continue
                result = _content_text(block.get("content"))
                _log(
                    f"emit tool_result request={request_id} path={path} "
                    f"tool_use_id={tool_use_id} result_len={len(result)} "
                    f"preview={_preview(result)!r}")
                EVENTS.emit({
                    "type": "tool_result",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": tool_use_id,
                    "content": result,
                    "is_error": bool(block.get("is_error")),
                })


class HTTPRequestObserver:
    def __init__(self, tracker: HTTPExchangeTracker):
        self.tracker = tracker
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        while True:
            if b"\r\n\r\n" not in self.buf:
                return
            header, rest = self.buf.split(b"\r\n\r\n", 1)
            start, headers = _header_map(header + b"\r\n\r\n")
            clen = _content_length(headers)
            if len(rest) < clen:
                return
            body = rest[:clen]
            self.buf = rest[clen:]
            parts = start.split(" ", 2)
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else ""
            request_id = self.tracker.new_request_id()
            reason = ""
            if _is_quota_probe(path, body):
                reason = "quota_probe"
            ignore_response = bool(reason)
            self.tracker.push({
                "request_id": request_id,
                "method": method,
                "path": path,
                "ignore_response": ignore_response,
                "ignore_reason": reason,
            })
            _log(
                f"request_start request={request_id} method={method} path={path} "
                f"body_bytes={len(body)} ignore_reason={reason or '-'}")
            EVENTS.emit({
                "type": "request_start",
                "request_id": request_id,
                "method": method,
                "path": path,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_bytes": len(body),
                "ignore_reason": reason,
            })
            _emit_observed_tool_blocks(request_id, path, body)


class ChunkedBodyObserver:
    def __init__(self, observer: SSEObserver):
        self.observer = observer
        self.buf = b""
        self.remaining = None
        self.done = False

    def feed(self, data: bytes):
        if self.done:
            return data
        self.buf += data
        while not self.done:
            if self.remaining is None:
                if b"\r\n" not in self.buf:
                    return None
                size_line, self.buf = self.buf.split(b"\r\n", 1)
                self.remaining = int(size_line.split(b";", 1)[0], 16)
                if self.remaining == 0:
                    if len(self.buf) < 2:
                        return None
                    if self.buf.startswith(b"\r\n"):
                        leftover = self.buf[2:]
                    else:
                        trailer_end = self.buf.find(b"\r\n\r\n")
                        if trailer_end < 0:
                            return None
                        leftover = self.buf[trailer_end + 4:]
                    self.buf = b""
                    self.done = True
                    finish = getattr(self.observer, "finish", None)
                    if finish:
                        finish()
                    return leftover
            if len(self.buf) < self.remaining + 2:
                return None
            chunk_data = self.buf[:self.remaining]
            self.buf = self.buf[self.remaining + 2:]
            self.remaining = None
            if chunk_data:
                self.observer.feed(chunk_data)
        return self.buf


class DecodingObserver:
    def __init__(self, observer, encoding: str, request_id: str):
        self.observer = observer
        self.encoding = (encoding or "").lower()
        self.request_id = request_id
        self.decoder = self._make_decoder()
        self.unsupported = False

    def _make_decoder(self):
        if not self.encoding or "identity" in self.encoding:
            return None
        if "gzip" in self.encoding:
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if "deflate" in self.encoding:
            return zlib.decompressobj()
        self.unsupported = True
        _log(
            f"response_ignored request={self.request_id} "
            f"reason=unsupported_content_encoding encoding={self.encoding}")
        EVENTS.emit({
            "type": "response_ignored",
            "request_id": self.request_id,
            "reason": "unsupported_content_encoding",
            "payload_type": self.encoding,
        })
        return None

    def feed(self, data: bytes):
        if self.unsupported:
            return
        if not data:
            return
        if self.decoder is None:
            self.observer.feed(data)
            return
        decoded = self.decoder.decompress(data)
        if decoded:
            self.observer.feed(decoded)

    def finish(self):
        if self.unsupported:
            return
        if self.decoder is not None:
            decoded = self.decoder.flush()
            if decoded:
                self.observer.feed(decoded)
        finish = getattr(self.observer, "finish", None)
        if finish:
            finish()


class HTTPResponseObserver:
    def __init__(self, tracker: HTTPExchangeTracker):
        self.tracker = tracker
        self.buf = b""
        self.body_observer = None

    def feed(self, data: bytes):
        self.buf += data
        while True:
            if self.body_observer:
                leftover = self.body_observer.feed(self.buf)
                if leftover is None:
                    self.buf = b""
                    return
                self.buf = leftover
                self.body_observer = None
                continue
            if b"\r\n\r\n" not in self.buf:
                return
            header, rest = self.buf.split(b"\r\n\r\n", 1)
            start, headers = _header_map(header + b"\r\n\r\n")
            is_chunked = _is_chunked(headers)
            if is_chunked:
                self.buf = rest
                self._start_chunked_response(start, headers)
                continue
            else:
                clen = _content_length(headers)
                if clen and len(rest) < clen:
                    return
                body = rest[:clen] if clen else b""
                remaining = rest[clen:] if clen else rest
            self.buf = remaining
            self._emit_response(start, headers, body, is_chunked=False)

    def finish(self):
        if self.body_observer:
            finish = getattr(self.body_observer, "finish", None)
            if finish:
                finish()
        if self.buf:
            _log(f"response observer discarded incomplete trailing bytes={len(self.buf)}")

    def _start_chunked_response(self, start: str, headers) -> None:
        ctx = self.tracker.pop()
        request_id = ctx.get("request_id", self.tracker.connection_id)
        ctype, encoding, status = self._response_meta(start, headers)
        _log(
            f"response_start request={request_id} path={ctx.get('path', '')} status={status} "
            f"ctype={ctype or '-'} body_bytes=chunked encoding={encoding or '-'} chunked=True")
        EVENTS.emit({
            "type": "response_start",
            "request_id": request_id,
            "path": ctx.get("path", ""),
            "status": status,
            "content_type": ctype,
            "content_length": 0,
            "content_encoding": encoding,
            "chunked": True,
        })
        if ctx.get("ignore_response"):
            EVENTS.emit({
                "type": "response_ignored",
                "request_id": request_id,
                "path": ctx.get("path", ""),
                "reason": ctx.get("ignore_reason", "request_ignored"),
            })
            self.body_observer = ChunkedBodyObserver(_NullObserver())
        elif "text/event-stream" in ctype:
            self.body_observer = ChunkedBodyObserver(
                DecodingObserver(
                    SSEObserver({"type": "sse", "request_id": request_id}),
                    encoding,
                    request_id,
                ))
        elif "json" in ctype:
            self.body_observer = ChunkedBodyObserver(JSONResponseObserver(
                {"type": "sse", "request_id": request_id},
                content_length=0,
                encoding=encoding,
            ))
        else:
            self.body_observer = ChunkedBodyObserver(_NullObserver())

    def _response_meta(self, start: str, headers):
        ctype = "\n".join(v for k, v in headers if k.lower() == "content-type").lower()
        encoding = "\n".join(v for k, v in headers if k.lower() == "content-encoding").lower()
        status = ""
        parts = start.split(" ", 2)
        if len(parts) > 1:
            status = parts[1]
        return ctype, encoding, status

    def _emit_response(self, start: str, headers, body: bytes, is_chunked: bool):
        ctx = self.tracker.pop()
        request_id = ctx.get("request_id", self.tracker.connection_id)
        ctype, encoding, status = self._response_meta(start, headers)
        _log(
            f"response_start request={request_id} path={ctx.get('path', '')} status={status} "
            f"ctype={ctype or '-'} body_bytes={len(body)} encoding={encoding or '-'} chunked={is_chunked}")
        EVENTS.emit({
            "type": "response_start",
            "request_id": request_id,
            "path": ctx.get("path", ""),
            "status": status,
            "content_type": ctype,
            "content_length": len(body),
            "content_encoding": encoding,
            "chunked": is_chunked,
        })
        if ctx.get("ignore_response"):
            EVENTS.emit({
                "type": "response_ignored",
                "request_id": request_id,
                "path": ctx.get("path", ""),
                "reason": ctx.get("ignore_reason", "request_ignored"),
            })
            return
        if "text/event-stream" in ctype:
            sse = SSEObserver({"type": "sse", "request_id": request_id})
            sse.feed(body)
            return
        if "json" in ctype:
            json_observer = JSONResponseObserver(
                {"type": "sse", "request_id": request_id},
                content_length=len(body),
                encoding=encoding,
            )
            json_observer.feed(body)
            json_observer.finish()


class JSONResponseObserver:
    def __init__(self, base_event: dict, content_length: int = 0, encoding: str = ""):
        self.base_event = base_event
        self.content_length = max(0, int(content_length or 0))
        self.encoding = encoding or ""
        self.buf = b""
        self.emitted = False

    def feed(self, data: bytes):
        if self.emitted or not data:
            return
        self.buf += data
        if self.content_length and len(self.buf) >= self.content_length:
            self.finish()

    def finish(self):
        if self.emitted:
            return
        self.emitted = True
        raw = self.buf[:self.content_length] if self.content_length else self.buf
        if not raw:
            return
        if "gzip" in self.encoding:
            raw = gzip.decompress(raw)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            self._ignore("json_payload_not_object", "")
            return
        ptype = payload.get("type", "")
        _log(
            f"json_response request={self.base_event.get('request_id', '')} "
            f"type={ptype or '-'} keys={sorted(payload.keys())[:10]}")
        if ptype != "message":
            self._ignore("json_type_not_message", str(ptype))
            return
        content = payload.get("content") or []
        if not isinstance(content, list):
            self._ignore("message_content_not_list", "")
            return
        text_blocks = [block for block in content if isinstance(block, dict) and block.get("type") == "text"]
        if len(text_blocks) == 1 and _is_title_json_text(str(text_blocks[0].get("text", ""))):
            self._ignore("title_json_message", "message")
            return
        emitted_blocks = 0
        for idx, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text = block.get("text", "")
                if not text:
                    continue
                emitted_blocks += 1
                _log(
                    f"emit json_text request={self.base_event.get('request_id', '')} "
                    f"index={idx} text_len={len(text)} preview={_preview(text)!r}")
                self._emit("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text"},
                })
                for pos in range(0, len(text), 1800):
                    self._emit("content_block_delta", {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": text[pos:pos + 1800]},
                    })
                self._emit("content_block_stop", {
                    "type": "content_block_stop",
                    "index": idx,
                })
            elif btype == "tool_use":
                emitted_blocks += 1
                self._emit("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                    },
                })
                tool_input = block.get("input") or {}
                self._emit("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(tool_input, separators=(",", ":")),
                    },
                })
                self._emit("content_block_stop", {
                    "type": "content_block_stop",
                    "index": idx,
                })
        if emitted_blocks == 0:
            self._ignore("message_without_supported_content", "")
            return
        usage = payload.get("usage") or {}
        if usage:
            self._emit("message_delta", {"type": "message_delta", "usage": usage})
        self._emit("message_stop", {"type": "message_stop"})

    def _ignore(self, reason: str, payload_type: str):
        ev = dict(self.base_event)
        ev.update({
            "type": "response_ignored",
            "reason": reason,
            "payload_type": payload_type,
        })
        _log(
            f"response_ignored request={self.base_event.get('request_id', '')} "
            f"reason={reason} payload_type={payload_type}")
        EVENTS.emit(ev)

    def _emit(self, event_name: str, payload: dict):
        ev = dict(self.base_event)
        ev.update({"event": event_name, "payload": payload})
        EVENTS.emit(ev)


class _NullObserver:
    def feed(self, data: bytes):
        return


def _pipe_exact(src, dst, observer=None, wire_logger=None, observer_before_send: bool = False):
    while True:
        chunk = src.recv(65536)
        if not chunk:
            if observer:
                finish = getattr(observer, "finish", None)
                if finish:
                    try:
                        finish()
                    except Exception as exc:
                        _log(f"observer finish failed without affecting proxy stream: {exc}")
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            return
        if wire_logger:
            try:
                wire_logger.log("in", chunk)
            except Exception as exc:
                _log(f"wire log failed without affecting proxy stream: {exc}")
                wire_logger = None
        if observer and observer_before_send:
            try:
                observer.feed(chunk)
            except Exception as exc:
                _log(f"observer failed without affecting proxy stream: {exc}")
                observer = None
        dst.sendall(chunk)
        if wire_logger:
            try:
                wire_logger.log("out", chunk)
            except Exception as exc:
                _log(f"wire log failed without affecting proxy stream: {exc}")
                wire_logger = None
        if observer and not observer_before_send:
            try:
                observer.feed(chunk)
            except Exception as exc:
                _log(f"observer failed without affecting proxy stream: {exc}")
                observer = None


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
            if int(wire_context.get("upstream_to_client_bytes", 0) or 0) > 0:
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
