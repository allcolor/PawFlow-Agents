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

logger = logging.getLogger(__name__)


# ── Shared WS Listener (singleton per port) ──────────────────────

class FilesystemWSListener:
    """Shared WebSocket listener — one per port, multiple filesystem services."""

    _instances: Dict[int, "FilesystemWSListener"] = {}
    _lock = threading.Lock()

    @classmethod
    def get_or_create(cls, port: int) -> "FilesystemWSListener":
        with cls._lock:
            if port not in cls._instances:
                inst = cls(port)
                cls._instances[port] = inst
            return cls._instances[port]

    def __init__(self, port: int):
        self._port = port
        self._routes: Dict[str, "FilesystemService"] = {}  # path → service
        self._routes_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._ref_count = 0

    def register_route(self, path: str, service: "FilesystemService"):
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
            logger.info("Relay connected: %s (path=%s, addr=%s)", relay_id, req_path, tag)

            # Confirm registration
            await self._ws_send(writer, json.dumps({
                "type": "registered", "relay_id": relay_id,
            }).encode())

            # Store relay connection on the service
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
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(
                        self._ws_recv(reader), timeout=120)
                except asyncio.TimeoutError:
                    # Send ping
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
                elif msg.get("type") == "ping":
                    await self._ws_send(writer, json.dumps({"type": "pong"}).encode())

        except Exception as e:
            logger.debug("Relay connection error (%s): %s", tag, e)
        finally:
            if service:
                service._clear_relay()
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

class FilesystemService:
    """Filesystem service backed by a reverse WebSocket relay."""

    TYPE = "filesystem"
    VERSION = "2.0.0"
    NAME = "Filesystem (Relay)"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._port = int(config.get("port", 9091))
        self._path = config.get("path", "/ws/relay")
        self._token = config.get("token", "")
        self._mode = config.get("mode", "readwrite")
        self._service_id = config.get("_service_id", "")
        self._connection = None

        self._project_context: Optional[Dict] = None  # auto-fetched on relay connect

        # Relay state
        self._relay_reader: Optional[asyncio.StreamReader] = None
        self._relay_writer: Optional[asyncio.StreamWriter] = None
        self._relay_loop: Optional[asyncio.AbstractEventLoop] = None
        self._relay_lock = threading.Lock()

        # Pending requests: {request_id: (Event, result_holder)}
        self._pending: Dict[str, tuple] = {}
        self._pending_lock = threading.Lock()

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
        listener = FilesystemWSListener.get_or_create(self._port)
        listener.register_route(self._path, self)
        self._connection = listener
        logger.info("FilesystemService '%s' listening on port %d path %s",
                     self._service_id, self._port, self._path)

    def disconnect(self):
        if self._connection:
            self._connection.unregister_route(self._path)
            self._connection = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    # ── Relay connection management ──

    def _set_relay(self, reader, writer, loop):
        with self._relay_lock:
            self._relay_reader = reader
            self._relay_writer = writer
            self._relay_loop = loop

    def _clear_relay(self):
        with self._relay_lock:
            self._relay_reader = None
            self._relay_writer = None
            self._relay_loop = None
        # Cancel pending requests
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

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a command to the relay and wait for the result (sync)."""
        with self._relay_lock:
            writer = self._relay_writer
            loop = self._relay_loop
        if not writer or not loop:
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

        # Send command via WS (async→sync bridge)
        payload = json.dumps({
            "type": "command",
            "request_id": request_id,
            "action": action,
            "path": path,
            **kwargs,
        }).encode("utf-8")

        async def _send():
            listener = self._connection
            if listener:
                await listener._ws_send(writer, payload)

        try:
            asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=5)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Failed to send to relay: {e}")

        # Wait for result
        if not evt.wait(timeout=60):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise Exception(f"Relay timeout for {action} on {self._service_id}")

        if "error" in holder:
            raise Exception(holder["error"])

        data = holder.get("data")
        # Check for relay-level errors
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
        data = self._request("stat", path)
        return FilesystemEntry(**data) if isinstance(data, dict) else data

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

    def exec(self, path: str, command: str, timeout: int = 30):
        return self._request("exec", path, command=command, timeout=timeout)

    # ── Git ──

    def git_status(self, path="."): return self._request("git_status", path)
    def git_log(self, path=".", count=10): return self._request("git_log", path, count=count)
    def git_diff(self, path=".", ref=""): return self._request("git_diff", path, ref=ref)
    def git_commit(self, path=".", message=""): return self._request("git_commit", path, message=message)
    def git_pull(self, path="."): return self._request("git_pull", path)
    def git_push(self, path="."): return self._request("git_push", path)
    def git_checkout(self, path=".", ref=""): return self._request("git_checkout", path, ref=ref)


# Register with ServiceFactory
ServiceFactory.register(FilesystemService)
