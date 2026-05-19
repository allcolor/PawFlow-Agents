"""File Tracking Service — tracks which files have already been processed.

Prevents reprocessing of files that have already been seen by listFiles
or listSFTP tasks. Supports multiple tracking strategies.

Config:
    strategy: str       — "lastModified" | "md5" | "both" (default "lastModified")
    storage_path: str   — path to tracking database (JSON file, default "file_tracking.json")
    max_entries: int    — max tracked files before pruning oldest (default 100000)
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Set

from core.base_service import BaseService

logger = logging.getLogger(__name__)


class FileTrackingService(BaseService):
    """Tracks files to avoid reprocessing."""

    TYPE = "fileTracking"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "strategy": {"type": "select", "required": False, "default": "lastModified", "options": ["lastModified", "md5", "both"], "description": "Tracking strategy"},
            "storage_path": {"type": "string", "required": False, "default": "file_tracking.json", "description": "Path to tracking database"},
            "max_entries": {"type": "integer", "required": False, "default": 100000, "description": "Max tracked files before pruning"},
        }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._strategy = self.config.get("strategy", "lastModified")
        self._storage_path = self.config.get("storage_path", "file_tracking.json")
        self._max_entries = int(self.config.get("max_entries", 100000))
        self._tracked: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _create_connection(self):
        """Load tracking state from disk."""
        if os.path.exists(self._storage_path):
            try:
                with open(self._storage_path) as f:
                    self._tracked = json.load(f)
                logger.info(f"FileTracking loaded {len(self._tracked)} entries from {self._storage_path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"FileTracking could not load state: {e}")
                self._tracked = {}
        return True

    def _close_connection(self):
        """Save tracking state to disk."""
        self._save()

    def _save(self):
        """Persist tracking state."""
        with self._lock:
            try:
                with open(self._storage_path, "w") as f:
                    json.dump(self._tracked, f)
            except OSError as e:
                logger.error(f"FileTracking save failed: {e}")

    def _make_key(self, path: str) -> str:
        """Normalize path for consistent tracking."""
        return os.path.normpath(os.path.abspath(path))

    def is_new(self, path: str, mtime: float = 0, size: int = 0,
               content: Optional[bytes] = None) -> bool:
        """Check if a file is new or has changed since last processing.

        Args:
            path: File path (local or remote)
            mtime: Last modification timestamp
            size: File size in bytes
            content: File content (for MD5 strategy, optional)

        Returns:
            True if the file should be processed (new or changed)
        """
        key = self._make_key(path)

        with self._lock:
            entry = self._tracked.get(key)
            if entry is None:
                return True  # Never seen

            if self._strategy in ("lastModified", "both"):
                if mtime > entry.get("mtime", 0):
                    return True
                if size != entry.get("size", 0):
                    return True

            if self._strategy in ("md5", "both") and content is not None:
                md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
                if md5 != entry.get("md5", ""):
                    return True

            return False

    def mark_processed(self, path: str, mtime: float = 0, size: int = 0,
                       content: Optional[bytes] = None, md5: Optional[str] = None):
        """Mark a file as processed.

        Args:
            path: File path
            mtime: Last modification timestamp
            size: File size
            content: File content (optional, for MD5 computation)
            md5: Pre-computed MD5 hash (optional)
        """
        key = self._make_key(path)
        computed_md5 = md5
        if computed_md5 is None and content is not None:
            computed_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()

        with self._lock:
            self._tracked[key] = {
                "mtime": mtime,
                "size": size,
                "md5": computed_md5 or "",
                "processed_at": time.time(),
            }

            # Prune if over max entries (remove oldest)
            if len(self._tracked) > self._max_entries:
                sorted_keys = sorted(
                    self._tracked.keys(),
                    key=lambda k: self._tracked[k].get("processed_at", 0),
                )
                for old_key in sorted_keys[:len(self._tracked) - self._max_entries]:
                    del self._tracked[old_key]

        # Auto-save periodically (every 100 marks)
        if len(self._tracked) % 100 == 0:
            self._save()

    def reset(self, path: Optional[str] = None):
        """Reset tracking state.

        Args:
            path: If provided, reset only this path. Otherwise reset all.
        """
        with self._lock:
            if path:
                key = self._make_key(path)
                self._tracked.pop(key, None)
            else:
                self._tracked.clear()
        self._save()

    def get_tracked_count(self) -> int:
        """Number of tracked files."""
        return len(self._tracked)

    def get_tracked_paths(self) -> Set[str]:
        """Get all tracked file paths."""
        with self._lock:
            return set(self._tracked.keys())


# Auto-register
from core import ServiceFactory
ServiceFactory.register(FileTrackingService)
