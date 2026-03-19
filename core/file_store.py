"""FileStore — Thread-safe temporary file storage with TTL and download support.

Used by agent tools to create downloadable files. Files are stored in a
persistent directory with an index file so they survive restarts.
Expired files are cleaned up automatically.
"""

import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/files"


class FileStore:
    """Singleton file store with TTL and disk persistence."""

    _instance: Optional["FileStore"] = None
    _lock = threading.Lock()

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = os.path.abspath(base_dir or _DEFAULT_DIR)
        os.makedirs(self._base_dir, exist_ok=True)
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._store_lock = threading.RLock()
        self._loaded = False

    @classmethod
    def instance(cls) -> "FileStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


    def _ensure_loaded(self):
        """Load index from disk on first access."""
        if self._loaded:
            return
        self._loaded = True
        self._migrate_from_temp()
        self._load_index()

    def _migrate_from_temp(self):
        """One-time migration from old tempdir-based storage."""
        import tempfile
        old_dir = os.path.join(tempfile.gettempdir(), "pyfi2_files")
        if not os.path.isdir(old_dir) or os.path.normpath(old_dir) == os.path.normpath(self._base_dir):
            return
        migrated = 0
        for item in os.listdir(old_dir):
            src = os.path.join(old_dir, item)
            dst = os.path.join(self._base_dir, item)
            if os.path.isdir(src) and not os.path.exists(dst):
                try:
                    shutil.copytree(src, dst)
                    import traceback
                    print(f"[FILE_DELETE] migrate rmtree: {src}")
                    traceback.print_stack()
                    shutil.rmtree(src, ignore_errors=True)
                    migrated += 1
                except Exception:
                    pass
        if migrated:
            logger.info(f"FileStore: migrated {migrated} files from {old_dir}")
        # Clean up old directory if empty
        try:
            if not os.listdir(old_dir):
                os.rmdir(old_dir)
        except Exception:
            pass

    def store(self, filename: str, content: bytes,
              content_type: str = "application/octet-stream",
              conversation_id: str = "") -> str:
        """Store a file and return its file_id.

        Args:
            filename: Original filename (preserved for download)
            content: File content as bytes
            content_type: MIME type

        Returns:
            file_id: Unique identifier for retrieval
        """
        file_id = uuid.uuid4().hex[:12]
        file_dir = os.path.join(self._base_dir, file_id)
        os.makedirs(file_dir, exist_ok=True)

        # Sanitize filename
        safe_name = os.path.basename(filename) or "file"
        file_path = os.path.join(file_dir, safe_name)

        with open(file_path, "wb") as f:
            f.write(content)

        with self._store_lock:
            self._ensure_loaded()
            self._entries[file_id] = {
                "filename": safe_name,
                "path": file_path,
                "content_type": content_type,
                "size": len(content),
                "created_at": time.time(),
                "conversation_id": conversation_id,
            }

        self._save_index()
        logger.info(f"FileStore: stored '{safe_name}' as {file_id} "
                    f"({len(content)} bytes)")
        return file_id

    def get(self, file_id: str) -> Optional[Tuple[str, bytes, str]]:
        """Retrieve a file by ID.

        Returns:
            (filename, content_bytes, content_type) or None if not found/expired
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if entry is None:
                return None

        try:
            with open(entry["path"], "rb") as f:
                content = f.read()
            return (entry["filename"], content, entry["content_type"])
        except FileNotFoundError:
            with self._store_lock:
                self._entries.pop(file_id, None)
            self._save_index()
            return None

    def exists(self, file_id: str) -> bool:
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if entry is None:
                return False
            # File exists if it's in the index and on disk
            return os.path.exists(entry.get("path", ""))

    def delete(self, file_id: str):
        with self._store_lock:
            self._ensure_loaded()
            self._remove_entry(file_id)
        self._save_index()

    def _remove_entry(self, file_id: str):
        """Remove entry and its files (must be called with lock held)."""
        entry = self._entries.pop(file_id, None)
        if entry:
            file_dir = os.path.dirname(entry["path"])
            import traceback
            print(f"[FILE_DELETE] _remove_entry: file_id={file_id}, dir={file_dir}")
            traceback.print_stack()
            try:
                shutil.rmtree(file_dir, ignore_errors=True)
            except Exception:
                pass

    def list_files(self) -> List[Dict[str, Any]]:
        """List all stored files."""
        result = []
        with self._store_lock:
            self._ensure_loaded()
            for fid, entry in self._entries.items():
                result.append({
                    "file_id": fid,
                    "filename": entry["filename"],
                    "content_type": entry["content_type"],
                    "size": entry["size"],
                    "created_at": entry["created_at"],
                })
        return result



    def count(self) -> int:
        with self._store_lock:
            self._ensure_loaded()
            return len(self._entries)

    # -- Disk persistence (index file) --

    def _index_path(self) -> str:
        return os.path.join(self._base_dir, "_index.json")

    def _save_index(self):
        """Persist the entries index to disk.

        Safety: refuses to overwrite a non-empty index with an empty one
        if there are actual file directories on disk (prevents accidental
        data loss from unloaded state).
        """
        try:
            with self._store_lock:
                data = {
                    fid: {
                        "filename": e["filename"],
                        "path": e["path"],
                        "content_type": e["content_type"],
                        "size": e["size"],
                        "created_at": e["created_at"],
                        "conversation_id": e.get("conversation_id", ""),
                    }
                    for fid, e in self._entries.items()
                }
                # Safety: never overwrite a populated index with empty data
                # if file directories still exist on disk
                if not data:
                    existing_dirs = [
                        d for d in Path(self._base_dir).iterdir()
                        if d.is_dir() and not d.name.startswith("_")
                    ]
                    if existing_dirs:
                        logger.warning(
                            "FileStore: refusing to save empty index — "
                            "%d file dirs still on disk. Call _rebuild_index() first.",
                            len(existing_dirs))
                        return
                path = self._index_path()
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                for _attempt in range(3):
                    try:
                        os.replace(tmp, path)
                        break
                    except OSError:
                        if _attempt < 2:
                            time.sleep(0.1)
                        else:
                            raise
        except Exception as e:
            logger.error(f"FileStore: failed to save index: {e}")

    def _load_index(self):
        """Load entries index from disk, rebuilding from directory if needed."""
        path = self._index_path()
        now = time.time()

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded = 0
                for fid, entry in data.items():
                    # Resolve path — may be relative from older versions
                    stored_path = entry.get("path", "")
                    if stored_path and not os.path.isabs(stored_path):
                        # Reconstruct absolute path from base_dir
                        stored_path = os.path.join(
                            self._base_dir, fid, entry.get("filename", ""))
                        entry["path"] = stored_path
                    # Verify file still exists
                    if os.path.exists(stored_path):
                        self._entries[fid] = entry
                        loaded += 1
                if loaded:
                    logger.info(f"FileStore: loaded {loaded} files from index")
                if loaded == 0:
                    self._rebuild_index()
                return
            except Exception as e:
                logger.warning(f"FileStore: failed to load index, rebuilding: {e}")

        # No index or failed to load — rebuild from directory structure
        self._rebuild_index()

    def _rebuild_index(self):
        """Rebuild index by scanning the file store directory."""
        loaded = 0
        base = Path(self._base_dir)
        for file_dir in base.iterdir():
            if not file_dir.is_dir() or file_dir.name.startswith("_"):
                continue
            fid = file_dir.name
            # Find the actual file inside the directory
            files = [f for f in file_dir.iterdir() if f.is_file()]
            if not files:
                import traceback
                print(f"[FILE_DELETE] _rebuild_index: empty dir removed: {file_dir}")
                traceback.print_stack()
                shutil.rmtree(file_dir, ignore_errors=True)
                continue
            actual_file = files[0]
            self._entries[fid] = {
                "filename": actual_file.name,
                "path": str(actual_file),
                "content_type": self._guess_content_type(actual_file.name),
                "size": actual_file.stat().st_size,
                "created_at": actual_file.stat().st_ctime,
                "expires_at": 0,  # no expiry for rebuilt entries
            }
            loaded += 1
        if loaded:
            logger.info(f"FileStore: rebuilt index from disk ({loaded} files)")
            self._save_index()

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        """Guess content type from filename extension."""
        ext = os.path.splitext(filename)[1].lower()
        types = {
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".html": "text/html",
            ".htm": "text/html",
            ".json": "application/json",
            ".xml": "application/xml",
            ".csv": "text/csv",
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".zip": "application/zip",
            ".py": "text/x-python",
            ".js": "text/javascript",
            ".css": "text/css",
        }
        return types.get(ext, "application/octet-stream")
