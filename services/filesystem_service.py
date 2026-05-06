"""Unified Filesystem Service — WS route on the main HTTPListenerService.

On connect(), registers /ws/relay/<service_id> on the shared main listener;
the relay client then reverse-connects to that URL and streams filesystem
commands over the pool.

Config:
    token: str      — Shared token (relay must match to connect)
    mode: str       — "readwrite" | "readonly" (informational)

Relay usage:
    python tools/pawflow_relay.py --server wss://<host>:<main_port>/ws/relay/<service_id>
        --relay-id <service_id> --token <token> --dir /path
"""

import asyncio
import base64
import contextlib
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
    "pawflow_relay_launcher.py", "fs_actions.py", "fs_exec.py",
    "fs_screen.py", "fs_mcp.py", "fs_common.py",
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
        remote = service._request("script_hash", _request_timeout=30.0)
        if isinstance(remote, dict) and remote.get("hash") == bundle["hash"]:
            logger.debug("Relay scripts up to date (hash=%s)", bundle["hash"])
            return
    except Exception:
        pass  # Relay doesn't support script_hash yet, push anyway
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
                pass

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
        self._service_id = config.get("_service_id", "")

        self._project_context: Optional[Dict] = None  # auto-fetched on relay connect
        self._relay_shells: List[str] = []  # available shells on the relay system
        self._relay_info: Dict[str, Any] = {}  # full registration info (platform, containerized, etc.)

        # Relay connection pool. Each entry is one WS to the same
        # service_id. The pool typically holds exactly one entry —
        # multi-relay setups link several RelayServices to a conversation
        # (core/relay_bindings) with one designated default; routing
        # between different relays happens at the agent-tool-config
        # level via that default + explicit `relay=` param, NOT inside
        # this pool. The pool grows beyond 1 only during reconnect
        # overlap: a dying WS that the server hasn't detected as dead
        # yet, plus the freshly reconnected one. We always send to the
        # most-recently-connected WS first and fall back to older ones
        # only if it fails. Round-robin would split traffic across the
        # dying and live WS unpredictably.
        self._relay_pool: List[Dict] = []  # [{"reader", "writer", "loop"}]
        self._relay_pool_lock = threading.Lock()

        # Pending requests: {request_id: (Event, result_holder)}
        self._pending: Dict[str, tuple] = {}
        self._pending_lock = threading.Lock()

        # Inverse-direction handler: relay-initiated FS ops scoped to the
        # owner's CLAUDE_SESSIONS_DIR slot. We seed `_user_id` from the
        # registry-supplied `_scope_id` if this is a user-scoped relay,
        # so the FUSE bridge serves requests even before any tool
        # handler has called set_user_id(). Without this seed, the
        # first FUSE callbacks (e.g. `ls /cc_sessions/` from a bare
        # relay terminal) would arrive with `_user_id == ""` and the
        # dispatcher would either return EACCES or block depending on
        # the path — exact symptom user saw.
        try:
            from core.service_registry import SCOPE_USER
            if config.get("_scope", "") == SCOPE_USER:
                self._user_id = str(config.get("_scope_id", "") or "")
            else:
                self._user_id = ""
        except Exception:
            self._user_id = ""
        self._server_fs = None
        self._server_fs_lock = threading.Lock()
        # Second inverse-direction handler: virtualized FUSE view of the
        # FileStore. Methods come in with the `ffs.` prefix and dispatch
        # to RelayFileStoreFs instead of RelayServerFs.
        self._filestore_fs = None
        self._filestore_fs_lock = threading.Lock()
        self._ctx_sync_lock = threading.Lock()
        self._ctx_sync_active = False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
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
        # Release any open fds in the inverse-direction handlers
        with self._server_fs_lock:
            if self._server_fs is not None:
                try:
                    self._server_fs.close()
                except Exception as e:
                    logger.debug('server_fs.close failed: %s', e, exc_info=True)
                self._server_fs = None
        with self._filestore_fs_lock:
            if self._filestore_fs is not None:
                try:
                    self._filestore_fs.close()
                except Exception as e:
                    logger.debug('filestore_fs.close failed: %s', e, exc_info=True)
                self._filestore_fs = None

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
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with contextlib.suppress(Exception):
                    try:
                        loop.run_until_complete(
                            loop.shutdown_default_executor(timeout=2.0))
                    except TypeError:
                        loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
        except Exception as e:
            logger.error('Relay WS handler error (%s): %s', remote, e, exc_info=True)

    async def _serve_relay_session(self, reader, writer, loop, remote):
        import asyncio
        service = self
        # One asyncio.Lock per WS connection — required because the
        # relay_request handler is now spawned as a task per inbound
        # frame (so a slow ffs.read doesn't block the next FUSE
        # callback in line), and concurrent tasks calling
        # writer.write()/drain() would interleave WS frames. The lock
        # is passed alongside the writer rather than attached to it
        # because StreamWriter implementations (e.g. _SockWriter on
        # Windows) can have __slots__ and refuse new attributes.
        send_lock = asyncio.Lock()
        relay_tasks = set()
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode('utf-8'))
            if reg.get('type') != 'register':
                return
            relay_token = reg.get('token', '')
            if not relay_token or relay_token != service.config.get('token', ''):
                async with send_lock:
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
            async with send_lock:
                await _ws_send_frame(writer, json.dumps({
                    'type': 'registered', 'relay_id': relay_id}).encode())
            service._set_relay(reader, writer, loop, send_lock, relay_tasks)
            self._spawn_ctx_sync(reg_info, relay_id)
            await self._relay_main_loop(reader, writer, service, send_lock, relay_tasks)
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
            if relay_tasks:
                tasks = list(relay_tasks)
                for task in tasks:
                    task.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=2.0)
                relay_tasks.clear()
            try:
                writer.close()
            except Exception as e:
                logger.debug('writer.close failed: %s', e, exc_info=True)
            logger.info('Relay disconnected: %s', remote)

    def _spawn_ctx_sync(self, reg_info, relay_id):
        service = self
        with self._ctx_sync_lock:
            if self._ctx_sync_active:
                logger.debug('Relay context sync already running for %s', relay_id)
                return
            self._ctx_sync_active = True

        def _fetch_ctx_and_sync():
            try:
                try:
                    ctx = service._request(
                        'project_context', '.', _request_timeout=30.0)
                    service._project_context = ctx
                    logger.info('Project context loaded for %s: %s',
                                 relay_id, ctx.get('project_types', []))
                except Exception as e:
                    logger.debug('Failed to load project context: %s', e, exc_info=True)
                try:
                    _sync_relay_scripts(service, reg_info)
                except Exception as e:
                    logger.debug('Relay script sync failed: %s', e, exc_info=True)
            finally:
                with service._ctx_sync_lock:
                    service._ctx_sync_active = False

        threading.Thread(target=_fetch_ctx_and_sync, daemon=True,
                         name=f'relay-ctx-{relay_id}').start()

    async def _relay_main_loop(self, reader, writer, service, send_lock, relay_tasks):
        import asyncio
        KEEPALIVE = 120
        while True:
            try:
                opcode, payload = await asyncio.wait_for(
                    _ws_recv_frame(reader), timeout=KEEPALIVE)
            except asyncio.TimeoutError:
                reader_exception = None
                exception_getter = getattr(reader, "exception", None)
                if callable(exception_getter):
                    with contextlib.suppress(Exception):
                        reader_exception = exception_getter()
                if reader_exception is not None:
                    raise reader_exception
                async with send_lock:
                    await _ws_send_frame(
                        writer, json.dumps({'type': 'ping'}).encode())
                continue
            if opcode == 0x08:
                break
            if opcode == 0x09:
                async with send_lock:
                    await _ws_send_frame(writer, payload, opcode=0x0A)
                continue
            if opcode != 0x01:
                continue
            msg = json.loads(payload.decode('utf-8'))
            await self._dispatch_relay_msg(msg, writer, service, send_lock, relay_tasks)

    async def _dispatch_relay_msg(self, msg, writer, service, send_lock, relay_tasks):
        import asyncio
        mtype = msg.get('type')
        if mtype == 'relay_request':
            # Fire-and-forget: each FUSE callback (sfs.read, ffs.getattr,
            # …) runs on the executor without blocking the WS receiver.
            # Otherwise CC reading 8 MB through 1 MB FUSE chunks holds
            # the main loop for the full sequence and every other FUSE
            # op (and any concurrent terminal/exec frame) queues up
            # behind it. The send back is serialized via send_lock so
            # concurrent tasks can't interleave frames.
            task = asyncio.create_task(
                service._handle_relay_request(msg, writer, send_lock))
            relay_tasks.add(task)
            task.add_done_callback(relay_tasks.discard)
            return
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
            async with send_lock:
                await _ws_send_frame(
                    writer, json.dumps({'type': 'pong'}).encode())

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _get_server_fs(self):
        """Lazy-instantiate the inverse-direction FS handler.

        Returns None if no user_id is set yet — callers must reject the
        request rather than fall back to an unscoped handler.
        """
        if not self._user_id:
            return None
        with self._server_fs_lock:
            if self._server_fs is None:
                from services.relay_server_fs import RelayServerFs
                self._server_fs = RelayServerFs(self._user_id)
            return self._server_fs

    def _get_filestore_fs(self):
        """Lazy-instantiate the FileStore FUSE handler (ffs.* methods)."""
        if not self._user_id:
            return None
        with self._filestore_fs_lock:
            if self._filestore_fs is None:
                from services.relay_filestore_fs import RelayFileStoreFs
                self._filestore_fs = RelayFileStoreFs(self._user_id)
            return self._filestore_fs

    async def _handle_relay_request(self, msg, writer, send_lock):
        """Service a relay→server FS op (the inverse direction).

        The relay's FUSE proxy forwards each FUSE callback as a
        `relay_request` over the existing tunnel. The method prefix
        selects the handler:
          - `sfs.*` → cc-sessions slot (CLAUDE_SESSIONS_DIR/<user>/)
          - `ffs.*` → virtualized FileStore view
        Anything else returns ENOSYS.
        """
        import asyncio
        import time as _time
        request_id = msg.get('request_id', '')
        method = msg.get('method', '')
        args = msg.get('args', {}) or {}
        _t0 = _time.monotonic()
        logger.debug("[server-fs] %s ENTER rid=%s args=%s",
                     method, request_id[:8], _short_args(args))
        if method.startswith('ffs.'):
            fs = self._get_filestore_fs()
        elif method.startswith('sfs.'):
            fs = self._get_server_fs()
        else:
            fs = None
        if fs is None:
            if not self._user_id:
                reply = {'error': 'EACCES', 'errno': 13,
                         'message': 'relay has no owner user_id'}
            else:
                reply = {'error': 'ENOSYS', 'errno': 38,
                         'message': f'unknown method prefix: {method!r}'}
        else:
            # FS ops are sync — run on the loop's default executor so we
            # don't block other relay traffic on a slow disk. Hard 10s
            # cap: a single os.listdir/os.read on a hung WSL UNC path
            # must NOT freeze the FUSE callback indefinitely. After the
            # cap we send EIO; the kernel surfaces "Input/output error"
            # to the caller instead of blocking the whole shell.
            loop = asyncio.get_event_loop()
            try:
                reply = await asyncio.wait_for(
                    loop.run_in_executor(None, fs.handle, method, args),
                    timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[server-fs] %s TIMEOUT rid=%s after 10s — returning EIO",
                    method, request_id[:8])
                reply = {'error': 'EIO', 'errno': 5,
                         'message': f'{method} timed out after 10s'}
        _dt = int((_time.monotonic() - _t0) * 1000)
        if 'error' in reply:
            logger.debug("[server-fs] %s EXIT rid=%s dt=%dms err=%s",
                         method, request_id[:8], _dt, reply.get('error'))
        else:
            logger.debug("[server-fs] %s EXIT rid=%s dt=%dms ok",
                         method, request_id[:8], _dt)
        envelope = {'type': 'relay_response', 'request_id': request_id, **reply}
        try:
            # Serialize so concurrent _handle_relay_request tasks (now
            # spawned via create_task) can't interleave WS frames.
            async with send_lock:
                await _ws_send_frame(writer, json.dumps(envelope).encode())
        except Exception as e:
            logger.warning('[server-fs] failed to send response for %s: %s',
                           request_id, e)

    # ── Relay connection management ──

    def _set_relay(self, reader, writer, loop, send_lock, relay_tasks=None):
        """Add a relay connection to the pool."""
        with self._relay_pool_lock:
            self._relay_pool.append({"reader": reader, "writer": writer,
                                      "loop": loop, "send_lock": send_lock,
                                      "tasks": relay_tasks})
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
        removed = []
        with self._relay_pool_lock:
            if reader:
                kept = []
                for conn in self._relay_pool:
                    if conn["reader"] is reader:
                        removed.append(conn)
                    else:
                        kept.append(conn)
                self._relay_pool = kept
            else:
                removed = list(self._relay_pool)
                self._relay_pool.clear()
            alive = len(self._relay_pool)
        removed_readers = {conn.get("reader") for conn in removed}
        for conn in removed:
            for task in list(conn.get("tasks") or ()):
                task.cancel()
        with self._pending_lock:
            pending_items = list(self._pending.items())
            for rid, (evt, holder) in pending_items:
                pending_reader = holder.get("_relay_reader")
                if alive == 0 or pending_reader in removed_readers:
                    self._pending.pop(rid, None)
                    holder["error"] = "Relay disconnected"
                    evt.set()
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
        """Cancel a pending request — unblock the waiting thread AND tell
        the relay to kill the underlying subprocess.

        Two-step:
          1. Push a `cancel_request` envelope to the relay so it can
             terminate the Popen registered for this request_id (see
             pawflow_relay.proc_registry).
          2. Pop the local pending entry and unblock the waiter with
             '[Interrupted by user]'. The thread that called `_request`
             returns immediately even if the relay's kill takes a moment.
        """
        if request_id:
            self._send_cancel_request_to_relay(request_id)
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry:
            evt, holder = entry
            holder["error"] = "[Interrupted by user]"
            evt.set()

    def _send_cancel_request_to_relay(self, request_id: str):
        """Broadcast a cancel_request envelope to every connected relay.

        Best-effort and non-blocking: send timeouts are absorbed silently
        because a missed cancel only means the action thread will exit
        naturally when its subprocess does. We log the failure for
        forensics but never raise — cancel_pending must always succeed
        in unblocking the local waiter.
        """
        with self._relay_pool_lock:
            pool = self._relay_pool[:]
        if not pool:
            return
        payload = json.dumps({
            "type": "cancel_request",
            "request_id": request_id,
        }).encode("utf-8")
        for conn in pool:
            writer, loop = conn["writer"], conn["loop"]
            send_lock = conn.get("send_lock")
            async def _send(w=writer, lk=send_lock):
                if lk is not None:
                    async with lk:
                        await _ws_send_frame(w, payload)
                else:
                    await _ws_send_frame(w, payload)
            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=2)
            except Exception as e:
                logger.warning(
                    "[%s] cancel_request push failed for %s: %s",
                    self._service_id, request_id, e)

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

    def _send_to_pool(self, pool: List[Dict], payload: bytes,
                      request_id: str = ""):
        """Send `payload` over the WS pool, most-recently-connected first.

        Returns None on success, the last exception on total failure.
        Round-robin would be incoherent here — the pool only ever holds
        more than one entry during a reconnect overlap (a dying old WS
        plus the freshly attached new one), so splitting traffic across
        the two would route some requests to the dying socket. Multi-
        relay is handled at the conversation level via core/relay_bindings
        (link_relay + set_default_relay), not inside this pool.
        """
        last_err = None
        for conn in reversed(pool):
            writer, loop = conn["writer"], conn["loop"]
            send_lock = conn.get("send_lock")

            async def _send(w=writer, lk=send_lock):
                if lk is not None:
                    async with lk:
                        await _ws_send_frame(w, payload)
                else:
                    await _ws_send_frame(w, payload)

            if request_id:
                with self._pending_lock:
                    entry = self._pending.get(request_id)
                    if not entry:
                        return Exception("Relay disconnected")
                    entry[1]["_relay_reader"] = conn["reader"]
            try:
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=10)
                return None
            except Exception as e:
                last_err = e
                continue
        return last_err

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a command to the relay and wait for the result (sync).

        Uses the pool's most-recently-connected entry first, falling
        back to older entries only on send failure.
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
        wait_timeout = kwargs.pop("_request_timeout", None)

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

        last_err = self._send_to_pool(pool, payload, request_id=request_id)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Register a kill hook so a FORCE STOP at the tool-relay layer
        # propagates all the way down to the relay's subprocess: the
        # hook calls cancel_pending(rid) which both unblocks our local
        # evt.wait() below AND pushes a cancel_request envelope so the
        # relay terminates its Popen.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            pass

        if not evt.wait(timeout=wait_timeout):
            self.cancel_pending(request_id)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

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

        last_err = self._send_to_pool(pool, payload, request_id=request_id)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Same kill-hook registration as `_request` — see comment there.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            pass

        evt.wait(timeout=timeout)

        if not evt.is_set():
            self.cancel_pending(request_id)
            raise Exception("Timeout waiting for relay response")

        with self._pending_lock:
            self._pending.pop(request_id, None)
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

        last_err = self._send_to_pool(pool, payload, request_id=request_id)

        if last_err:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {last_err}")

        # Same kill-hook registration as `_request` — see comment there.
        try:
            from services.tool_relay_service import register_kill_hook
            register_kill_hook(lambda rid=request_id: self.cancel_pending(rid))
        except Exception:
            pass

        # Wait for relay response — no limit unless timeout explicitly given
        _wait_timeout = kwargs.get("timeout")
        if not evt.wait(timeout=_wait_timeout):
            self.cancel_pending(request_id)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    # ── Filesystem interface ──

    def list_dir(self, path: str = ".", local: bool = False,
                 recursive: bool = False, max_entries: int = 0):
        from core.filesystem import FilesystemEntry
        data = self._request(
            "list_dir", path, local=local,
            recursive=bool(recursive), max_entries=int(max_entries or 0))
        return [FilesystemEntry(**e) if isinstance(e, dict) else e for e in data]

    def read_file(self, path: str, local: bool = False) -> bytes:
        try:
            data = self._request("read_file", path, local=local)
            if isinstance(data, dict) and "content" in data:
                return base64.b64decode(data["content"])
            return data.encode("utf-8") if isinstance(data, str) else data
        except Exception as e:
            if "too large" in str(e).lower():
                return self._read_chunked(path, local=local)
            raise

    def _read_chunked(self, path: str, local: bool = False) -> bytes:
        """Read a large file in chunks via the relay."""
        first = self._request("read_file_chunked", path, local=local)
        chunks = [base64.b64decode(first["data"])]
        total_chunks = first.get("total_chunks", 1)
        chunk_size = first.get("chunk_size", 1024 * 1024)
        for i in range(1, total_chunks):
            chunk = self._request("read_chunk", path, index=i, chunk_size=chunk_size,
                                  local=local)
            chunks.append(base64.b64decode(chunk["data"]))
            if chunk.get("done"):
                break
        return b"".join(chunks)

    def write_file(self, path: str, content: bytes, local: bool = False):
        if len(content) > 50 * 1024 * 1024:  # > 50MB → chunked
            self._write_chunked(path, content, local=local)
        else:
            self._request("write_file", path,
                           content=base64.b64encode(content).decode("ascii"),
                           base64=True, local=local)

    def _write_chunked(self, path: str, content: bytes, local: bool = False):
        """Write a large file in chunks via the relay."""
        chunk_size = 1024 * 1024  # 1MB
        total = len(content)
        for i in range(0, total, chunk_size):
            chunk = content[i:i + chunk_size]
            done = (i + chunk_size) >= total
            self._request("write_file_chunked", path,
                           index=i // chunk_size,
                           data=base64.b64encode(chunk).decode("ascii"),
                           done=done, local=local)

    def delete_file(self, path: str, local: bool = False):
        self._request("delete_file", path, local=local)

    def mkdir(self, path: str, local: bool = False):
        self._request("mkdir", path, local=local)

    def stat(self, path: str, local: bool = False):
        from core.filesystem import FilesystemEntry
        import dataclasses
        data = self._request("stat", path, local=local)
        if isinstance(data, dict):
            # Filter to known fields only (relay may return extra like 'created')
            valid = {f.name for f in dataclasses.fields(FilesystemEntry)}
            return FilesystemEntry(**{k: v for k, v in data.items() if k in valid})
        return data

    def exists(self, path: str, local: bool = False) -> bool:
        data = self._request("exists", path, local=local)
        return data.get("exists", False) if isinstance(data, dict) else bool(data)

    def search(self, path: str, pattern: str, recursive: bool = True,
               local: bool = False, limit: int = 500):
        return self._request("search", path, pattern=pattern,
                             recursive=recursive, local=local, limit=limit)

    def grep(self, path: str, regex: str, recursive: bool = True, **kwargs):
        return self._request("grep", path, regex=regex, recursive=recursive, **kwargs)

    def find_replace(self, path: str, pattern: str, replacement: str,
                     local: bool = False):
        return self._request("find_replace", path, pattern=pattern,
                             replacement=replacement, local=local)

    def edit(self, path: str, old_string: str, new_string: str,
             replace_all: bool = False, local: bool = False):
        return self._request("edit", path, old_string=old_string,
                              new_string=new_string, replace_all=replace_all,
                              local=local)

    def batch_edit(self, edits: list, local: bool = False):
        return self._request("batch_edit", ".", edits=edits, local=local)

    def apply_patch(self, patch: str, local: bool = False):
        return self._request("apply_patch", ".", patch=patch, local=local)

    def edit_notebook(self, path: str, cell_index: int, new_source: str = "",
                      cell_type: str = "", operation: str = "edit",
                      local: bool = False):
        return self._request("edit_notebook", path, cell_index=cell_index,
                              new_source=new_source, cell_type=cell_type,
                              operation=operation, local=local)

    def exec(self, path: str, command: str, timeout=None, shell: str = "", env: dict = None,
             local: bool = False):
        kwargs = {"command": command}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if shell:
            kwargs["shell"] = shell
        if env:
            kwargs["env"] = env
        if local:
            kwargs["local"] = True
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
