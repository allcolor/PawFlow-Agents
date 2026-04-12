"""FileStore — Thread-safe file storage with scoped buckets.

Files are stored under:
    data/runtime/files/{user_id}/{conv_id}/{bucket}/{file_id}_{filename}

Buckets hold up to 50 files, then a new one is created.
Empty buckets are deleted on cleanup.

URLs: /files/{file_id} — the server resolves file_id to disk path via the index.

Access levels:
    private       — owner + agents of the conversation (default)
    shared        — owner + named users in shared_with list
    authenticated — any authenticated user on the server
    gateway_key   — anyone with the per-file HMAC key (no login needed)
    public        — anyone (no auth, no gateway)
"""

import hashlib
import hmac
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

import core.paths as _paths

BUCKET_MAX = 50
ACCESS_PRIVATE = "private"
ACCESS_SHARED = "shared"
ACCESS_AUTHENTICATED = "authenticated"
ACCESS_GATEWAY_KEY = "gateway_key"
ACCESS_PUBLIC = "public"
VALID_ACCESS_LEVELS = (
    ACCESS_PRIVATE, ACCESS_SHARED, ACCESS_AUTHENTICATED,
    ACCESS_GATEWAY_KEY, ACCESS_PUBLIC,
)


class FileStore:
    """Singleton file store with disk persistence."""

    _instance: Optional["FileStore"] = None
    _lock = threading.Lock()

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = Path(base_dir or str(_paths.FILES_DIR))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._store_lock = threading.RLock()
        self._loaded = False
        self._last_cleanup: float = 0.0

    @classmethod
    def instance(cls) -> "FileStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    # ── Store ────────────────────────────────────────────────────

    def store(self, filename: str, content: bytes,
              content_type: str = "application/octet-stream",
              conversation_id: str = "",
              user_id: str = "",
              ttl: int = 0,
              agent_name: str = "",
              category: str = "") -> str:
        """Store a file. Returns file_id (UUID for URL).

        user_id and conversation_id are required — every file belongs to
        a user in a conversation context.
        """
        if not user_id or not conversation_id:
            logger.warning("FileStore.store called without user_id=%r conv_id=%r for %s",
                           user_id, conversation_id, filename)
        file_id = uuid.uuid4().hex[:12]
        safe_name = Path(filename).name or "file"
        disk_name = f"{file_id}_{safe_name}"

        scope_dir = self._scope_dir(user_id, conversation_id)
        bucket = self._pick_bucket(scope_dir)
        bucket_dir = scope_dir / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)

        file_path = bucket_dir / disk_name
        file_path.write_bytes(content)

        with self._store_lock:
            self._ensure_loaded()
            self._entries[file_id] = {
                "filename": safe_name,
                "path": str(file_path),
                "content_type": content_type,
                "size": len(content),
                "created_at": time.time(),
                "conversation_id": conversation_id,
                "user_id": user_id,
                "access": ACCESS_PRIVATE,
                "shared_with": [],
                "ttl": ttl,
                "agent_name": agent_name,
                "category": category,
            }

        self._save_index()
        return file_id

    # ── Retrieve ─────────────────────────────────────────────────

    def get(self, file_id: str, user_id: str = "",
            gateway_key: str = "") -> Optional[Tuple[str, bytes, str]]:
        """Retrieve a file by ID with access control.

        Returns (filename, bytes, content_type) or None if not found/denied.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if entry is None:
                return None

        # TTL check
        if entry.get("ttl", 0) > 0:
            if time.time() - entry.get("created_at", 0) > entry["ttl"]:
                self._delete_entry(file_id)
                return None

        # Access control based on level
        if not self.check_access(file_id, user_id=user_id,
                                  gateway_key=gateway_key):
            return None

        try:
            content = Path(entry["path"]).read_bytes()
            return (entry["filename"], content, entry["content_type"])
        except FileNotFoundError:
            self._delete_entry(file_id)
            return None

    def check_access(self, file_id: str, user_id: str = "",
                     gateway_key: str = "") -> bool:
        """Check if access is allowed for the given credentials.

        Returns True if access granted, False otherwise.
        """
        with self._store_lock:
            entry = self._entries.get(file_id)
        if entry is None:
            return False

        access = entry.get("access", ACCESS_PRIVATE)

        if access == ACCESS_PUBLIC:
            return True

        if access == ACCESS_GATEWAY_KEY:
            expected = self._derive_gateway_key(file_id)
            return gateway_key and hmac.compare_digest(gateway_key, expected)

        if access == ACCESS_AUTHENTICATED:
            return bool(user_id)

        if access == ACCESS_SHARED:
            if not user_id:
                return False
            owner = entry.get("user_id", "")
            if owner == user_id:
                return True
            return user_id in entry.get("shared_with", [])

        # ACCESS_PRIVATE — owner only (empty owner = no restriction)
        owner = entry.get("user_id", "")
        if not owner:
            return True
        return user_id == owner

    def get_access_level(self, file_id: str) -> str:
        """Get the access level of a file."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
        if entry is None:
            return ""
        return entry.get("access", ACCESS_PRIVATE)

    def set_access(self, file_id: str, level: str,
                   shared_with: Optional[List[str]] = None,
                   owner_user_id: str = "") -> bool:
        """Change file access level. Only the owner can change access.

        Returns True if changed, False if not found or not owner.
        """
        if level not in VALID_ACCESS_LEVELS:
            raise ValueError(f"Invalid access level: {level}")
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if not entry:
                return False
            owner = entry.get("user_id", "")
            if owner_user_id and owner and owner != owner_user_id:
                return False
            entry["access"] = level
            if shared_with is not None:
                entry["shared_with"] = list(shared_with)
        self._save_index()
        return True

    def get_share_url(self, file_id: str, base_url: str = "") -> str:
        """Get the shareable URL for a file.

        For gateway_key files, includes ?k= parameter.
        For others, returns the plain URL.
        """
        with self._store_lock:
            entry = self._entries.get(file_id)
        if not entry:
            return ""
        url = f"{base_url}/files/{file_id}"
        if entry.get("access") == ACCESS_GATEWAY_KEY:
            key = self._derive_gateway_key(file_id)
            url += f"?k={key}"
        return url

    def _derive_gateway_key(self, file_id: str) -> str:
        """Derive a per-file gateway key using HMAC."""
        from core.secrets import get_secrets_manager
        secret = get_secrets_manager()._key
        return hmac.new(secret, f"file:{file_id}".encode(),
                        hashlib.sha256).hexdigest()[:32]

    def get_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get file metadata without reading content."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if entry is None:
                return None
            return {
                "file_id": file_id,
                "filename": entry["filename"],
                "content_type": entry["content_type"],
                "size": entry["size"],
                "created_at": entry.get("created_at", 0),
                "user_id": entry.get("user_id", ""),
                "conversation_id": entry.get("conversation_id", ""),
            }

    def find_by_name(self, filename: str, user_id: str = "") -> Optional[str]:
        """Find the most recent file_id matching a filename."""
        with self._store_lock:
            self._ensure_loaded()
            for fid, entry in self._entries.items():
                if not self._accessible(entry, user_id):
                    continue
                if entry["filename"] == filename:
                    return fid
            for fid, entry in self._entries.items():
                if not self._accessible(entry, user_id):
                    continue
                if filename in entry["filename"] or entry["filename"] in filename:
                    return fid
        return None

    def exists(self, file_id: str) -> bool:
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if entry is None:
                return False
            return Path(entry.get("path", "")).exists()

    # ── Delete ───────────────────────────────────────────────────

    def delete(self, file_id: str, user_id: str = "") -> bool:
        """Delete a file by ID."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._entries.get(file_id)
            if not entry:
                return False
            owner = entry.get("user_id", "")
            if user_id and owner and owner != user_id:
                return False
        self._delete_entry(file_id)
        return True

    def delete_by(self, category: str = "", conversation_id: str = "",
                  agent_name: str = "") -> int:
        """Delete all files matching filters (AND logic). Returns count."""
        to_delete = []
        with self._store_lock:
            self._ensure_loaded()
            for fid, entry in self._entries.items():
                if category and entry.get("category") != category:
                    continue
                if conversation_id and entry.get("conversation_id") != conversation_id:
                    continue
                if agent_name and entry.get("agent_name") != agent_name:
                    continue
                to_delete.append(fid)
        for fid in to_delete:
            self._delete_entry(fid)
        return len(to_delete)

    def _delete_entry(self, file_id: str):
        """Remove a file from index + disk. Cleans empty bucket."""
        with self._store_lock:
            entry = self._entries.pop(file_id, None)
        if entry:
            file_path = Path(entry["path"])
            try:
                file_path.unlink(missing_ok=True)
                # Clean empty bucket dir
                bucket_dir = file_path.parent
                if bucket_dir.is_dir() and not any(bucket_dir.iterdir()):
                    bucket_dir.rmdir()
            except Exception:
                pass
            self._save_index()

    # ── List ─────────────────────────────────────────────────────

    # Internal categories hidden from user file listing
    _INTERNAL_CATEGORIES = frozenset({
        "tool_result", "checkpoint", "checkpoint_bin", "checkpoint_diff",
    })

    def list_files(self, user_id: str = "",
                   conversation_id: str = "",
                   include_internal: bool = False) -> List[Dict[str, Any]]:
        """List files accessible to user.

        By default, internal files (tool_result, checkpoint) are hidden.
        Use include_internal=True to see them.
        """
        result = []
        with self._store_lock:
            self._ensure_loaded()
            for fid, entry in self._entries.items():
                if not self._accessible(entry, user_id):
                    continue
                if conversation_id and entry.get("conversation_id") != conversation_id:
                    continue
                if not include_internal and entry.get("category") in self._INTERNAL_CATEGORIES:
                    continue
                result.append({
                    "file_id": fid,
                    "filename": entry["filename"],
                    "content_type": entry["content_type"],
                    "size": entry["size"],
                    "created_at": entry["created_at"],
                    "user_id": entry.get("user_id", ""),
                    "conversation_id": entry.get("conversation_id", ""),
                    "access": entry.get("access", ACCESS_PRIVATE),
                    "category": entry.get("category", ""),
                })
        return result

    def list_by_category(self, category: str,
                         conversation_id: str = "") -> List[Dict[str, Any]]:
        """List files matching category."""
        result = []
        with self._store_lock:
            self._ensure_loaded()
            for fid, entry in self._entries.items():
                if entry.get("category") != category:
                    continue
                if conversation_id and entry.get("conversation_id") != conversation_id:
                    continue
                result.append({"id": fid, **entry})
        return result

    # ── Share ────────────────────────────────────────────────────


    def count(self) -> int:
        with self._store_lock:
            self._ensure_loaded()
            return len(self._entries)

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup_expired(self, max_age_hours: int = 24):
        """Remove files past TTL or global max age."""
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        to_delete = []
        with self._store_lock:
            for fid, entry in list(self._entries.items()):
                created = entry.get("created_at", 0)
                if not created:
                    continue
                ttl = entry.get("ttl", 0)
                if ttl > 0 and (now - created) > ttl:
                    to_delete.append(fid)
                elif created < cutoff:
                    to_delete.append(fid)
        for fid in to_delete:
            self._delete_entry(fid)
        if to_delete:
            logger.info("FileStore: cleaned up %d expired files", len(to_delete))

    # ── Internal ─────────────────────────────────────────────────

    def _scope_dir(self, user_id: str = "", conversation_id: str = "") -> Path:
        """Scope directory for a file."""
        if user_id and conversation_id:
            return self._base_dir / user_id / conversation_id
        # Fallback for system-generated files (no user context)
        if user_id:
            return self._base_dir / user_id / "_system"
        return self._base_dir / "_system"

    def _pick_bucket(self, scope_dir: Path) -> str:
        """Find or create a bucket with room (< BUCKET_MAX files)."""
        if not scope_dir.is_dir():
            return "0000"
        buckets = sorted(
            [d.name for d in scope_dir.iterdir()
             if d.is_dir() and d.name.isdigit()])
        if not buckets:
            return "0000"
        last = buckets[-1]
        last_dir = scope_dir / last
        count = sum(1 for _ in last_dir.iterdir()) if last_dir.is_dir() else 0
        if count < BUCKET_MAX:
            return last
        return f"{int(last) + 1:04d}"

    @staticmethod
    def _accessible(entry: Dict, user_id: str) -> bool:
        if not user_id:
            return True
        owner = entry.get("user_id", "")
        if not owner or owner == user_id:
            return True
        return user_id in entry.get("shared_with", [])

    def _ensure_loaded(self):
        if self._loaded:
            now = time.time()
            if now - self._last_cleanup > 3600:
                self._last_cleanup = now
                self.cleanup_expired()
            return
        self._loaded = True
        self._load_index()
        self._last_cleanup = time.time()

    def _index_path(self) -> Path:
        return self._base_dir / "_index.json"

    def _save_index(self):
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
                        "user_id": e.get("user_id", ""),
                        "access": e.get("access", ACCESS_PRIVATE),
                        "shared_with": e.get("shared_with", []),
                        "ttl": e.get("ttl", 0),
                        "agent_name": e.get("agent_name", ""),
                        "category": e.get("category", ""),
                    }
                    for fid, e in self._entries.items()
                }
            path = self._index_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.error("FileStore: failed to save index: %s", e)

    def _load_index(self):
        path = self._index_path()
        if not path.exists():
            self._rebuild_index()
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for fid, entry in data.items():
                if Path(entry.get("path", "")).exists():
                    self._entries[fid] = entry
            if self._entries:
                logger.info("FileStore: loaded %d files from index",
                            len(self._entries))
            else:
                self._rebuild_index()
        except Exception as e:
            logger.warning("FileStore: index load failed, rebuilding: %s", e)
            self._rebuild_index()

    def _rebuild_index(self):
        """Rebuild index by scanning disk for {file_id}_{filename} pattern."""
        loaded = 0
        import mimetypes
        for file_path in self._base_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.name.startswith("_"):
                continue
            # Parse {file_id}_{filename}
            name = file_path.name
            if "_" not in name:
                continue
            file_id = name.split("_", 1)[0]
            if len(file_id) != 12:
                continue
            original_name = name[13:]  # after {file_id}_
            content_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
            # Determine user_id and conv_id from path
            rel = file_path.relative_to(self._base_dir)
            parts = rel.parts  # e.g. ('user1', 'conv1', '0000', 'abc_test.txt')
            user_id = ""
            conv_id = ""
            if len(parts) >= 4 and parts[0] != "_shared":
                user_id = parts[0]
                conv_id = parts[1] if parts[1] != "_unscoped" else ""

            self._entries[file_id] = {
                "filename": original_name,
                "path": str(file_path),
                "content_type": content_type,
                "size": file_path.stat().st_size,
                "created_at": file_path.stat().st_mtime,
                "conversation_id": conv_id,
                "user_id": user_id,
                "shared_with": [],
                "ttl": 0,
                "agent_name": "",
                "category": "",
            }
            loaded += 1
        if loaded:
            logger.info("FileStore: rebuilt index with %d files", loaded)
            self._save_index()
