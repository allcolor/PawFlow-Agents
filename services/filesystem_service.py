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


# ── Shared WS Listener (singleton per port) ──────────────────────

class WSListener:
    """Shared WebSocket listener — one per port, multiple relay/tool services."""

    _instances: Dict[int, "WSListener"] = {}
    _lock = threading.Lock()

    @classmethod
    def get_or_create(cls, port: int) -> "WSListener":
        with cls._lock:
            if port not in cls._instances:
                inst = cls(port)
                cls._instances[port] = inst
            return cls._instances[port]

    def __init__(self, port: int):
        self._port = port
        self._routes: Dict[str, "RelayService"] = {}  # path → service
        self._routes_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._ref_count = 0

    def register_route(self, path: str, service: "RelayService"):
        with self._routes_lock:
            self._routes[path] = service
            self._ref_count += 1
        if not self._thread or not self._thread.is_alive():
            self._start()

    def unregister_route(self, path: str):
        with self._routes_lock:
            self._routes.pop(path, None)
            self._ref_count = max(0, self._ref_count - 1)
        # Cleanup temp cert files
        for attr in ("_cert_file", "_key_file"):
            f = getattr(self, attr, None)
            if f and hasattr(f, "name"):
                try:
                    os.unlink(f.name)
                except OSError:
                    pass

    def _start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"fs-ws-listener-{self._port}",
        )
        self._thread.start()
        # Wait for server to be ready
        for _ in range(50):
            if self._server:
                break
            time.sleep(0.1)

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    def _create_ssl_context(self):
        """Generate ephemeral self-signed cert for TLS."""
        import ssl
        import tempfile
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            import datetime as _dt

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "pawflow-relay"),
            ])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(_dt.datetime.now(_dt.timezone.utc))
                .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1))
                .sign(key, hashes.SHA256())
            )
            # Write to temp files
            self._cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            self._cert_file.write(cert.public_bytes(serialization.Encoding.PEM))
            self._cert_file.close()
            self._key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            self._key_file.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
            self._key_file.close()

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self._cert_file.name, self._key_file.name)
            logger.info("TLS enabled for filesystem listener (ephemeral cert)")
            return ctx
        except ImportError:
            logger.warning("cryptography package not installed — TLS disabled for filesystem relay")
            return None

    async def _serve(self):
        ssl_ctx = self._create_ssl_context()
        self._server = await asyncio.start_server(
            self._handle_connection, "0.0.0.0", self._port,
            ssl=ssl_ctx,
        )
        proto = "wss" if ssl_ctx else "ws"
        logger.info("Filesystem WS listener started on port %d (%s)", self._port, proto)
        async with self._server:
            await self._server.serve_forever()

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter):
        """Handle incoming relay connection — WS upgrade + command loop."""
        addr = writer.get_extra_info("peername")
        tag = f"{addr[0]}:{addr[1]}" if addr else "?"

        try:
            # Read HTTP upgrade request
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
                if not chunk:
                    return
                request += chunk

            # Parse request path
            first_line = request.split(b"\r\n")[0].decode("latin-1", errors="replace")
            parts = first_line.split()
            req_path = parts[1] if len(parts) >= 2 else "/"

            # Find service for this path
            with self._routes_lock:
                service = self._routes.get(req_path)
            if not service:
                writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            # WS upgrade response
            ws_key = b""
            for line in request.split(b"\r\n"):
                if line.lower().startswith(b"sec-websocket-key:"):
                    ws_key = line.split(b":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1(ws_key + b"258EAFA5-E914-47DA-95CA-5AB5ADF7254B").digest()
            ).decode()
            writer.write(
                f"HTTP/1.1 101 Switching Protocols\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                f"\r\n".encode("latin-1")
            )
            await writer.drain()

            # Read first message (registration)
            opcode, payload = await self._ws_recv(reader)
            if opcode != 0x01:
                writer.close()
                return
            reg = json.loads(payload.decode("utf-8"))
            if reg.get("type") != "register":
                writer.close()
                return

            # Validate token
            relay_token = reg.get("token", "")
            if not relay_token or relay_token != service._token:
                await self._ws_send(writer, json.dumps(
                    {"type": "error", "message": "Token mismatch"}
                ).encode())
                writer.close()
                return

            relay_id = reg.get("relay_id", "")
            _reg_info = reg.get("info", {})
            logger.info("Relay connected: %s (path=%s, addr=%s)", relay_id, req_path, tag)

            # Store relay metadata (shells, platform, containerized, etc.)
            if _reg_info.get("shells") and hasattr(service, '_relay_shells'):
                service._relay_shells = _reg_info["shells"]
            if service and _reg_info:
                service._relay_info = _reg_info

            # Confirm registration
            await self._ws_send(writer, json.dumps({
                "type": "registered", "relay_id": relay_id,
            }).encode())

            # Tool relay vs filesystem relay
            if hasattr(service, 'handle_tool_request'):
                _user_id = reg.get("user_id", "")
                _conv_id = reg.get("conversation_id", "")
                _agent_name = reg.get("agent_name", "")
                logger.info("Tool relay connected: user=%s conv=%s agent=%s addr=%s",
                             _user_id, _conv_id, _agent_name, tag)
                _KEEPALIVE_INTERVAL = 120  # seconds between pings if no data
                while True:
                    try:
                        opcode, payload = await asyncio.wait_for(
                            self._ws_recv(reader), timeout=_KEEPALIVE_INTERVAL)
                    except asyncio.TimeoutError:
                        await self._ws_send(writer, json.dumps({"type": "ping"}).encode())
                        continue
                    if opcode == 0x08:
                        break
                    if opcode == 0x09:
                        await self._ws_send(writer, payload, opcode=0x0A)
                        continue
                    if opcode != 0x01:
                        continue
                    msg = json.loads(payload.decode("utf-8"))
                    if msg.get("type") == "ping":
                        await self._ws_send(writer, json.dumps({"type": "pong"}).encode())
                        continue
                    if msg.get("type") != "request":
                        continue
                    import threading as _th
                    def _exec(m=msg):
                        try:
                            resp = service.handle_tool_request(
                                m, _user_id, _conv_id, _agent_name)
                        except Exception as e:
                            resp = {"type": "error",
                                    "request_id": m.get("request_id", ""),
                                    "error": str(e)}
                        asyncio.run_coroutine_threadsafe(
                            self._ws_send(writer, json.dumps(resp).encode("utf-8")),
                            self._loop)
                    _th.Thread(target=_exec, daemon=True,
                               name=f"tool-relay-{msg.get('method', '?')}").start()

            # Filesystem relay
            service._set_relay(reader, writer, self._loop)

            # Auto-fetch project context in background
            try:
                import threading as _th
                def _fetch_ctx():
                    try:
                        ctx = service._request("project_context", ".")
                        service._project_context = ctx
                        logger.info("Project context loaded for '%s': %s",
                                     relay_id, ctx.get("project_types", []))
                    except Exception as e:
                        logger.debug("Failed to load project context: %s", e)
                _th.Thread(target=_fetch_ctx, daemon=True).start()
            except Exception:
                pass

            # Main loop: read results from relay
            _KEEPALIVE_INTERVAL = 120  # seconds between pings if no data
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(
                        self._ws_recv(reader), timeout=_KEEPALIVE_INTERVAL)
                except asyncio.TimeoutError:
                    await self._ws_send(writer, json.dumps({"type": "ping"}).encode())
                    continue

                if opcode == 0x08:  # close
                    break
                if opcode == 0x09:  # ping
                    await self._ws_send(writer, payload, opcode=0x0A)
                    continue
                if opcode != 0x01:  # text
                    continue

                msg = json.loads(payload.decode("utf-8"))
                if msg.get("type") == "result" or msg.get("type") == "error":
                    service._resolve_pending(msg)
                elif msg.get("type") == "progress":
                    # Intermediate progress from long-running commands
                    from core.relay_manager import RelayConnectionManager
                    RelayConnectionManager.instance().dispatch_progress(
                        user_id, relay_id, msg.get("request_id", ""),
                        msg.get("data", {}))
                elif msg.get("type") == "exec_output":
                    service._dispatch_exec_output(msg)
                elif msg.get("type") == "ping":
                    await self._ws_send(writer, json.dumps({"type": "pong"}).encode())

        except Exception as e:
            _err_str = str(e)
            if "0 bytes read" in _err_str:
                logger.debug("Relay disconnected normally (%s)", tag)
            else:
                logger.error("Relay connection error (%s): %s", tag, e)
        finally:
            if service:
                service._clear_relay(reader=reader)
            try:
                writer.close()
            except Exception:
                pass
            logger.info("Relay disconnected: %s", tag)

    # ── WS frame helpers (minimal, no deps) ──

    async def _ws_recv(self, reader: asyncio.StreamReader):
        hdr = await reader.readexactly(2)
        opcode = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]
        if masked:
            mask = await reader.readexactly(4)
            data = await reader.readexactly(length)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        else:
            payload = await reader.readexactly(length)
        return opcode, payload

    async def _ws_send(self, writer: asyncio.StreamWriter, data: bytes, opcode=0x01):
        await self._ws_send_raw(writer, data, opcode)

    @staticmethod
    async def _ws_send_raw(writer: asyncio.StreamWriter, data: bytes, opcode=0x01):
        frame = bytes([0x80 | opcode])
        length = len(data)
        if length < 126:
            frame += bytes([length])
        elif length < 65536:
            frame += bytes([126]) + struct.pack("!H", length)
        else:
            frame += bytes([127]) + struct.pack("!Q", length)
        frame += data
        writer.write(frame)
        await writer.drain()


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
        self._path = config.get("path", "/ws/relay")
        self._token = config.get("token", "")
        self._mode = config.get("mode", "readwrite")
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
        """Register route on the shared listener."""
        listener = WSListener.get_or_create(self._port)
        listener.register_route(self._path, self)
        self._connection = listener
        self._initialized = True
        logger.info("RelayService '%s' listening on port %d path %s",
                     self._service_id, self._port, self._path)

    def is_connected(self) -> bool:
        """A relay service is connected when a relay client is in the pool."""
        with self._relay_pool_lock:
            return len(self._relay_pool) > 0

    def disconnect(self):
        if self._connection:
            self._connection.unregister_route(self._path)
            self._connection = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    # ── Relay connection management ──

    def _set_relay(self, reader, writer, loop):
        """Add a relay connection to the pool."""
        with self._relay_pool_lock:
            self._relay_pool.append({"reader": reader, "writer": writer, "loop": loop})
            count = len(self._relay_pool)
        logger.info("Relay pool: %d connection(s) for '%s'", count, self._service_id)

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
                f"--server ws://<host>:{self._port}{self._path} "
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
                listener = self._connection
                if listener:
                    await listener._ws_send(w, payload)

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
                listener = self._connection
                if listener:
                    await listener._ws_send(w, payload)

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

    def grep(self, path: str, regex: str, recursive: bool = True):
        return self._request("grep", path, regex=regex, recursive=recursive)

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

    def exec(self, path: str, command: str, timeout=None, shell: str = ""):
        kwargs = {"command": command}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if shell:
            kwargs["shell"] = shell
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
