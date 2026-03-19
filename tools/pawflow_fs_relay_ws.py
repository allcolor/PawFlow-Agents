#!/usr/bin/env python3
"""PawFlow Filesystem Relay — Standalone WebSocket relay for filesystem access.

Same functionality as pawflow_fs_relay.py but over persistent WebSocket
connections for lower latency on frequent operations. Zero external
dependencies (stdlib only).

Usage:
    python pawflow_fs_relay_ws.py --port 9877 --dir /home/user/data --secret abc123
    python pawflow_fs_relay_ws.py --port 9877 --dir C:\\Users\\me\\project --secret abc --readonly
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── WebSocket opcodes ────────────────────────────────────────────

OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


# ── Minimal WebSocket frame helpers (RFC 6455) ───────────────────

async def ws_read_frame(reader: asyncio.StreamReader) -> Optional[Tuple[int, bytes]]:
    """Read one WebSocket frame. Returns (opcode, payload) or None on EOF."""
    try:
        hdr = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None

    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F

    # Extended payload length
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]

    # Masking key (client→server frames must be masked per RFC 6455)
    mask = await reader.readexactly(4) if masked else None

    # Payload
    payload = bytearray(await reader.readexactly(length))

    # Unmask
    if mask:
        for i in range(len(payload)):
            payload[i] ^= mask[i & 3]

    return opcode, bytes(payload)


def ws_make_frame(opcode: int, payload: bytes) -> bytes:
    """Build an unmasked WebSocket frame (server→client)."""
    frame = bytearray()
    frame.append(0x80 | opcode)  # FIN + opcode

    length = len(payload)
    if length <= 125:
        frame.append(length)
    elif length <= 65535:
        frame.append(126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(127)
        frame.extend(struct.pack("!Q", length))

    frame.extend(payload)
    return bytes(frame)


async def ws_send_text(writer: asyncio.StreamWriter, text: str):
    """Send a text frame."""
    writer.write(ws_make_frame(OP_TEXT, text.encode("utf-8")))
    await writer.drain()


async def ws_send_json(writer: asyncio.StreamWriter, ok: bool,
                       data=None, error=None):
    """Send a JSON response frame."""
    resp: Dict[str, Any] = {"ok": ok}
    if ok and data is not None:
        resp["data"] = data
    elif not ok and error:
        resp["error"] = error
    await ws_send_text(writer, json.dumps(resp, ensure_ascii=False, default=str))


# ── Filesystem action implementations ────────────────────────────

def _resolve(root_dir: str, rel_path: str) -> Optional[str]:
    """Resolve relative path safely. Returns None on traversal."""
    root = Path(root_dir).resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return str(target)



# Actions imported from shared module (no duplication)
from fs_actions import ACTIONS as _ACTIONS, WRITE_ACTIONS as _WRITE_ACTIONS



# ── WebSocket connection handler ─────────────────────────────────

async def _handle_client(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter,
                         root_dir: str, secret: str, readonly: bool,
                         allow_exec: bool = False):
    """Handle a single WebSocket client."""
    addr = writer.get_extra_info("peername")
    tag = f"{addr[0]}:{addr[1]}" if addr else "?"

    try:
        # ── HTTP upgrade handshake ──
        request = await reader.readuntil(b"\r\n\r\n")
        headers = {}
        for line in request.decode("latin-1").split("\r\n")[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        if "upgrade" not in headers.get("connection", "").lower():
            writer.close()
            return

        ws_key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(
            hashlib.sha1((ws_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()

        writer.write(
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            f"\r\n".encode("latin-1")
        )
        await writer.drain()
        sys.stderr.write(f"[FSRelay-WS] [{tag}] Connected\n")

        # ── Frame loop ──
        while True:
            frame = await ws_read_frame(reader)
            if frame is None:
                break

            opcode, payload = frame

            if opcode == OP_CLOSE:
                writer.write(ws_make_frame(OP_CLOSE, b""))
                await writer.drain()
                break
            elif opcode == OP_PING:
                writer.write(ws_make_frame(OP_PONG, payload))
                await writer.drain()
                continue
            elif opcode != OP_TEXT:
                continue

            # Parse JSON request
            try:
                req = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                await ws_send_json(writer, False, error=f"Invalid JSON: {e}")
                continue

            # Validate secret
            if not hmac.compare_digest(req.get("secret", ""), secret):
                await ws_send_json(writer, False, error="Invalid secret")
                continue

            action = req.get("action", "")
            rel_path = req.get("path", ".")

            # Readonly check
            if readonly and action in _WRITE_ACTIONS:
                sys.stderr.write(f"[FSRelay-WS] [FAIL] {action} path={rel_path} | readonly\n")
                await ws_send_json(writer, False, error="Operation not allowed in readonly mode")
                continue

            # Resolve path
            abs_path = _resolve(root_dir, rel_path)
            if abs_path is None:
                sys.stderr.write(f"[FSRelay-WS] [FAIL] {action} path={rel_path} | traversal\n")
                await ws_send_json(writer, False, error=f"Path traversal blocked: {rel_path}")
                continue

            # Dispatch
            handler_fn = _ACTIONS.get(action)
            if not handler_fn:
                await ws_send_json(writer, False, error=f"Unknown action: {action}")
                continue

            try:
                if action == "exec":
                    result = handler_fn(root_dir, abs_path, req, allow_exec=allow_exec)
                else:
                    result = handler_fn(root_dir, abs_path, req)
                sys.stderr.write(f"[FSRelay-WS] [OK] {action} path={rel_path}\n")
                await ws_send_json(writer, True, data=result)
            except Exception as e:
                sys.stderr.write(f"[FSRelay-WS] [FAIL] {action} path={rel_path} | {e}\n")
                await ws_send_json(writer, False, error=str(e))

    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    except Exception as e:
        sys.stderr.write(f"[FSRelay-WS] [{tag}] Error: {e}\n")
    finally:
        sys.stderr.write(f"[FSRelay-WS] [{tag}] Disconnected\n")
        writer.close()


# ── Main ──────────────────────────────────────────────────────────

async def _serve(bind: str, port: int, root_dir: str, secret: str, readonly: bool,
                  allow_exec: bool = False):
    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, root_dir, secret, readonly, allow_exec),
        bind, port,
    )
    sys.stderr.write(f"[FSRelay-WS] Listening on {bind}:{port} ...\n")
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(
        description="PawFlow Filesystem Relay — Secure WebSocket filesystem access",
    )
    parser.add_argument("--port", type=int, default=9877,
                        help="Port to listen on (default: 9877)")
    parser.add_argument("--dir", required=True,
                        help="Root directory for filesystem access")
    parser.add_argument("--secret", required=True,
                        help="Shared secret for authentication")
    parser.add_argument("--allow-exec", action="store_true",
                        help="Allow shell command execution (disabled by default)")
    parser.add_argument("--readonly", action="store_true",
                        help="Reject write/delete operations")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    args = parser.parse_args()

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[FSRelay-WS] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    mode = "readonly" if args.readonly else "readwrite"
    masked = args.secret[:2] + "*" * max(0, len(args.secret) - 2)

    sys.stderr.write(
        f"\n  PawFlow Filesystem Relay (WebSocket)\n"
        f"  ──────────────────────────────────\n"
        f"  Bind:      {args.bind}:{args.port}\n"
        f"  Directory: {root_dir}\n"
        f"  Mode:      {mode}\n"
        f"  Secret:    {masked}\n\n"
    )

    try:
        asyncio.run(_serve(args.bind, args.port, root_dir, args.secret, args.readonly,
                           allow_exec=args.allow_exec))
    except KeyboardInterrupt:
        sys.stderr.write("\n[FSRelay-WS] Shutting down.\n")


if __name__ == "__main__":
    main()
