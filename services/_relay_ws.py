"""WebSocket frame I/O + relay-script sync helpers for the filesystem relay.

Split out of filesystem_service.py to keep each module <=800 lines.
Re-exported from services.filesystem_service (invariant 1: import-path stability;
external callers import _ws_send_frame/_ws_recv_frame/_attach_sync_sock_to_loop).
"""

import asyncio
import base64
import hashlib
import logging
import os
import struct
import threading

logger = logging.getLogger(__name__)


def _invalidate_tool_relay_registry_cache() -> None:
    """Drop MCP tool registries when relay availability changes."""
    try:
        from services.tool_relay_service import ToolRelayService
        ToolRelayService.clear_registry_cache()
    except Exception:
        logger.debug("Tool relay registry cache invalidation failed", exc_info=True)


def _short_args(args: dict) -> str:
    """Compact dict repr for logging — caps long values, hides bulky bytes.

    FUSE ops carry args like {'path': '/...', 'fh': 3, 'data_b64': '<huge>'}.
    A single 1 MB write would otherwise put a megabyte of base64 into the
    log line; cap each value at 80 chars and replace bulky payloads with
    `<N bytes>` markers so we keep the line scannable.
    """
    if not args:
        return "{}"
    parts = []
    for k, v in args.items():
        if k in ("data_b64",):
            try:
                parts.append(f"{k}=<{len(v)}b>")
            except Exception:
                parts.append(f"{k}=<bulk>")
            continue
        s = repr(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return "{" + ", ".join(parts) + "}"

# Relay script files to sync (relative to tools/ directory)
_RELAY_SCRIPT_FILES = [
    "pawflow_relay_launcher.py", "fs_actions.py",
    "_fs_paths.py", "_fs_read.py", "_fs_grep.py", "_fs_edit.py",
    "fs_exec.py",
    "fs_screen.py", "fs_mcp.py", "fs_common.py",
]

_RELAY_RETRY_ATTEMPTS = 5
_RELAY_RETRY_DELAY_SECONDS = 5.0
_RELAY_RETRY_EXHAUSTED_MARKER = "Relay transport retry attempts exhausted"
_RELAY_DISCONNECT_ERRORS = (
    "Relay disconnected",
    "Relay not connected",
    "Failed to send to relay",
)


def _is_relay_disconnect_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _RELAY_DISCONNECT_ERRORS)


def _get_relay_scripts_bundle():
    """Read relay scripts from tools/ and return {filename: content_b64, hash: combined_hash}."""
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    scripts = {}
    h = hashlib.sha256()
    for fname in _RELAY_SCRIPT_FILES:
        fpath = os.path.join(tools_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "rb") as f:
                data = f.read()
            scripts[fname] = base64.b64encode(data).decode("ascii")
            h.update(data)
    return {"scripts": scripts, "hash": h.hexdigest()[:16]}


def _sync_relay_scripts(service, reg_info):
    """Push relay scripts to a connected relay if its version differs."""
    if not reg_info.get("containerized"):
        return  # Only sync to containerized relays
    bundle = _get_relay_scripts_bundle()
    if not bundle["scripts"]:
        return
    # Ask relay for its current script hash
    try:
        remote = service._request("script_hash", _request_timeout=30.0)
        if isinstance(remote, dict) and remote.get("hash") == bundle["hash"]:
            logger.debug("Relay scripts up to date (hash=%s)", bundle["hash"])
            return
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    # Push scripts
    try:
        result = service._request("update_scripts",
                                   scripts=bundle["scripts"],
                                   script_hash=bundle["hash"],
                                   _request_timeout=30.0)
        # _request unwraps {"ok": True, "data": {...}} → returns the
        # inner data dict, so check for its shape instead of "ok".
        if isinstance(result, dict) and "updated" in result:
            logger.info("Relay scripts synced (hash=%s, %d files, updated=%s)",
                         bundle["hash"], len(bundle["scripts"]),
                         result.get("updated"))
            if result.get("needs_restart"):
                logger.warning(
                    "Relay script update requires container restart "
                    "(pawflow_relay.py changed). Restart the relay.")
        else:
            logger.warning("Relay script sync rejected: %s", result)
    except Exception as e:
        logger.warning("Relay script sync failed: %s", e)


