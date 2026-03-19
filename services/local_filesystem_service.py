"""Local Filesystem Service — HTTP relay backend for user's filesystem.

Connects to a pawflow_fs_relay.py script running on the user's machine,
providing secure filesystem access through the FilesystemBackend interface.

Config:
    host: str       — Relay host (default: "localhost")
    port: int       — Relay port (default: 9876)
    secret: str     — Shared secret for authentication
    timeout: int    — Request timeout in seconds (default: 30)
    mode: str       — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str — Comma-separated allowed path prefixes (default: "")
    denied_paths: str  — Comma-separated denied path prefixes (default: "")
"""

import base64
import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from core import ServiceFactory, ServiceError
from core.base_service import BaseService
from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem,
)

logger = logging.getLogger(__name__)


class RelayHTTPBackend(FilesystemBackend):
    """Client that talks to pawflow_fs_relay.py over HTTP POST."""

    def __init__(self, host: str, port: int, secret: str, timeout: int = 30):
        self._url = f"http://{host}:{port}"
        self._secret = secret
        self._timeout = timeout

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a request to the relay and return the data field."""
        payload = {"action": action, "path": path, "secret": self._secret}
        payload.update(kwargs)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ServiceError(f"Relay connection failed: {e}")
        except Exception as e:
            raise ServiceError(f"Relay request failed: {e}")

        if not result.get("ok"):
            raise ServiceError(result.get("error", "Unknown relay error"))
        return result.get("data")

    # ── Basic operations ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        data = self._request("list_dir", path)
        return [
            FilesystemEntry(
                name=e["name"],
                kind=e.get("kind", "file"),
                size=e.get("size", 0),
                modified=e.get("modified", ""),
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
            name=data.get("name", ""),
            kind=data.get("kind", "file"),
            size=data.get("size", 0),
            modified=data.get("modified", ""),
        )

    def exists(self, path: str) -> bool:
        data = self._request("exists", path)
        return data.get("exists", False)

    # ── Advanced operations ──

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        return self._request("search", path, pattern=pattern, recursive=recursive)

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        return self._request("grep", path, regex=regex, recursive=recursive)

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        return self._request("find_replace", path, pattern=pattern, replacement=replacement)

    # ── Git operations ──

    @property
    def supports_git(self) -> bool:
        return True

    def git_status(self, path: str = ".") -> Dict[str, Any]:
        return self._request("git_status", path)

    def git_log(self, path: str = ".", count: int = 10) -> List[Dict[str, Any]]:
        return self._request("git_log", path, count=count)

    def git_diff(self, path: str = ".", ref: str = "") -> str:
        return self._request("git_diff", path, ref=ref)

    def git_commit(self, path: str = ".", message: str = "") -> Dict[str, Any]:
        return self._request("git_commit", path, message=message)

    def git_pull(self, path: str = ".") -> Dict[str, Any]:
        return self._request("git_pull", path)

    def git_push(self, path: str = ".") -> Dict[str, Any]:
        return self._request("git_push", path)

    def git_checkout(self, path: str = ".", ref: str = "") -> Dict[str, Any]:
        return self._request("git_checkout", path, ref=ref)


class LocalFilesystemService(BaseService):
    """Service wrapping a filesystem relay on the user's machine."""

    TYPE = "localFilesystem"
    VERSION = "1.0.0"
    NAME = "Local Filesystem (HTTP Relay)"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._host = self.config.get("host", "localhost")
        self._port = int(self.config.get("port", 9876))
        self._secret = self.config.get("secret", "")
        self._timeout = int(self.config.get("timeout", 30))
        self._mode = self.config.get("mode", "read")
        self._allowed = [p.strip() for p in self.config.get("allowed_paths", "").split(",") if p.strip()] or [""]
        self._denied = [p.strip() for p in self.config.get("denied_paths", "").split(",") if p.strip()]

    def _create_connection(self) -> PermissionEnforcedFilesystem:
        backend = RelayHTTPBackend(self._host, self._port, self._secret, self._timeout)
        perms = FilesystemPermissions(self._mode, self._allowed, self._denied)
        return PermissionEnforcedFilesystem(backend, perms)

    def _close_connection(self):
        if self._connection:
            self._connection.close()

    def ping(self) -> bool:
        """Check relay connectivity (Plan C: heartbeat)."""
        try:
            req = urllib.request.Request(
                f"http://{self._host}:{self._port}", method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("ok", False) or "service" in data.get("data", {})
        except Exception:
            return False

    # ── Convenience methods (delegate to connection) ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        return self._get_connection().list_dir(path)

    def read_file(self, path: str) -> bytes:
        return self._get_connection().read_file(path)

    def write_file(self, path: str, content: bytes) -> None:
        self._get_connection().write_file(path, content)

    def delete_file(self, path: str) -> None:
        self._get_connection().delete_file(path)

    def mkdir(self, path: str) -> None:
        self._get_connection().mkdir(path)

    def stat(self, path: str) -> FilesystemEntry:
        return self._get_connection().stat(path)

    def exists(self, path: str) -> bool:
        return self._get_connection().exists(path)

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        return self._get_connection().search(path, pattern, recursive)

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        return self._get_connection().grep(path, regex, recursive)

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        return self._get_connection().find_replace(path, pattern, replacement)

    # Git convenience
    def git_status(self, path: str = ".") -> Dict[str, Any]:
        return self._get_connection().git_status(path)

    def git_log(self, path: str = ".", count: int = 10) -> List[Dict[str, Any]]:
        return self._get_connection().git_log(path, count)

    def git_diff(self, path: str = ".", ref: str = "") -> str:
        return self._get_connection().git_diff(path, ref)

    def git_commit(self, path: str = ".", message: str = "") -> Dict[str, Any]:
        return self._get_connection().git_commit(path, message)

    def git_pull(self, path: str = ".") -> Dict[str, Any]:
        return self._get_connection().git_pull(path)

    def git_push(self, path: str = ".") -> Dict[str, Any]:
        return self._get_connection().git_push(path)

    def git_checkout(self, path: str = ".", ref: str = "") -> Dict[str, Any]:
        return self._get_connection().git_checkout(path, ref)

    @property
    def supports_git(self) -> bool:
        return self._get_connection().supports_git

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "host": {
                "type": "string", "required": False, "default": "localhost",
                "description": "Relay host address",
            },
            "port": {
                "type": "integer", "required": True, "default": 9876,
                "description": "Relay port",
            },
            "secret": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Shared secret for relay authentication",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 30,
                "description": "Request timeout in seconds",
            },
            "mode": {
                "type": "select", "required": False, "default": "read",
                "options": ["read", "readwrite", "full"],
                "description": "Permission mode",
            },
            "allowed_paths": {
                "type": "string", "required": False, "default": "",
                "description": "Comma-separated allowed path prefixes (empty = all)",
            },
            "denied_paths": {
                "type": "string", "required": False, "default": "",
                "description": "Comma-separated denied path prefixes",
            },
        }


ServiceFactory.register(LocalFilesystemService)
