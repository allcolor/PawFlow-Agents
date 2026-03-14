"""OneDrive Filesystem Service — Access Microsoft OneDrive via Graph API.

Uses OAuthTokenStore for token management (auto-refresh). Implements the
FilesystemBackend interface so it works with all filesystem tools/tasks.

Config:
    drive_id: str       — Drive ID (default: "me" for user's personal drive)
    mode: str           — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str  — Comma-separated allowed path prefixes
    denied_paths: str   — Comma-separated denied path prefixes

Requires: OAuth2 authorization with microsoft_onedrive provider
(scope: Files.ReadWrite.All offline_access).
"""

import json
import logging
import posixpath
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from core import ServiceFactory, ServiceError
from core.base_service import BaseService
from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem,
)

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"


def _api_request(method: str, url: str, token: str,
                 body: Optional[bytes] = None,
                 content_type: str = "application/json",
                 timeout: int = 30) -> Any:
    """Make an authenticated Microsoft Graph API request."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", content_type)

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        data = resp.read()
        ct = resp.headers.get("Content-Type", "")
        if ct.startswith("application/json") and data:
            return json.loads(data.decode("utf-8"))
        return data


class OneDriveBackend(FilesystemBackend):
    """Access OneDrive via Microsoft Graph API."""

    def __init__(self, user_id: str, drive_id: str = "me", timeout: int = 30):
        self._user_id = user_id
        self._drive_id = drive_id
        self._timeout = timeout

    def _token(self) -> str:
        from core.oauth_token_store import OAuthTokenStore
        token = OAuthTokenStore.instance().get_access_token(
            self._user_id, "microsoft_onedrive")
        if not token:
            raise ServiceError(
                "No OneDrive token. Authorize via OAuth first.")
        return token

    def _drive_path(self, path: str) -> str:
        """Build the Graph API URL path for a given file/folder path."""
        path = path.strip("/")
        if not path or path == ".":
            return f"{_GRAPH}/me/drive/root"
        # Encode each path component
        encoded = "/".join(urllib.parse.quote(p) for p in path.split("/"))
        return f"{_GRAPH}/me/drive/root:/{encoded}:"

    def _item_to_entry(self, item: dict) -> FilesystemEntry:
        is_folder = "folder" in item
        return FilesystemEntry(
            name=item.get("name", ""),
            kind="directory" if is_folder else "file",
            size=int(item.get("size", 0)),
            modified=item.get("lastModifiedDateTime", ""),
        )

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        base = self._drive_path(path)
        url = f"{base}/children?$top=200"
        entries = []

        while url:
            result = _api_request("GET", url, self._token(),
                                  timeout=self._timeout)
            for item in result.get("value", []):
                entries.append(self._item_to_entry(item))
            url = result.get("@odata.nextLink")

        return entries

    def read_file(self, path: str) -> bytes:
        base = self._drive_path(path)
        url = f"{base}/content"
        return _api_request("GET", url, self._token(),
                            timeout=self._timeout)

    def write_file(self, path: str, content: bytes) -> None:
        base = self._drive_path(path)
        url = f"{base}/content"
        _api_request("PUT", url, self._token(), body=content,
                     content_type="application/octet-stream",
                     timeout=self._timeout)

    def delete_file(self, path: str) -> None:
        # Need item ID for delete
        base = self._drive_path(path)
        item = _api_request("GET", base, self._token(),
                            timeout=self._timeout)
        item_id = item["id"]
        url = f"{_GRAPH}/me/drive/items/{item_id}"
        _api_request("DELETE", url, self._token(), timeout=self._timeout)

    def mkdir(self, path: str) -> None:
        path = path.strip("/")
        parts = path.split("/")

        # Create each directory level
        current_base = f"{_GRAPH}/me/drive/root"
        for part in parts:
            children_url = f"{current_base}/children"
            metadata = {
                "name": part,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            }
            try:
                result = _api_request(
                    "POST", children_url, self._token(),
                    body=json.dumps(metadata).encode("utf-8"),
                    timeout=self._timeout,
                )
                item_id = result["id"]
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    # Already exists — resolve it
                    encoded = urllib.parse.quote(part)
                    resolve_url = f"{current_base}:/{encoded}:"
                    item = _api_request("GET", resolve_url, self._token(),
                                        timeout=self._timeout)
                    item_id = item["id"]
                else:
                    raise
            current_base = f"{_GRAPH}/me/drive/items/{item_id}"

    def stat(self, path: str) -> FilesystemEntry:
        base = self._drive_path(path)
        item = _api_request("GET", base, self._token(),
                            timeout=self._timeout)
        return self._item_to_entry(item)

    def exists(self, path: str) -> bool:
        try:
            base = self._drive_path(path)
            _api_request("GET", base, self._token(), timeout=self._timeout)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def search(self, path: str, pattern: str,
               recursive: bool = True) -> List[str]:
        """Search files by name using Graph search API."""
        import fnmatch
        search_term = pattern.replace("*", "").replace("?", "")
        if not search_term:
            search_term = " "  # Graph requires non-empty query

        base = self._drive_path(path)
        url = f"{base}/search(q='{urllib.parse.quote(search_term)}')"
        results = []

        while url:
            resp = _api_request("GET", url, self._token(),
                                timeout=self._timeout)
            for item in resp.get("value", []):
                name = item.get("name", "")
                if fnmatch.fnmatch(name, pattern):
                    # Build relative path from parentReference
                    parent = item.get("parentReference", {}).get("path", "")
                    # Remove /drive/root: prefix
                    if ":" in parent:
                        parent = parent.split(":", 1)[1].lstrip("/")
                    else:
                        parent = ""
                    rel = posixpath.join(parent, name) if parent else name
                    results.append(rel)
            url = resp.get("@odata.nextLink")

        return results

    def grep(self, path: str, regex: str,
             recursive: bool = True) -> List[dict]:
        """Search file contents by downloading and matching locally."""
        import re as re_mod
        results = []
        entries = self.list_dir(path)

        for entry in entries:
            if entry.kind == "directory" and recursive:
                sub = posixpath.join(path, entry.name) if path != "." else entry.name
                results.extend(self.grep(sub, regex, recursive))
            elif entry.kind == "file":
                file_path = posixpath.join(path, entry.name) if path != "." else entry.name
                try:
                    content = self.read_file(file_path)
                    text = content.decode("utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        m = re_mod.search(regex, line)
                        if m:
                            results.append({
                                "path": file_path,
                                "line_number": i,
                                "line": line,
                                "match": m.group(),
                            })
                except Exception:
                    pass

        return results

    def find_replace(self, path: str, pattern: str,
                     replacement: str) -> dict:
        import re as re_mod
        content = self.read_file(path)
        text = content.decode("utf-8")
        new_text, count = re_mod.subn(pattern, replacement, text)
        if count > 0:
            self.write_file(path, new_text.encode("utf-8"))
        return {"path": path, "replacements": count}

    def close(self) -> None:
        pass

    @property
    def supports_git(self) -> bool:
        return False


class OneDriveService(BaseService):
    """Microsoft OneDrive filesystem service."""

    TYPE = "oneDrive"
    VERSION = "1.0.0"
    NAME = "Microsoft OneDrive"
    DESCRIPTION = "Access OneDrive files via Microsoft Graph API"
    ICON = "cloud"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "drive_id": {
                "type": "string", "required": False, "default": "me",
                "description": "Drive ID (default: user's personal drive)",
            },
            "mode": {
                "type": "select", "required": False, "default": "readwrite",
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
            "timeout": {
                "type": "integer", "required": False, "default": 30,
                "description": "Request timeout in seconds",
            },
        }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._user_id: str = ""

    def set_user_id(self, user_id: str):
        """Set the authenticated user ID (injected at runtime)."""
        self._user_id = user_id

    def _create_connection(self):
        if not self._user_id:
            raise ServiceError("OneDrive requires user_id (set via set_user_id)")

        drive_id = self.config.get("drive_id", "me")
        timeout = int(self.config.get("timeout", 30))
        backend = OneDriveBackend(self._user_id, drive_id, timeout)

        mode = self.config.get("mode", "readwrite")
        allowed = [p.strip() for p in self.config.get("allowed_paths", "").split(",") if p.strip()]
        denied = [p.strip() for p in self.config.get("denied_paths", "").split(",") if p.strip()]
        perms = FilesystemPermissions(mode, allowed or [""], denied)

        return PermissionEnforcedFilesystem(backend, perms)

    def _close_connection(self):
        conn = getattr(self, '_connection', None)
        if conn:
            conn.close()

    # ── Convenience methods ─────────────────────────────────────────

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

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[dict]:
        return self._get_connection().grep(path, regex, recursive)

    def find_replace(self, path: str, pattern: str, replacement: str) -> dict:
        return self._get_connection().find_replace(path, pattern, replacement)


ServiceFactory.register(OneDriveService)
