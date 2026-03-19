"""WebSocket Filesystem Service — WebSocket relay backend for user's filesystem.

Connects to a openpaw_fs_relay_ws.py script running on the user's machine,
providing secure filesystem access through the FilesystemBackend interface.
Persistent connection for lower latency on frequent operations.

Config:
    host: str       — Relay host (default: "localhost")
    port: int       — Relay port (default: 9877)
    secret: str     — Shared secret for authentication
    timeout: int    — Request timeout in seconds (default: 30)
    mode: str       — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str — Comma-separated allowed path prefixes (default: "")
    denied_paths: str  — Comma-separated denied path prefixes (default: "")
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import threading
from typing import Any, Dict, List, Optional

from core import ServiceFactory, ServiceError
from core.base_service import BaseService
from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem,
)

logger = logging.getLogger(__name__)


class RelayWebSocketBackend(FilesystemBackend):
    """Client that talks to openpaw_fs_relay_ws.py over WebSocket."""

    def __init__(self, host: str, port: int, secret: str, timeout: int = 30):
        self._host = host
        self._port = port
        self._secret = secret
        self._timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = threading.Lock()
        self._connected = False

    def _ensure_connected(self):
        """Ensure WebSocket connection is established (blocking)."""
        if self._connected and self._writer and not self._writer.is_closing():
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — run in a new thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self._connect_sync)
                    future.result(timeout=self._timeout)
            else:
                loop.run_until_complete(self._connect_async())
        except RuntimeError:
            # No event loop — create one
            self._connect_sync()

    def _connect_sync(self):
        """Connect synchronously by creating a temporary event loop."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._connect_async())
        finally:
            loop.close()

    async def _connect_async(self):
        """Perform WebSocket handshake."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=self._timeout,
        )

        # Generate WebSocket key
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")

        # Send upgrade request
        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._writer.write(request.encode("latin-1"))
        await self._writer.drain()

        # Read response
        response = await asyncio.wait_for(
            self._reader.readuntil(b"\r\n\r\n"),
            timeout=self._timeout,
        )

        if b"101" not in response:
            raise ServiceError(f"WebSocket upgrade failed: {response[:100]}")

        self._connected = True

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a request over WebSocket and return the data field."""
        with self._lock:
            self._ensure_connected()

            payload = {"action": action, "path": path, "secret": self._secret}
            payload.update(kwargs)
            text = json.dumps(payload)

            try:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        self._send_and_receive(text)
                    )
                finally:
                    loop.close()
            except Exception as e:
                self._connected = False
                raise ServiceError(f"WebSocket request failed: {e}")

            if not result.get("ok"):
                raise ServiceError(result.get("error", "Unknown relay error"))
            return result.get("data")

    async def _send_and_receive(self, text: str) -> dict:
        """Send a text frame and read the response."""
        # Send text frame
        payload = text.encode("utf-8")
        frame = self._make_frame(0x1, payload)
        self._writer.write(frame)
        await self._writer.drain()

        # Read response frame
        hdr = await asyncio.wait_for(
            self._reader.readexactly(2), timeout=self._timeout)
        opcode = hdr[0] & 0x0F
        length = hdr[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H",
                await self._reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q",
                await self._reader.readexactly(8))[0]

        data = await asyncio.wait_for(
            self._reader.readexactly(length), timeout=self._timeout)

        if opcode == 0x8:  # Close frame
            self._connected = False
            raise ServiceError("WebSocket connection closed by relay")

        return json.loads(data.decode("utf-8"))

    @staticmethod
    def _make_frame(opcode: int, payload: bytes) -> bytes:
        """Build a masked client frame (RFC 6455 requires client masking)."""
        frame = bytearray()
        frame.append(0x80 | opcode)

        length = len(payload)
        if length <= 125:
            frame.append(0x80 | length)  # masked
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))

        # Masking key
        mask = os.urandom(4)
        frame.extend(mask)
        # Mask payload
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask[i & 3]
        frame.extend(masked)
        return bytes(frame)

    # ── Basic operations ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        data = self._request("list_dir", path)
        return [
            FilesystemEntry(
                name=e["name"], kind=e.get("kind", "file"),
                size=e.get("size", 0), modified=e.get("modified", ""),
            )
            for e in data
        ]

    def read_file(self, path: str) -> bytes:
        data = self._request("read_file", path)
        return base64.b64decode(data["content"])

    def write_file(self, path: str, content: bytes) -> None:
        self._request("write_file", path,
                       content=base64.b64encode(content).decode("ascii"))

    def delete_file(self, path: str) -> None:
        self._request("delete_file", path)

    def mkdir(self, path: str) -> None:
        self._request("mkdir", path)

    def stat(self, path: str) -> FilesystemEntry:
        data = self._request("stat", path)
        return FilesystemEntry(
            name=data.get("name", ""), kind=data.get("kind", "file"),
            size=data.get("size", 0), modified=data.get("modified", ""),
        )

    def exists(self, path: str) -> bool:
        return self._request("exists", path).get("exists", False)

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        return self._request("search", path, pattern=pattern, recursive=recursive)

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        return self._request("grep", path, regex=regex, recursive=recursive)

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        return self._request("find_replace", path, pattern=pattern, replacement=replacement)

    # ── Git ──

    @property
    def supports_git(self) -> bool:
        return True

    def git_status(self, path="."): return self._request("git_status", path)
    def git_log(self, path=".", count=10): return self._request("git_log", path, count=count)
    def git_diff(self, path=".", ref=""): return self._request("git_diff", path, ref=ref)
    def git_commit(self, path=".", message=""): return self._request("git_commit", path, message=message)
    def git_pull(self, path="."): return self._request("git_pull", path)
    def git_push(self, path="."): return self._request("git_push", path)
    def git_checkout(self, path=".", ref=""): return self._request("git_checkout", path, ref=ref)

    def close(self):
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
            except Exception:
                pass
        self._connected = False


