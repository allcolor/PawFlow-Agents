"""Browser Filesystem Service — File System Access API backend.

Bridges to the user's browser via SSE events. The browser executes filesystem
operations locally using the File System Access API (Chromium only) and posts
results back. Refactored from the LocalFilesHandler tool.

Config:
    conversation_id: str — Auto-set from agent context
    mode: str            — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str   — Comma-separated allowed path prefixes
    denied_paths: str    — Comma-separated denied path prefixes

Limitations:
    - Only works in Chromium-based browsers (Chrome, Edge)
    - Requires user to open a folder via the folder button in chat UI
    - No git support (browser has no git access)
    - search/grep/find_replace handled in browser JavaScript
"""

import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from core import ServiceFactory, ServiceError
from core.base_service import BaseService
from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem,
)

logger = logging.getLogger(__name__)


class BrowserFilesystemBackend(FilesystemBackend):
    """Bridge to the browser via SSE file_request events.

    Each operation publishes an SSE event and blocks until the browser
    responds (or times out after 60s).
    """

    # Class-level shared state (same pattern as LocalFilesHandler)
    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    def __init__(self, conversation_id: str):
        self._conversation_id = conversation_id

    def _request(self, action: str, path: str = ".", **kwargs) -> Any:
        """Send a file_request SSE event and wait for the browser response."""
        from core.conversation_event_bus import ConversationEventBus

        if not self._conversation_id:
            raise ServiceError("No conversation context for browser filesystem")

        request_id = uuid.uuid4().hex[:12]
        event = threading.Event()

        with self._lock:
            self._pending[request_id] = event

        # Publish SSE event to browser
        payload = {"request_id": request_id, "action": action, "path": path}
        payload.update(kwargs)
        ConversationEventBus.instance().publish_event(
            self._conversation_id, "file_request", payload,
        )

        # Block until browser responds
        if not event.wait(timeout=60):
            with self._lock:
                self._pending.pop(request_id, None)
                self._results.pop(request_id, None)
            raise ServiceError(
                "Browser did not respond within 60s. "
                "Make sure a local folder is opened via the folder button."
            )

        with self._lock:
            result = self._results.pop(request_id, None)
            self._pending.pop(request_id, None)

        if result is None:
            raise ServiceError("No result received from browser")

        if isinstance(result, dict) and "error" in result:
            raise ServiceError(result["error"])

        return result

    @classmethod
    def resolve_request(cls, request_id: str, result: Any) -> bool:
        """Called when the browser POSTs a file operation result back."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                return False
            cls._results[request_id] = result
            event.set()
        return True

    # ── FilesystemBackend implementation ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        data = self._request("list_dir", path)
        if isinstance(data, list):
            return [
                FilesystemEntry(
                    name=e.get("name", ""), kind=e.get("kind", "file"),
                    size=e.get("size", 0), modified=e.get("modified", ""),
                )
                for e in data
            ]
        # Legacy format: data might be a dict with entries
        entries = data.get("entries", []) if isinstance(data, dict) else []
        return [
            FilesystemEntry(
                name=e.get("name", ""), kind="directory" if e.get("is_dir") else "file",
                size=e.get("size", 0), modified=e.get("modified", ""),
            )
            for e in entries
        ]

    def read_file(self, path: str) -> bytes:
        data = self._request("read_file", path)
        if isinstance(data, dict):
            content = data.get("content", "")
        else:
            content = str(data)
        # Browser returns text content, not base64
        return content.encode("utf-8") if isinstance(content, str) else content

    def write_file(self, path: str, content: bytes) -> None:
        # Browser expects text content
        text = content.decode("utf-8", errors="replace")
        self._request("write_file", path, content=text)

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
        data = self._request("exists", path)
        return data.get("exists", False) if isinstance(data, dict) else bool(data)

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        data = self._request("search", path, pattern=pattern, recursive=recursive)
        return data if isinstance(data, list) else []

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        data = self._request("grep", path, regex=regex, recursive=recursive)
        return data if isinstance(data, list) else []

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        return self._request("find_replace", path, pattern=pattern, replacement=replacement)


class BrowserFilesystemService(BaseService):
    """Service wrapping the browser File System Access API."""

    TYPE = "browserFilesystem"
    VERSION = "1.0.0"
    NAME = "Browser Filesystem (File System Access API)"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._conversation_id = self.config.get("conversation_id", "")
        self._mode = self.config.get("mode", "readwrite")
        self._allowed = [p.strip() for p in self.config.get("allowed_paths", "").split(",") if p.strip()] or [""]
        self._denied = [p.strip() for p in self.config.get("denied_paths", "").split(",") if p.strip()]

    def set_conversation_id(self, conversation_id: str):
        """Set conversation ID (called by agent_loop at runtime)."""
        self._conversation_id = conversation_id

    def _create_connection(self) -> PermissionEnforcedFilesystem:
        backend = BrowserFilesystemBackend(self._conversation_id)
        perms = FilesystemPermissions(self._mode, self._allowed, self._denied)
        return PermissionEnforcedFilesystem(backend, perms)

    def _close_connection(self):
        pass

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

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {"type": "string", "required": False, "description": "Conversation ID (auto-set)"},
            "mode": {"type": "select", "required": False, "default": "readwrite", "options": ["read", "readwrite", "full"], "description": "Permission mode"},
            "allowed_paths": {"type": "string", "required": False, "default": "", "description": "Allowed path prefixes"},
            "denied_paths": {"type": "string", "required": False, "default": "", "description": "Denied path prefixes"},
        }


ServiceFactory.register(BrowserFilesystemService)