# Module-level WS frame helpers (shared by relay services)

def _attach_sync_sock_to_loop(sock, loop):
    """Bridge a sync socket (SSL or plain TCP) to an asyncio event loop.

    Python 3.14's ``loop.connect_accepted_socket()`` rejects SSLSockets
    outright (``TypeError: Socket cannot be of type SSLSocket``). Since
    TLS is terminated by the HTTPListener *before* it hands the socket
    to the WS route handler, we receive an already-wrapped socket with
    decrypted bytes — exactly what asyncio refuses to accept.

    The workaround: a background reader thread does blocking ``recv()``
    on the socket and feeds bytes into an ``asyncio.StreamReader`` via
    ``call_soon_threadsafe``. The writer is a minimal shim that does
    blocking ``sendall()`` directly on the socket (WS frames are small,
    so the in-thread send is fine).

    Returns ``(reader, writer_shim)`` usable with ``_ws_recv_frame`` /
    ``_ws_send_frame`` as if they came from ``connect_accepted_socket``.
    """
    sock.setblocking(True)
    reader = asyncio.StreamReader(loop=loop)
    stop_event = threading.Event()

    def _call_reader(method, *args):
        if stop_event.is_set() or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(method, *args)
        except RuntimeError:
            return

    def _read_pump():
        try:
            while not stop_event.is_set():
                try:
                    data = sock.recv(65536)
                except OSError as e:
                    _call_reader(reader.set_exception, e)
                    return
                if not data:
                    _call_reader(reader.feed_eof)
                    return
                _call_reader(reader.feed_data, data)
        except Exception as e:
            _call_reader(reader.set_exception, e)

    threading.Thread(
        target=_read_pump, daemon=True,
        name=f"ws-sock-read-{id(sock)}").start()

    class _SockWriter:
        __slots__ = ("_sock", "_closed", "_stop_event")

        def __init__(self, s, stop):
            self._sock = s
            self._closed = False
            self._stop_event = stop

        def write(self, data):
            if self._closed:
                return
            try:
                self._sock.sendall(data)
            except OSError:
                self._closed = True

        async def drain(self):
            return

        def close(self):
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            try:
                self._sock.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return reader, _SockWriter(sock, stop_event)


async def _ws_recv_frame(reader):
    hdr = await reader.readexactly(2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack('!H', await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack('!Q', await reader.readexactly(8))[0]
    if masked:
        mask = await reader.readexactly(4)
        data = await reader.readexactly(length)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    else:
        payload = await reader.readexactly(length)
    return opcode, payload


def _ws_close_info(payload: bytes) -> str:
    """Return a readable websocket close code/reason string."""
    if not payload:
        return "code=none reason=''"
    code = 0
    reason = ""
    try:
        if len(payload) >= 2:
            code = struct.unpack('!H', payload[:2])[0]
            reason = payload[2:].decode('utf-8', errors='replace')
        else:
            reason = payload.decode('utf-8', errors='replace')
    except Exception:
        reason = repr(payload[:120])
    return f"code={code or 'none'} reason={reason!r}"


async def _ws_send_frame(writer, data, opcode=0x01):
    frame = bytes([0x80 | opcode])
    length = len(data)
    if length < 126:
        frame += bytes([length])
    elif length < 65536:
        frame += bytes([126]) + struct.pack('!H', length)
    else:
        frame += bytes([127]) + struct.pack('!Q', length)
    frame += data
    writer.write(frame)
    await writer.drain()


# Filesystem Service

# ── Filesystem Service ────────────────────────────────────────────
