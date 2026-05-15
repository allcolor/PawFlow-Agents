#!/usr/bin/env python3
"""Claude Code lifecycle hook bridge for claude-code-interactive."""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import sys
import time
from urllib.parse import urlparse


def _masked_frame(obj: dict) -> bytes:
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
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bytes(header) + payload


def _recvn(sock, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("event WS closed")
        out += chunk
    return out


def _recv_json(sock) -> dict:
    hdr = _recvn(sock, 2)
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(_recvn(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recvn(sock, 8), "big")
    return json.loads(_recvn(sock, length).decode("utf-8"))


def _connect(url: str, token: str, session_token: str):
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/ws/cc-interactive/events"
    sock = socket.create_connection((host, port), timeout=5)
    if parsed.scheme == "wss":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    internal = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
    cookie = f"Cookie: pawflow_internal={internal}\r\n" if internal else ""
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"{cookie}\r\n"
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
    sock.sendall(_masked_frame({
        "type": "register",
        "token": token,
        "session_token": session_token,
        "container_id": os.environ.get("HOSTNAME", ""),
        "client_kind": "hook",
    }))
    ack = _recv_json(sock)
    if ack.get("type") == "error":
        raise ConnectionError(ack.get("message", "registration failed"))
    return sock


def _compact_input(raw: dict) -> dict:
    keep = {
        "hook_event_name", "session_id", "cwd", "permission_mode",
        "source", "trigger", "error", "matcher", "agent_id", "agent_type",
    }
    return {k: v for k, v in raw.items() if k in keep}


def main() -> int:
    session_token = os.environ.get("PAWFLOW_CCI_SESSION_TOKEN", "")
    url = os.environ.get("PAWFLOW_CCI_EVENT_URL", "")
    token = os.environ.get("PAWFLOW_CCI_EVENT_TOKEN", "")
    if not session_token or not url or not token:
        return 0
    try:
        raw = json.loads(sys.stdin.read() or "{}")
        event = {
            "type": "hook",
            "hook_event_name": raw.get("hook_event_name", ""),
            "input": _compact_input(raw),
            "container_id": os.environ.get("HOSTNAME", ""),
            "timestamp": time.time(),
        }
        sock = _connect(url, token, session_token)
        try:
            sock.sendall(_masked_frame({"type": "event", "event": event}))
        finally:
            sock.close()
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
