"""Google Drive Filesystem Service — Access Google Drive via REST API v3.

Uses OAuthTokenStore for token management (auto-refresh). Implements the
FilesystemBackend interface so it works with all filesystem tools/tasks.

Config:
    folder_id: str      — Root folder ID (default: "root")
    mode: str           — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str  — Comma-separated allowed path prefixes
    denied_paths: str   — Comma-separated denied path prefixes

Requires: OAuth2 authorization with google_drive provider (scope: drive).
The user_id is injected at runtime from the authenticated session.
"""

import base64
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

_BASE = "https://www.googleapis.com"
_DRIVE_V3 = f"{_BASE}/drive/v3"


def _api_request(method: str, url: str, token: str,
                 body: Optional[bytes] = None,
                 content_type: str = "application/json",
                 timeout: int = 30) -> Any:
    """Make an authenticated Google API request."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", content_type)

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        data = resp.read()
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(data.decode("utf-8"))
        return data


def _multipart_upload(url: str, token: str, metadata: dict,
                      content: bytes, mime_type: str = "application/octet-stream",
                      method: str = "POST", timeout: int = 60) -> dict:
    """Multipart upload (metadata + content) to Google Drive."""
    boundary = "pyfi2_boundary_drive"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


class GoogleDriveBackend(FilesystemBackend):
    """Access Google Drive via REST API v3."""

    def __init__(self, user_id: str, folder_id: str = "root", timeout: int = 30):
        self._user_id = user_id
        self._root_folder_id = folder_id
        self._timeout = timeout
        # Cache: path -> file_id (cleared on write operations)
        self._path_cache: Dict[str, str] = {}

    def _token(self) -> str:
        """Get a valid access token (auto-refreshes if needed)."""
        from core.oauth_token_store import OAuthTokenStore
        token = OAuthTokenStore.instance().get_access_token(
            self._user_id, "google_drive")
        if not token:
            raise ServiceError(
                "No Google Drive token. Authorize via OAuth first.")
        return token

    def _resolve_path(self, path: str) -> str:
        """Resolve a relative path to a Google Drive file ID.

        Path components are resolved one by one by listing children.
        Returns file_id or raises FileNotFoundError.
        """
        path = path.strip("/")
        if not path or path == ".":
            return self._root_folder_id

        if path in self._path_cache:
            return self._path_cache[path]

        parts = path.split("/")
        current_id = self._root_folder_id

        for i, part in enumerate(parts):
            q = (f"'{current_id}' in parents and name = '{part}' "
                 f"and trashed = false")
            url = (f"{_DRIVE_V3}/files?"
                   f"q={urllib.parse.quote(q)}&fields=files(id,name,mimeType)"
                   f"&pageSize=1")
            result = _api_request("GET", url, self._token(),
                                  timeout=self._timeout)
            files = result.get("files", [])
            if not files:
                partial = "/".join(parts[:i + 1])
                raise FileNotFoundError(f"Not found: {partial}")
            current_id = files[0]["id"]

        self._path_cache[path] = current_id
        return current_id

    def _resolve_parent(self, path: str) -> tuple:
        """Resolve parent folder ID and filename from path.

        Returns (parent_id, filename). Creates parent folders if needed.
        """
        path = path.strip("/")
        if "/" not in path:
            return self._root_folder_id, path
        parent_path = posixpath.dirname(path)
        filename = posixpath.basename(path)
        try:
            parent_id = self._resolve_path(parent_path)
        except FileNotFoundError:
            # Create parent directories recursively
            parent_id = self._mkdir_recursive(parent_path)
        return parent_id, filename

    def _mkdir_recursive(self, path: str) -> str:
        """Create nested directories, returning the final folder ID."""
        parts = path.strip("/").split("/")
        current_id = self._root_folder_id
        for part in parts:
            # Check if exists
            q = (f"'{current_id}' in parents and name = '{part}' "
                 f"and mimeType = 'application/vnd.google-apps.folder' "
                 f"and trashed = false")
            url = (f"{_DRIVE_V3}/files?"
                   f"q={urllib.parse.quote(q)}&fields=files(id)"
                   f"&pageSize=1")
            result = _api_request("GET", url, self._token(),
                                  timeout=self._timeout)
            files = result.get("files", [])
            if files:
                current_id = files[0]["id"]
            else:
                # Create folder
                metadata = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [current_id],
                }
                created = _api_request(
                    "POST", f"{_DRIVE_V3}/files",
                    self._token(),
                    body=json.dumps(metadata).encode("utf-8"),
                    timeout=self._timeout,
                )
                current_id = created["id"]
        return current_id

    def _invalidate_cache(self, path: str):
        """Remove cached entries for path and its parents."""
        path = path.strip("/")
        self._path_cache.pop(path, None)
        while "/" in path:
            path = posixpath.dirname(path)
            self._path_cache.pop(path, None)

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        folder_id = self._resolve_path(path)
        entries = []
        page_token = None

        while True:
            q = f"'{folder_id}' in parents and trashed = false"
            fields = "nextPageToken,files(id,name,mimeType,size,modifiedTime)"
            url = (f"{_DRIVE_V3}/files?"
                   f"q={urllib.parse.quote(q)}&fields={fields}"
                   f"&pageSize=1000&orderBy=name")
            if page_token:
                url += f"&pageToken={page_token}"

            result = _api_request("GET", url, self._token(),
                                  timeout=self._timeout)

            for f in result.get("files", []):
                is_folder = (f.get("mimeType") ==
                             "application/vnd.google-apps.folder")
                entries.append(FilesystemEntry(
                    name=f["name"],
                    kind="directory" if is_folder else "file",
                    size=int(f.get("size", 0)),
                    modified=f.get("modifiedTime", ""),
                ))

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return entries

    def read_file(self, path: str) -> bytes:
        file_id = self._resolve_path(path)
        url = f"{_DRIVE_V3}/files/{file_id}?alt=media"
        return _api_request("GET", url, self._token(), timeout=self._timeout)

    def write_file(self, path: str, content: bytes) -> None:
        self._invalidate_cache(path)
        try:
            # Try to update existing file
            file_id = self._resolve_path(path)
            upload_url = (f"{_BASE}/upload/drive/v3/files/{file_id}"
                          f"?uploadType=multipart")
            _multipart_upload(upload_url, self._token(),
                              metadata={}, content=content, method="PATCH",
                              timeout=self._timeout)
        except FileNotFoundError:
            # Create new file
            parent_id, filename = self._resolve_parent(path)
            upload_url = (f"{_BASE}/upload/drive/v3/files"
                          f"?uploadType=multipart")
            metadata = {"name": filename, "parents": [parent_id]}
            result = _multipart_upload(upload_url, self._token(),
                                       metadata=metadata, content=content,
                                       timeout=self._timeout)
            # Cache the new file ID
            clean_path = path.strip("/")
            self._path_cache[clean_path] = result["id"]

    def delete_file(self, path: str) -> None:
        file_id = self._resolve_path(path)
        url = f"{_DRIVE_V3}/files/{file_id}"
        _api_request("DELETE", url, self._token(), timeout=self._timeout)
        self._invalidate_cache(path)

    def mkdir(self, path: str) -> None:
        self._mkdir_recursive(path)

    def stat(self, path: str) -> FilesystemEntry:
        file_id = self._resolve_path(path)
        url = (f"{_DRIVE_V3}/files/{file_id}"
               f"?fields=name,mimeType,size,modifiedTime")
        f = _api_request("GET", url, self._token(), timeout=self._timeout)
        is_folder = (f.get("mimeType") ==
                     "application/vnd.google-apps.folder")
        return FilesystemEntry(
            name=f["name"],
            kind="directory" if is_folder else "file",
            size=int(f.get("size", 0)),
            modified=f.get("modifiedTime", ""),
        )

    def exists(self, path: str) -> bool:
        try:
            self._resolve_path(path)
            return True
        except FileNotFoundError:
            return False

    def search(self, path: str, pattern: str,
               recursive: bool = True) -> List[str]:
        """Search files by name pattern. Uses Drive API search."""
        folder_id = self._resolve_path(path)

        # Convert glob to Drive query
        # Drive supports 'name contains' but not full glob
        search_term = pattern.replace("*", "").replace("?", "")
        q_parts = [f"trashed = false"]
        if search_term:
            q_parts.append(f"name contains '{search_term}'")
        if not recursive:
            q_parts.append(f"'{folder_id}' in parents")

        q = " and ".join(q_parts)
        results = []
        page_token = None

        while True:
            url = (f"{_DRIVE_V3}/files?"
                   f"q={urllib.parse.quote(q)}"
                   f"&fields=nextPageToken,files(name)"
                   f"&pageSize=100")
            if page_token:
                url += f"&pageToken={page_token}"
            resp = _api_request("GET", url, self._token(),
                                timeout=self._timeout)
            for f in resp.get("files", []):
                # Apply local glob filter for exact matching
                import fnmatch
                if fnmatch.fnmatch(f["name"], pattern):
                    results.append(f["name"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return results

    def grep(self, path: str, regex: str,
             recursive: bool = True) -> List[dict]:
        """Search file contents. Downloads files and searches locally."""
        import re as re_mod
        results = []
        entries = self.list_dir(path)

        for entry in entries:
            if entry.kind == "directory" and recursive:
                sub_path = posixpath.join(path, entry.name) if path != "." else entry.name
                results.extend(self.grep(sub_path, regex, recursive))
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
                    pass  # Skip binary/unreadable files

        return results

    def find_replace(self, path: str, pattern: str,
                     replacement: str) -> dict:
        """Find and replace in a file."""
        import re as re_mod
        content = self.read_file(path)
        text = content.decode("utf-8")
        new_text, count = re_mod.subn(pattern, replacement, text)
        if count > 0:
            self.write_file(path, new_text.encode("utf-8"))
        return {"path": path, "replacements": count}

    def close(self) -> None:
        self._path_cache.clear()

    @property
    def supports_git(self) -> bool:
        return False


class GoogleDriveService(BaseService):
    """Google Drive filesystem service."""

    TYPE = "googleDrive"
    VERSION = "1.0.0"
    NAME = "Google Drive"
    DESCRIPTION = "Access Google Drive files via REST API"
    ICON = "cloud"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "folder_id": {
                "type": "string", "required": False, "default": "root",
                "description": "Root folder ID (default: Drive root)",
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
            raise ServiceError("GoogleDrive requires user_id (set via set_user_id)")

        folder_id = self.config.get("folder_id", "root")
        timeout = int(self.config.get("timeout", 30))
        backend = GoogleDriveBackend(self._user_id, folder_id, timeout)

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


ServiceFactory.register(GoogleDriveService)
