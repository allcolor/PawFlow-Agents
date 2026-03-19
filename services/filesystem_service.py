"""Unified Filesystem Service — reverse WebSocket relay.

The relay runs on the user's machine and connects TO the PawFlow server.
This service resolves commands via the RelayConnectionManager.

Config:
    token: str      — Shared token (relay must match to connect)
    mode: str       — Permission mode: "read" | "readwrite" (default: "readwrite")

Usage:
    1. Create service in PawFlow: type=filesystem, token=abc123, mode=readwrite
    2. Start relay: python tools/pawflow_relay.py --server ws://host:port/ws/relay
       --relay-id <service_id> --token abc123 --dir /path
    3. Agent calls: filesystem(action=read_file, path=file.txt, service=<service_id>)
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from core import ServiceError

logger = logging.getLogger(__name__)


class FilesystemService:
    """Filesystem service backed by a reverse WebSocket relay.

    The relay connects to the server and registers with relay_id = service_id.
    Commands are routed via RelayConnectionManager.
    """

    TYPE = "filesystem"
    VERSION = "2.0.0"
    NAME = "Filesystem (Relay)"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._token = config.get("token", "")
        self._mode = config.get("mode", "readwrite")
        self._service_id = config.get("_service_id", "")  # injected by registry
        self._connection = True  # always "connected" — relay manages the WS

    @property
    def service_id(self) -> str:
        return self._service_id

    def connect(self):
        """No-op — the relay connects to us, not the other way."""
        pass

    def disconnect(self):
        """No-op — relay manages its own connection."""
        pass

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _get_relay(self):
        """Get the connected relay for this service."""
        from core.relay_manager import RelayConnectionManager
        mgr = RelayConnectionManager.instance()
        conn = mgr.get(self._user_id if hasattr(self, '_user_id') else "",
                       relay_type="filesystem")
        if conn:
            return conn
        # Try by relay_id matching service_id
        with mgr._data_lock:
            for uid, relays in mgr._connections.items():
                for rid, rc in relays.items():
                    if rid == self._service_id and rc.relay_type == "filesystem":
                        return rc
        return None

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a command to the relay and wait for the result."""
        import uuid
        from core.relay_manager import RelayConnectionManager

        mgr = RelayConnectionManager.instance()
        conn = self._get_relay()
        if not conn:
            raise ServiceError(
                f"Relay '{self._service_id}' not connected. "
                f"Start: python tools/pawflow_relay.py "
                f"--server ws://<host>:<port>/ws/relay "
                f"--relay-id {self._service_id} --token <token> --dir <path>"
            )

        request_id = uuid.uuid4().hex[:12]
        user_id = conn.user_id
        payload = {"action": action, "path": path, **kwargs}

        try:
            result = mgr.send_command_sync(
                user_id, self._service_id, request_id, payload, timeout=60
            )
        except Exception as e:
            raise ServiceError(f"Relay command failed: {e}")

        if isinstance(result, dict) and result.get("error"):
            raise ServiceError(result["error"])

        return result

    # ── Filesystem interface ──

    def list_dir(self, path: str = "."):
        from core.filesystem import FilesystemEntry
        data = self._request("list_dir", path)
        return [FilesystemEntry(**e) if isinstance(e, dict) else e for e in data]

    def read_file(self, path: str) -> bytes:
        import base64
        data = self._request("read_file", path)
        if isinstance(data, dict) and "content" in data:
            return base64.b64decode(data["content"])
        return data.encode("utf-8") if isinstance(data, str) else data

    def write_file(self, path: str, content: bytes):
        import base64
        self._request("write_file", path,
                       content=base64.b64encode(content).decode("ascii"),
                       base64=True)

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

    def git_status(self, path: str = "."):
        return self._request("git_status", path)

    def git_log(self, path: str = ".", count: int = 10):
        return self._request("git_log", path, count=count)

    def git_diff(self, path: str = ".", ref: str = ""):
        return self._request("git_diff", path, ref=ref)

    def git_commit(self, path: str = ".", message: str = ""):
        return self._request("git_commit", path, message=message)

    def git_pull(self, path: str = "."):
        return self._request("git_pull", path)

    def git_push(self, path: str = "."):
        return self._request("git_push", path)

    def git_checkout(self, path: str = ".", ref: str = ""):
        return self._request("git_checkout", path, ref=ref)


# Register with ServiceFactory
from core import ServiceFactory
ServiceFactory.register(FilesystemService)
