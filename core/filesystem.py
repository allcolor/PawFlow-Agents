"""Filesystem abstraction layer for PawFlow.

Provides a unified interface for accessing filesystems from various backends
(HTTP relay, WebSocket relay, browser File System Access API, server local,
Google Drive, OneDrive). All file access in PawFlow should go through this
interface to enforce permissions and path safety.

Key classes:
- FilesystemEntry: Metadata about a file or directory
- FilesystemPermissions: Access control (mode + allowed/denied paths)
- FilesystemBackend: Abstract interface all backends implement
- PermissionEnforcedFilesystem: Wrapper that enforces permissions + normalizes paths
"""

import posixpath
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Data types ───────────────────────────────────────────────────────

@dataclass
class FilesystemEntry:
    """Metadata about a file or directory."""
    name: str
    kind: str  # "file" | "directory"
    size: int = 0
    modified: str = ""  # ISO 8601 timestamp


@dataclass
class GrepMatch:
    """A single grep match result."""
    path: str
    line_number: int
    line: str
    match: str = ""


# ── Permissions ──────────────────────────────────────────────────────

class FilesystemPermissions:
    """Configurable access control for filesystem operations.

    Args:
        mode: "read" | "readwrite" | "full"
        allowed_paths: Prefix whitelist ([""] = everything allowed)
        denied_paths: Prefix blacklist (takes priority over allowed)
    """

    MODES = ("read", "readwrite", "full")

    # Which modes allow which operation categories
    _OP_REQUIREMENTS = {
        "read": "read",
        "write": "readwrite",
        "delete": "full",
    }

    def __init__(self, mode: str = "read",
                 allowed_paths: Optional[List[str]] = None,
                 denied_paths: Optional[List[str]] = None):
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode '{mode}', must be one of {self.MODES}")
        self.mode = mode
        self.allowed_paths = allowed_paths if allowed_paths is not None else [""]
        self.denied_paths = denied_paths if denied_paths is not None else []

    def check(self, path: str, operation: str) -> bool:
        """Check if an operation is allowed on a path.

        Args:
            path: Normalized relative path (no leading slash, no ..)
            operation: 'read' | 'write' | 'delete'

        Returns:
            True if allowed, False if denied.
        """
        # 1. Mode check
        required = self._OP_REQUIREMENTS.get(operation, "full")
        mode_rank = self.MODES.index(self.mode)
        required_rank = self.MODES.index(required)
        if mode_rank < required_rank:
            return False

        # 2. Denied paths (priority)
        for denied in self.denied_paths:
            if denied and (path == denied or path.startswith(denied.rstrip("/") + "/")):
                return False

        # 3. Allowed paths (at least one must match)
        for allowed in self.allowed_paths:
            if allowed == "" or path == allowed or path.startswith(allowed.rstrip("/") + "/"):
                return True

        return False


# ── Abstract backend ─────────────────────────────────────────────────

class FilesystemBackend(ABC):
    """Abstract interface for all filesystem backends."""

    # ── Basic operations ──

    @abstractmethod
    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        """List directory contents."""

    @abstractmethod
    def read_file(self, path: str) -> bytes:
        """Read file content."""

    @abstractmethod
    def write_file(self, path: str, content: bytes) -> None:
        """Write content to a file (create or overwrite)."""

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file."""

    @abstractmethod
    def mkdir(self, path: str) -> None:
        """Create a directory (and parents if needed)."""

    @abstractmethod
    def stat(self, path: str) -> FilesystemEntry:
        """Get file/directory metadata."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists."""

    # ── Advanced operations ──

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        """Search for files by name (glob pattern). Returns relative paths."""
        raise NotImplementedError("search not supported by this backend")

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        """Search in file contents. Returns [{path, line_number, line, match}]."""
        raise NotImplementedError("grep not supported by this backend")

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        """Replace text in a file. Returns {replacements: int, path: str}."""
        raise NotImplementedError("find_replace not supported by this backend")

    # ── Git operations (optional) ──

    @property
    def supports_git(self) -> bool:
        """True if the backend supports git operations."""
        return False

    def git_status(self, path: str = ".") -> Dict[str, Any]:
        """Return {branch, clean, staged, modified, untracked}."""
        raise NotImplementedError("git not supported by this backend")

    def git_log(self, path: str = ".", count: int = 10) -> List[Dict[str, Any]]:
        """Return [{hash, author, date, message}]."""
        raise NotImplementedError("git not supported by this backend")

    def git_diff(self, path: str = ".", ref: str = "") -> str:
        """Return the textual diff."""
        raise NotImplementedError("git not supported by this backend")

    def git_commit(self, path: str = ".", message: str = "") -> Dict[str, Any]:
        """Stage all + commit. Return {hash, message}."""
        raise NotImplementedError("git not supported by this backend")

    def git_pull(self, path: str = ".") -> Dict[str, Any]:
        """Pull. Return {updated, conflicts}."""
        raise NotImplementedError("git not supported by this backend")

    def git_push(self, path: str = ".") -> Dict[str, Any]:
        """Push. Return {pushed, remote}."""
        raise NotImplementedError("git not supported by this backend")

    def git_checkout(self, path: str = ".", ref: str = "") -> Dict[str, Any]:
        """Checkout branch/tag. Return {branch}."""
        raise NotImplementedError("git not supported by this backend")

    def close(self) -> None:
        """Release resources held by this backend."""
        pass