class WebSocketFilesystemService(BaseService):
    """Service wrapping a WebSocket filesystem relay."""

    TYPE = "wsFilesystem"
    VERSION = "1.0.0"
    NAME = "Local Filesystem (WebSocket Relay)"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._host = self.config.get("host", "localhost")
        self._port = int(self.config.get("port", 9877))
        self._secret = self.config.get("secret", "")
        self._timeout = int(self.config.get("timeout", 30))
        self._mode = self.config.get("mode", "read")
        self._allowed = [p.strip() for p in self.config.get("allowed_paths", "").split(",") if p.strip()] or [""]
        self._denied = [p.strip() for p in self.config.get("denied_paths", "").split(",") if p.strip()]

    def _create_connection(self) -> PermissionEnforcedFilesystem:
        backend = RelayWebSocketBackend(self._host, self._port, self._secret, self._timeout)
        perms = FilesystemPermissions(self._mode, self._allowed, self._denied)
        return PermissionEnforcedFilesystem(backend, perms)

    def _close_connection(self):
        if self._connection:
            self._connection.close()

    # Convenience methods
    def list_dir(self, path="."): return self._get_connection().list_dir(path)
    def read_file(self, path): return self._get_connection().read_file(path)
    def write_file(self, path, content): self._get_connection().write_file(path, content)
    def delete_file(self, path): self._get_connection().delete_file(path)
    def mkdir(self, path): self._get_connection().mkdir(path)
    def stat(self, path): return self._get_connection().stat(path)
    def exists(self, path): return self._get_connection().exists(path)
    def search(self, path, pattern, recursive=True): return self._get_connection().search(path, pattern, recursive)
    def grep(self, path, regex, recursive=True): return self._get_connection().grep(path, regex, recursive)
    def find_replace(self, path, pattern, replacement): return self._get_connection().find_replace(path, pattern, replacement)
    def git_status(self, path="."): return self._get_connection().git_status(path)
    def git_log(self, path=".", count=10): return self._get_connection().git_log(path, count)
    def git_diff(self, path=".", ref=""): return self._get_connection().git_diff(path, ref)
    def git_commit(self, path=".", message=""): return self._get_connection().git_commit(path, message)
    def git_pull(self, path="."): return self._get_connection().git_pull(path)
    def git_push(self, path="."): return self._get_connection().git_push(path)
    def git_checkout(self, path=".", ref=""): return self._get_connection().git_checkout(path, ref)

    @property
    def supports_git(self): return self._get_connection().supports_git

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "host": {"type": "string", "required": False, "default": "localhost", "description": "Relay host"},
            "port": {"type": "integer", "required": True, "default": 9877, "description": "Relay port"},
            "secret": {"type": "string", "required": True, "sensitive": True, "description": "Shared secret"},
            "timeout": {"type": "integer", "required": False, "default": 30, "description": "Timeout (seconds)"},
            "mode": {"type": "select", "required": False, "default": "read", "options": ["read", "readwrite", "full"], "description": "Permission mode"},
            "allowed_paths": {"type": "string", "required": False, "default": "", "description": "Allowed path prefixes (comma-separated)"},
            "denied_paths": {"type": "string", "required": False, "default": "", "description": "Denied path prefixes (comma-separated)"},
        }


ServiceFactory.register(WebSocketFilesystemService)
