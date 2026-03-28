"""Server Filesystem Service — Direct access to the server's disk (admin-only).

For the rare cases where a flow needs to access the server filesystem
(exports, logs, staging areas). Restricted to admin users only.

Config:
    root: str          — Absolute path to the root directory (required)
    mode: str          — Permission mode: "read" | "readwrite" | "full"
    allowed_paths: str — Comma-separated allowed path prefixes
    denied_paths: str  — Comma-separated denied path prefixes

SECURITY: Only admin users can install this service type. Enforced in
ServiceFactory and API layer.
"""

import fnmatch
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core import ServiceFactory, ServiceError
from core.base_service import BaseService
from core.filesystem import (
    FilesystemBackend, FilesystemEntry, FilesystemPermissions,
    PermissionEnforcedFilesystem,
)

logger = logging.getLogger(__name__)


class ServerFilesystemBackend(FilesystemBackend):
    """Direct filesystem access on the server. ADMIN ONLY."""

    def __init__(self, root_path: str):
        self._root = Path(root_path).resolve()
        if not self._root.is_dir():
            raise ServiceError(f"Root directory does not exist: {root_path}")

    def _resolve(self, path: str) -> Path:
        """Resolve path with anti-traversal check."""
        target = (self._root / path).resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            raise PermissionError(f"Path traversal blocked: {path}")
        return target

    # ── Basic operations ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        p = self._resolve(path)
        entries = []
        for entry in sorted(p.iterdir()):
            st = entry.stat()
            entries.append(FilesystemEntry(
                name=entry.name,
                kind="directory" if entry.is_dir() else "file",
                size=st.st_size if entry.is_file() else 0,
                modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            ))
        return entries

    def read_file(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write_file(self, path: str, content: bytes) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def delete_file(self, path: str) -> None:
        p = self._resolve(path)
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        else:
            raise FileNotFoundError(f"Not found: {path}")

    def mkdir(self, path: str) -> None:
        self._resolve(path).mkdir(parents=True, exist_ok=True)

    def stat(self, path: str) -> FilesystemEntry:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"Not found: {path}")
        st = p.stat()
        return FilesystemEntry(
            name=p.name,
            kind="directory" if p.is_dir() else "file",
            size=st.st_size if p.is_file() else 0,
            modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        )

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    # ── Advanced operations ──

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        p = self._resolve(path)
        matches = p.rglob(pattern) if recursive else p.glob(pattern)
        results = []
        for m in sorted(matches):
            try:
                results.append(str(m.relative_to(self._root)).replace("\\", "/"))
            except ValueError:
                pass
        return results

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        compiled = re.compile(regex)
        p = self._resolve(path)
        files = p.rglob("*") if recursive else p.glob("*")
        results = []
        for fp in sorted(files):
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                m = compiled.search(line)
                if m:
                    try:
                        rel = str(fp.relative_to(self._root)).replace("\\", "/")
                    except ValueError:
                        rel = str(fp)
                    results.append({
                        "path": rel, "line_number": i,
                        "line": line, "match": m.group(),
                    })
        return results

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        compiled = re.compile(pattern)
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        new_text, count = compiled.subn(replacement, text)
        if count > 0:
            p.write_text(new_text, encoding="utf-8")
        try:
            rel = str(p.relative_to(self._root)).replace("\\", "/")
        except ValueError:
            rel = str(p)
        return {"replacements": count, "path": rel}



class ServerFilesystemService(BaseService):
    """Server filesystem service. ADMIN ONLY."""

    TYPE = "filesystem"
    VERSION = "1.0.0"
    NAME = "Server Filesystem (Admin Only)"
    ADMIN_ONLY = True  # Flag for service installation checks

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._root = self.config.get("root", "")
        self._mode = self.config.get("mode", "read")
        self._allowed = [p.strip() for p in self.config.get("allowed_paths", "").split(",") if p.strip()] or [""]
        self._denied = [p.strip() for p in self.config.get("denied_paths", "").split(",") if p.strip()]

    def _create_connection(self) -> PermissionEnforcedFilesystem:
        if not self._root:
            raise ServiceError("'root' config is required for server filesystem")
        backend = ServerFilesystemBackend(self._root)
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
            "root": {"type": "string", "required": True, "description": "Absolute path to root directory"},
            "mode": {"type": "select", "required": False, "default": "read", "options": ["read", "readwrite", "full"], "description": "Permission mode"},
            "allowed_paths": {"type": "string", "required": False, "default": "", "description": "Allowed path prefixes"},
            "denied_paths": {"type": "string", "required": False, "default": "", "description": "Denied path prefixes"},
        }


ServiceFactory.register(ServerFilesystemService)
