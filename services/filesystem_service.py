"""Unified Filesystem Service — WS listener that relays connect to.

Like HTTPListenerService: binds a port, accepts relay connections.
Multiple services can share a port (different paths).

Config:
    port: int       — WS listener port (default: 9091)
    path: str       — WS endpoint path (default: /ws/relay)
    token: str      — Shared token (relay must match to connect)
    mode: str       — "readwrite" | "readonly" (informational)

Relay usage:
    python tools/pawflow_relay.py --server ws://host:port/path
        --relay-id <service_id> --token <token> --dir /path
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

# Relay script files to sync (relative to tools/ directory)
_RELAY_SCRIPT_FILES = [
    "pawflow_relay.py", "fs_actions.py", "fs_exec.py",
    "fs_screen.py", "fs_mcp.py",
]


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
        remote = service._request("script_hash")
        if isinstance(remote, dict) and remote.get("hash") == bundle["hash"]:
            logger.debug("Relay scripts up to date (hash=%s)", bundle["hash"])
            return
    except Exception:
        pass  # Relay doesn't support script_hash yet, push anyway
    # Push scripts
    try:
        result = service._request("update_scripts",
                                   scripts=bundle["scripts"],
                                   script_hash=bundle["hash"])
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

    def _read_pump():
        try:
            while True:
                try:
                    data = sock.recv(65536)
                except OSError as e:
                    loop.call_soon_threadsafe(reader.set_exception, e)
                    return
                if not data:
                    loop.call_soon_threadsafe(reader.feed_eof)
                    return
                loop.call_soon_threadsafe(reader.feed_data, data)
        except Exception as e:
            loop.call_soon_threadsafe(reader.set_exception, e)

    threading.Thread(
        target=_read_pump, daemon=True,
        name=f"ws-sock-read-{id(sock)}").start()

    class _SockWriter:
        __slots__ = ("_sock", "_closed")

        def __init__(self, s):
            self._sock = s
            self._closed = False

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
            try:
                self._sock.close()
            except Exception:
                pass

    return reader, _SockWriter(sock)


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

class RelayService(BaseService):
    """Filesystem service backed by a reverse WebSocket relay."""

    TYPE = "relay"
    VERSION = "2.0.0"
    NAME = "Filesystem (Relay)"
    DESCRIPTION = "Remote filesystem access via WebSocket relay"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._port = int(config.get("port", 9091))
        self._service_id = config.get("_service_id", "")

        self._project_context: Optional[Dict] = None  # auto-fetched on relay connect
        self._relay_shells: List[str] = []  # available shells on the relay system
        self._relay_info: Dict[str, Any] = {}  # full registration info (platform, containerized, etc.)

        # Relay connection pool — supports multiple connections for resilience
        self._relay_pool: List[Dict] = []  # [{"reader", "writer", "loop"}]
        self._relay_pool_lock = threading.Lock()
        self._relay_idx = 0

        # Pending requests: {request_id: (Event, result_holder)}
        self._pending: Dict[str, tuple] = {}
        self._pending_lock = threading.Lock()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "port": {"type": "integer", "required": False, "default": 9091,
                     "description": "WebSocket listener port for relay connections"},
            "path": {"type": "string", "required": False, "default": "/ws/relay",
                     "description": "WebSocket endpoint path"},
            "token": {"type": "string", "required": True, "sensitive": True,
                      "description": "Authentication token (relay must match)"},
            "mode": {"type": "select", "required": False, "default": "readwrite",
                     "options": ["readwrite", "readonly"],
                     "description": "Access mode for file operations"},
        }

    @property
    def service_id(self) -> str:
        return self._service_id

    def get_project_prompt(self) -> str:
        """Build a system prompt supplement from the auto-scanned project context."""
        ctx = self._project_context
        if not ctx:
            return ""
        lines = [f"\n\n## Filesystem: {self._service_id}"]
        if ctx.get("project_types"):
            lines.append(f"Project type: {', '.join(ctx['project_types'])}")
        if ctx.get("git"):
            lines.append(f"Git repo (branch: {ctx.get('git_branch', '?')})")
        # .pawflow.md or CLAUDE.md — project instructions
        for key in (".pawflow.md", "CLAUDE.md"):
            if key in ctx.get("config_files", {}):
                lines.append(f"\n### {key}\n{ctx['config_files'][key]}")
        # README summary (first 2000 chars)
        for key in ("README.md", "readme.md"):
            if key in ctx.get("config_files", {}):
                readme = ctx["config_files"][key][:2000]
                lines.append(f"\n### {key} (excerpt)\n{readme}")
                break
        # File tree
        if ctx.get("tree"):
            tree = ctx["tree"][:3000]
            lines.append(f"\n### Project structure\n```\n{tree}\n```")
        return "\n".join(lines)

    def connect(self):
        from services.http_listener_service import HTTPListenerService
        instances = HTTPListenerService.all_instances()
        if not instances:
            logger.warning('RelayService %s: no HTTPListenerService running yet, route not registered',
                           self._service_id)
            self._initialized = True
            return
        listener = next(iter(instances.values()))
        route = f'/ws/relay/{self._service_id}'
        self._route_path = route
        listener.register_route('GET', route, self._service_id, callback=None, ws_handler=self._handle_ws)
        self._connection = listener
        self._initialized = True
        logger.info('RelayService %s registered on main listener path %s', self._service_id, route)

    def is_connected(self) -> bool:
        with self._relay_pool_lock:
            return len(self._relay_pool) > 0

    def disconnect(self):
        if self._connection and getattr(self, '_route_path', ''):
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception as e:
                logger.error('Failed to unregister relay route %s: %s', self._route_path, e, exc_info=True)
            self._connection = None

    def _handle_ws(self, sock, path_params, meta):
        import asyncio
        remote = meta.get('remote_addr', '?')
        try:
            loop = asyncio.new_event_loop()
            try:
                reader, writer = _attach_sync_sock_to_loop(sock, loop)
                loop.run_until_complete(
                    self._serve_relay_session(reader, writer, loop, remote))
            finally:
                loop.close()
        except Exception as e:
            logger.error('Relay WS handler error (%s): %s', remote, e, exc_info=True)

    async def _serve_relay_session(self, reader, writer, loop, remote):
        import asyncio
        service = self
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode('utf-8'))
            if reg.get('type') != 'register':
                return
            relay_token = reg.get('token', '')
            if not relay_token or relay_token != service.config.get('token', ''):
                await _ws_send_frame(writer, json.dumps(
                    {'type': 'error', 'message': 'Token mismatch'}).encode())
                return
            relay_id = reg.get('relay_id', '')
            reg_info = reg.get('info', {})
            logger.info('Relay connected: %s (addr=%s)', relay_id, remote)
            if reg_info.get('shells'):
                service._relay_shells = reg_info['shells']
            if reg_info:
                service._relay_info = reg_info
            service._relay_addr = remote
            await _ws_send_frame(writer, json.dumps({
                'type': 'registered', 'relay_id': relay_id}).encode())
            service._set_relay(reader, writer, loop)
            self._spawn_ctx_sync(reg_info, relay_id)
            await self._relay_main_loop(reader, writer, service)
        except Exception as e:
            _err_str = str(e)
            # Peer-initiated close is the nominal end of a relay session:
            # clean FIN ("0 bytes read"), ECONNRESET (WinError 10054 —
            # container killed mid-read on Windows), or StreamReader's
            # IncompleteReadError. Log as info so we don't spam ERROR
            # tracebacks for routine shutdowns.
            _peer_close = (
                '0 bytes read' in _err_str
                or '10054' in _err_str
                or 'reset by peer' in _err_str.lower()
                or isinstance(e, (ConnectionResetError, asyncio.IncompleteReadError)))
            if _peer_close:
                logger.info('Relay disconnected: %s (closed by peer)', remote)
            else:
                logger.error('Relay connection error (%s): %s', remote, e, exc_info=True)
        finally:
            try:
                service._clear_relay(reader=reader)
            except Exception as e:
                logger.debug('_clear_relay failed: %s', e, exc_info=True)
            try:
                writer.close()
            except Exception as e:
                logger.debug('writer.close failed: %s', e, exc_info=True)
            logger.info('Relay disconnected: %s', remote)

    def _spawn_ctx_sync(self, reg_info, relay_id):
        service = self
        def _fetch_ctx_and_sync():
            try:
                ctx = service._request('project_context', '.')
                service._project_context = ctx
                logger.info('Project context loaded for %s: %s',
                             relay_id, ctx.get('project_types', []))
            except Exception as e:
                logger.debug('Failed to load project context: %s', e, exc_info=True)
            try:
                _sync_relay_scripts(service, reg_info)
            except Exception as e:
                logger.debug('Relay script sync failed: %s', e, exc_info=True)
        threading.Thread(target=_fetch_ctx_and_sync, daemon=True,
                         name=f'relay-ctx-{relay_id}').start()

    async def _relay_main_loop(self, reader, writer, service):
        import asyncio
        KEEPALIVE = 120
        while True:
            try:
                opcode, payload = await asyncio.wait_for(
                    _ws_recv_frame(reader), timeout=KEEPALIVE)
            except asyncio.TimeoutError:
                await _ws_send_frame(writer, json.dumps({'type': 'ping'}).encode())
                continue
            if opcode == 0x08:
                break
            if opcode == 0x09:
                await _ws_send_frame(writer, payload, opcode=0x0A)
                continue
            if opcode != 0x01:
                continue
            msg = json.loads(payload.decode('utf-8'))
            await self._dispatch_relay_msg(msg, writer, service)

    async def _dispatch_relay_msg(self, msg, writer, service):
        mtype = msg.get('type')
        if mtype in ('result', 'error'):
            service._resolve_pending(msg)
        elif mtype == 'progress':
            service._dispatch_progress(msg)
        elif mtype == 'exec_output':
            service._dispatch_exec_output(msg)
        elif mtype == 'http_response':
            service._dispatch_http_response(msg)
        elif mtype == 'terminal_data':
            try:
                from services.terminal_proxy import dispatch_terminal_data
                dispatch_terminal_data(msg.get('session_id', ''), msg.get('data', ''))
            except Exception as e:
                logger.debug('terminal_data dispatch failed: %s', e, exc_info=True)
        elif mtype == 'terminal_exit':
            try:
                from services.terminal_proxy import dispatch_terminal_exit
                dispatch_terminal_exit(msg.get('session_id', ''))
            except Exception as e:
                logger.debug('terminal_exit dispatch failed: %s', e, exc_info=True)
        elif mtype == 'cs_ws_data':
            try:
                from services.code_server_proxy import dispatch_cs_ws_data
                dispatch_cs_ws_data(service._service_id,
                                     msg.get('session_id', ''),
                                     msg.get('data', ''),
                                     msg.get('opcode', 1))
            except Exception as e:
                logger.debug('cs_ws_data dispatch failed: %s', e, exc_info=True)
        elif mtype == 'cs_ws_close':
            try:
                from services.code_server_proxy import dispatch_cs_ws_close
                dispatch_cs_ws_close(service._service_id, msg.get('session_id', ''))
            except Exception as e:
                logger.debug('cs_ws_close dispatch failed: %s', e, exc_info=True)
        elif mtype == 'ping':
            await _ws_send_frame(writer, json.dumps({'type': 'pong'}).encode())

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    # ── Relay connection management ──

    def _set_relay(self, reader, writer, loop):
        """Add a relay connection to the pool."""
        with self._relay_pool_lock:
            self._relay_pool.append({"reader": reader, "writer": writer, "loop": loop})
            count = len(self._relay_pool)
        logger.info("Relay pool: %d connection(s) for '%s'", count, self._service_id)
        # Notify all SSE clients to refresh resources (relay status changed)
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            with bus._lock:
                cids = list(bus._subscribers.keys())
            for cid in cids:
                bus.publish_event(cid, "relay_status_changed", {
                    "relay_id": self._service_id, "connected": True})
        except Exception:
            pass

    def _clear_relay(self, reader=None):
        """Remove a connection from the pool (by reader), or all if None."""
        with self._relay_pool_lock:
            if reader:
                self._relay_pool = [c for c in self._relay_pool if c["reader"] is not reader]
            else:
                self._relay_pool.clear()
            alive = len(self._relay_pool)
        if alive == 0:
            with self._pending_lock:
                for rid, (evt, holder) in self._pending.items():
                    holder["error"] = "Relay disconnected"
                    evt.set()
                self._pending.clear()
        # Notify SSE clients
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            with bus._lock:
                cids = list(bus._subscribers.keys())
            for cid in cids:
                bus.publish_event(cid, "relay_status_changed", {
                    "relay_id": self._service_id, "connected": alive > 0})
        except Exception:
            pass

    def _resolve_pending(self, msg: dict):
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry:
            evt, holder = entry
            if msg.get("type") == "error":
                holder["error"] = msg.get("error", "Unknown relay error")
            else:
                holder["data"] = msg.get("data", {})
            evt.set()

    def cancel_pending(self, request_id: str):
        """Cancel a pending request — unblock the waiting thread with an error."""
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry:
            evt, holder = entry
            holder["error"] = "[Interrupted by user]"
            evt.set()

    def _dispatch_progress(self, msg: dict):
        """Forward progress messages to registered callback or terminal proxy."""
        data = msg.get("data", {})

        # Terminal data/exit from local terminal (forwarded via host helper progress)
        if isinstance(data, dict) and data.get("type") in ("terminal_data", "terminal_exit"):
            try:
                if data["type"] == "terminal_data":
                    from services.terminal_proxy import dispatch_terminal_data
                    dispatch_terminal_data(data.get("session_id", ""), data.get("data", ""))
                elif data["type"] == "terminal_exit":
                    from services.terminal_proxy import dispatch_terminal_exit
                    dispatch_terminal_exit(data.get("session_id", ""))
            except Exception:
                pass
            return

        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_progress")
            if cb:
                try:
                    cb(data)
                except Exception:
                    pass

    def _dispatch_exec_output(self, msg: dict):
        """Forward streaming exec_output to the registered callback (if any)."""
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_output")
            if cb:
                try:
                    cb(msg.get("stream", "stdout"), msg.get("data", ""))
                except Exception:
                    pass

    def _dispatch_http_response(self, msg: dict):
        """Forward streaming http_response chunks to the registered callback.

        Message kinds: "start", "chunk", "end" — see fs_http.action_http_fetch.
        """
        request_id = msg.get("request_id", "")
        with self._pending_lock:
            entry = self._pending.get(request_id)
        if entry:
            _, holder = entry
            cb = holder.get("_on_output")
            if cb:
                try:
                    cb(msg.get("kind", ""), msg.get("data"))
                except Exception:
                    pass

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a command to the relay and wait for the result (sync).

        Uses connection pool with round-robin + failover.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            raise Exception(
                f"Relay not connected to '{self._service_id}'. "
                f"Start: python tools/pawflow_relay.py "
                f"--server wss://<server_host>:<server_port>/ws/relay/{self._service_id} "
                f"--relay-id {self._service_id} --token <token> --dir <path>"
            )

        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            "path": path,
            **kwargs,
        }).encode("utf-8")

        # Round-robin with failover
        last_err = None
        for attempt in range(len(pool)):
            idx = (self._relay_idx + attempt) % len(pool)
            conn = pool[idx]
            writer, loop = conn["writer"], conn["loop"]

            async def _send(w=writer):
                await _ws_send_frame(w, payload)

            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=10)
                self._relay_idx = idx + 1
                last_err = None
                break
            except Exception as e:
                last_err = e
                continue

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        evt.wait()  # no limit — relay operations take as long as they take

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        # Check for relay-level errors
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    def _request_with_progress(self, action: str, on_progress=None,
                               timeout=None, **kwargs) -> Any:
        """Like _request but supports progress callbacks.

        Progress messages arriving before the final result are dispatched
        to on_progress(data_dict) via _dispatch_progress.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            raise Exception(f"Relay not connected to '{self._service_id}'.")

        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        if on_progress:
            holder["_on_progress"] = on_progress

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            **kwargs,
        }).encode("utf-8")

        last_err = None
        for attempt in range(len(pool)):
            idx = (self._relay_idx + attempt) % len(pool)
            conn = pool[idx]
            writer, loop = conn["writer"], conn["loop"]

            async def _send(w=writer):
                await _ws_send_frame(w, payload)

            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=10)
                self._relay_idx = idx + 1
                last_err = None
                break
            except Exception as e:
                last_err = e
                continue

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        evt.wait(timeout=timeout)

        with self._pending_lock:
            self._pending.pop(request_id, None)

        if not evt.is_set():
            raise Exception("Timeout waiting for relay response")
        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    def _request_stream(self, action: str, path: str = ".",
                        on_output=None, **kwargs) -> Any:
        """Like _request but registers an on_output callback for streaming.

        exec_output messages arriving before the final result are dispatched
        to on_output(stream, data) via _dispatch_exec_output.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            raise Exception(f"Relay not connected to '{self._service_id}'.")

        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        if on_output:
            holder["_on_output"] = on_output

        with self._pending_lock:
            self._pending[request_id] = (evt, holder)

        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            "path": path,
            **kwargs,
        }).encode("utf-8")

        last_err = None
        for attempt in range(len(pool)):
            idx = (self._relay_idx + attempt) % len(pool)
            conn = pool[idx]
            writer, loop = conn["writer"], conn["loop"]

            async def _send(w=writer):
                await _ws_send_frame(w, payload)

            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=10)
                self._relay_idx = idx + 1
                last_err = None
                break
            except Exception as e:
                last_err = e
                continue

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Wait for relay response — no limit unless timeout explicitly given
        _wait_timeout = kwargs.get("timeout")
        if not evt.wait(timeout=_wait_timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    # ── Filesystem interface ──

    def list_dir(self, path: str = "."):
        from core.filesystem import FilesystemEntry
        data = self._request("list_dir", path)
        return [FilesystemEntry(**e) if isinstance(e, dict) else e for e in data]

    def read_file(self, path: str) -> bytes:
        try:
            data = self._request("read_file", path)
            if isinstance(data, dict) and "content" in data:
                return base64.b64decode(data["content"])
            return data.encode("utf-8") if isinstance(data, str) else data
        except Exception as e:
            if "too large" in str(e).lower():
                return self._read_chunked(path)
            raise

    def _read_chunked(self, path: str) -> bytes:
        """Read a large file in chunks via the relay."""
        first = self._request("read_file_chunked", path)
        chunks = [base64.b64decode(first["data"])]
        total_chunks = first.get("total_chunks", 1)
        chunk_size = first.get("chunk_size", 1024 * 1024)
        for i in range(1, total_chunks):
            chunk = self._request("read_chunk", path, index=i, chunk_size=chunk_size)
            chunks.append(base64.b64decode(chunk["data"]))
            if chunk.get("done"):
                break
        return b"".join(chunks)

    def write_file(self, path: str, content: bytes):
        if len(content) > 50 * 1024 * 1024:  # > 50MB → chunked
            self._write_chunked(path, content)
        else:
            self._request("write_file", path,
                           content=base64.b64encode(content).decode("ascii"),
                           base64=True)

    def _write_chunked(self, path: str, content: bytes):
        """Write a large file in chunks via the relay."""
        chunk_size = 1024 * 1024  # 1MB
        total = len(content)
        for i in range(0, total, chunk_size):
            chunk = content[i:i + chunk_size]
            done = (i + chunk_size) >= total
            self._request("write_file_chunked", path,
                           index=i // chunk_size,
                           data=base64.b64encode(chunk).decode("ascii"),
                           done=done)

    def delete_file(self, path: str):
        self._request("delete_file", path)

    def mkdir(self, path: str):
        self._request("mkdir", path)

    def stat(self, path: str):
        from core.filesystem import FilesystemEntry
        import dataclasses
        data = self._request("stat", path)
        if isinstance(data, dict):
            # Filter to known fields only (relay may return extra like 'created')
            valid = {f.name for f in dataclasses.fields(FilesystemEntry)}
            return FilesystemEntry(**{k: v for k, v in data.items() if k in valid})
        return data

    def exists(self, path: str) -> bool:
        data = self._request("exists", path)
        return data.get("exists", False) if isinstance(data, dict) else bool(data)

    def search(self, path: str, pattern: str, recursive: bool = True):
        return self._request("search", path, pattern=pattern, recursive=recursive)

    def grep(self, path: str, regex: str, recursive: bool = True, **kwargs):
        return self._request("grep", path, regex=regex, recursive=recursive, **kwargs)

    def find_replace(self, path: str, pattern: str, replacement: str):
        return self._request("find_replace", path, pattern=pattern, replacement=replacement)

    def edit(self, path: str, old_string: str, new_string: str, replace_all: bool = False):
        return self._request("edit", path, old_string=old_string,
                              new_string=new_string, replace_all=replace_all)

    def batch_edit(self, edits: list):
        return self._request("batch_edit", ".", edits=edits)

    def apply_patch(self, patch: str):
        return self._request("apply_patch", ".", patch=patch)

    def edit_notebook(self, path: str, cell_index: int, new_source: str = "",
                      cell_type: str = "", operation: str = "edit"):
        return self._request("edit_notebook", path, cell_index=cell_index,
                              new_source=new_source, cell_type=cell_type,
                              operation=operation)

    def exec(self, path: str, command: str, timeout=None, shell: str = "", env: dict = None):
        kwargs = {"command": command}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if shell:
            kwargs["shell"] = shell
        if env:
            kwargs["env"] = env
        return self._request("exec", path, **kwargs)

    def exec_stream(self, path: str, command: str, timeout=None,
                    shell: str = "", on_output=None):
        """Execute a command with streaming output via on_output(stream, data).

        Returns the final result dict (stdout, stderr, returncode).
        on_output is called for each line as it arrives from the relay.
        """
        kwargs = {"command": command, "timeout": timeout}
        if shell:
            kwargs["shell"] = shell
        return self._request_stream("exec_stream", path, on_output=on_output, **kwargs)

    def http_fetch(self, url: str, method: str = "GET",
                    headers: dict = None, body: bytes = b"",
                    timeout: int = 300, local: bool = False) -> dict:
        """Sync HTTP fetch via the relay container.

        Returns {ok, status, headers, body_bytes} — body is decoded
        from the relay's base64 wire format. Use this when PawFlow's
        own HTTP stack is fingerprint-blocked (Cloudflare on Windows
        Python urllib) but the relay's Linux stack works.

        `local=True` forwards to the user's host helper (PAWFLOW_HOST_HELPER),
        same semantic as the screen / desktop actions.
        """
        import base64 as _b64
        _body = body if isinstance(body, (bytes, bytearray)) else (body or b"")
        result = self._request(
            "http_fetch", ".",
            local=local,
            url=url,
            method=method,
            headers=headers or {},
            body=_b64.b64encode(bytes(_body)).decode("ascii") if _body else "",
            timeout=timeout,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            err = (result or {}).get("error", "http_fetch returned no result")
            raise Exception(f"relay http_fetch failed: {err}")
        b64 = result.get("body", "")
        body_bytes = _b64.b64decode(b64) if b64 else b""
        return {
            "ok": True,
            "status": int(result.get("status", 0)),
            "headers": result.get("headers") or {},
            "body_bytes": body_bytes,
        }

    def http_fetch_stream(self, url: str, method: str = "GET",
                           headers: dict = None, body: bytes = b"",
                           timeout: int = 300, on_output=None):
        """Fetch an HTTP URL through the relay with streaming response.

        on_output(kind, data) is called with kind in {"start", "chunk", "end"}.
        Used by the /relay-proxy/ route to pipe Anthropic API calls through
        a user-local endpoint (llama-server, etc.).

        local=True ensures the request is executed on the user's host (via
        PawCode CLI), not inside the relay container — so 'localhost' in
        the target URL means the user's actual localhost.
        """
        import base64 as _b64
        _body = body if isinstance(body, (bytes, bytearray)) else (body or b"")
        return self._request_stream(
            "http_fetch", ".",
            on_output=on_output,
            local=True,
            url=url,
            method=method,
            headers=headers or {},
            body=_b64.b64encode(bytes(_body)).decode("ascii") if _body else "",
            timeout=timeout,
        )

    # ── Git ──

    # ── Aliases (LLMs often drop the _file suffix) ──

    read = read_file
    write = write_file
    delete = delete_file

    # ── Git ──

    def git_status(self, path="."): return self._request("git_status", path)
    def git_log(self, path=".", count=10): return self._request("git_log", path, count=count)
    def git_diff(self, path=".", ref=""): return self._request("git_diff", path, ref=ref)
    def git_commit(self, path=".", message="", files=None, amend=False): return self._request("git_commit", path, message=message, files=files or [], amend=amend)
    def git_pull(self, path="."): return self._request("git_pull", path)
    def git_push(self, path="."): return self._request("git_push", path)
    def git_checkout(self, path=".", ref=""): return self._request("git_checkout", path, ref=ref)
    def git_add(self, path=".", files=None): return self._request("git_add", path, files=files or [])
    def git_reset(self, path=".", files=None, ref="", mode="mixed"): return self._request("git_reset", path, files=files or [], ref=ref, mode=mode)
    def git_stash(self, path=".", operation="push", message="", index=0): return self._request("git_stash", path, operation=operation, message=message, index=index)
    def git_branch(self, path=".", operation="list", branch="", base="", force=False): return self._request("git_branch", path, operation=operation, branch=branch, base=base, force=force)
    def git_merge(self, path=".", branch="", no_ff=False): return self._request("git_merge", path, branch=branch, no_ff=no_ff)
    def git_rebase(self, path=".", onto="", operation="start"): return self._request("git_rebase", path, onto=onto, operation=operation)
    def git_cherry_pick(self, path=".", commits=None): return self._request("git_cherry_pick", path, commits=commits or [])
    def git_tag(self, path=".", operation="list", tag="", message=""): return self._request("git_tag", path, operation=operation, tag=tag, message=message)
    def git_blame(self, path=".", file="", start_line=0, end_line=0): return self._request("git_blame", path, file=file, start_line=start_line, end_line=end_line)
    def project_init(self, path=".", force=False): return self._request("project_init", path, force=force)

    def git_worktree_list(self, path="."):
        return self._request("git_worktree_list", path)

    def git_worktree_add(self, path=".", branch="", worktree_path="", create_new_branch=False):
        return self._request("git_worktree_add", path, branch=branch,
                              worktree_path=worktree_path, create_new_branch=create_new_branch)

    def git_worktree_remove(self, path=".", worktree_path=""):
        return self._request("git_worktree_remove", path, worktree_path=worktree_path)


# Register with ServiceFactory
ServiceFactory.register(RelayService)