# ── Permission-enforced wrapper ──────────────────────────────────────

def _normalize_path(path: str) -> str:
    """Normalize a path to a safe relative form.

    - Converts backslashes to forward slashes
    - Removes leading slashes
    - Collapses . and resolves ..
    - Rejects paths that escape the root via ..

    Returns:
        Normalized relative path (e.g. "src/main.py", "." for root)

    Raises:
        PermissionError: If the path tries to escape root via ..
    """
    # Normalize separators
    p = path.replace("\\", "/")

    # Strip leading slashes (we work with relative paths)
    p = p.lstrip("/")

    # Normalize with posixpath
    p = posixpath.normpath(p) if p else "."

    # Reject traversal above root
    if p.startswith("..") or "/../" in f"/{p}/" or p == "..":
        raise PermissionError(f"Path traversal blocked: {path}")

    return p


# Operation category mapping
_OP_CATEGORY = {
    # Read operations
    "list_dir": "read",
    "read_file": "read",
    "stat": "read",
    "exists": "read",
    "search": "read",
    "grep": "read",
    "git_status": "read",
    "git_log": "read",
    "git_diff": "read",
    # Write operations
    "write_file": "write",
    "mkdir": "write",
    "find_replace": "write",
    "git_commit": "write",
    "git_pull": "write",
    "git_push": "write",
    "git_checkout": "write",
    # Delete operations
    "delete_file": "delete",
}


class PermissionEnforcedFilesystem:
    """Wrapper that validates permissions and normalizes paths before
    delegating to the underlying FilesystemBackend."""

    def __init__(self, backend: FilesystemBackend,
                 permissions: FilesystemPermissions):
        self._backend = backend
        self._permissions = permissions

    @property
    def supports_git(self) -> bool:
        return self._backend.supports_git

    def _check(self, path: str, operation: str) -> str:
        """Normalize path and check permissions. Returns normalized path.

        Raises:
            PermissionError: If the operation is denied.
        """
        normed = _normalize_path(path)
        category = _OP_CATEGORY.get(operation, "read")
        if not self._permissions.check(normed, category):
            raise PermissionError(
                f"Permission denied: {operation} on '{normed}' "
                f"(mode={self._permissions.mode})"
            )
        return normed

    # ── Basic operations ──

    def list_dir(self, path: str = ".") -> List[FilesystemEntry]:
        p = self._check(path, "list_dir")
        return self._backend.list_dir(p)

    def read_file(self, path: str) -> bytes:
        p = self._check(path, "read_file")
        return self._backend.read_file(p)

    def write_file(self, path: str, content: bytes) -> None:
        p = self._check(path, "write_file")
        self._backend.write_file(p, content)

    def delete_file(self, path: str) -> None:
        p = self._check(path, "delete_file")
        self._backend.delete_file(p)

    def mkdir(self, path: str) -> None:
        p = self._check(path, "mkdir")
        self._backend.mkdir(p)

    def stat(self, path: str) -> FilesystemEntry:
        p = self._check(path, "stat")
        return self._backend.stat(p)

    def exists(self, path: str) -> bool:
        p = self._check(path, "exists")
        return self._backend.exists(p)

    # ── Advanced operations ──

    def search(self, path: str, pattern: str, recursive: bool = True) -> List[str]:
        p = self._check(path, "search")
        return self._backend.search(p, pattern, recursive)

    def grep(self, path: str, regex: str, recursive: bool = True) -> List[Dict[str, Any]]:
        p = self._check(path, "grep")
        return self._backend.grep(p, regex, recursive)

    def find_replace(self, path: str, pattern: str, replacement: str) -> Dict[str, Any]:
        p = self._check(path, "find_replace")
        return self._backend.find_replace(p, pattern, replacement)

    # ── Git operations ──

    def git_status(self, path: str = ".") -> Dict[str, Any]:
        p = self._check(path, "git_status")
        return self._backend.git_status(p)

    def git_log(self, path: str = ".", count: int = 10) -> List[Dict[str, Any]]:
        p = self._check(path, "git_log")
        return self._backend.git_log(p, count)

    def git_diff(self, path: str = ".", ref: str = "") -> str:
        p = self._check(path, "git_diff")
        return self._backend.git_diff(p, ref)

    def git_commit(self, path: str = ".", message: str = "") -> Dict[str, Any]:
        p = self._check(path, "git_commit")
        return self._backend.git_commit(p, message)

    def git_pull(self, path: str = ".") -> Dict[str, Any]:
        p = self._check(path, "git_pull")
        return self._backend.git_pull(p)

    def git_push(self, path: str = ".") -> Dict[str, Any]:
        p = self._check(path, "git_push")
        return self._backend.git_push(p)

    def git_checkout(self, path: str = ".", ref: str = "") -> Dict[str, Any]:
        p = self._check(path, "git_checkout")
        return self._backend.git_checkout(p, ref)

    def close(self) -> None:
        self._backend.close()
