"""ConversationStore — Thread-safe conversation persistence with TTL.

Used by agentLoop to maintain multi-turn conversations across HTTP requests.
Each conversation is identified by a conversation_id and has a configurable TTL.

The conversation history is append-only: new messages are appended atomically
and never overwritten by concurrent threads.  Context compaction (for the LLM
context window) is done on a *copy* by the agent loop — the canonical history
stored here is never compacted.

Persists conversations to JSON files on disk so they survive restarts.

Each conversation entry has a ``status`` field for autonomous agent tracking:
- ``idle``    — normal chat, no autonomous work in progress
- ``active``  — agent is working or has pending work
- ``complete``— agent finished all work
- ``blocked`` — agent is stuck and needs user input
"""

# Valid conversation statuses
CONV_STATUSES = ("idle", "active", "complete", "blocked")

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/conversations"


class ConversationStore:
    """Singleton store for agent conversations with disk persistence."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._conversations: Dict[str, Dict[str, Any]] = {}
        self._deleted: set = set()  # explicitly deleted conversation IDs
        self._store_lock = threading.Lock()
        self._store_dir = Path(store_dir or _DEFAULT_DIR)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = False

    @classmethod
    def instance(cls) -> "ConversationStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self):
        """Load conversations from disk on first access."""
        if self._loaded:
            return
        self._loaded = True
        self._load_from_disk()

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def load(self, conversation_id: str,
             user_id: str = "") -> Optional[List[Dict[str, Any]]]:
        """Load conversation messages. Returns None if not found, expired, or
        if *user_id* is given and doesn't match the conversation owner."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return None
            if entry["expires_at"] > 0 and entry["expires_at"] < time.time():
                del self._conversations[conversation_id]
                self._delete_from_disk(conversation_id)
                return None
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return None  # access denied
            # Return a copy so callers can't corrupt the canonical history
            return list(entry["messages"])

    def save(self, conversation_id: str, messages: List[Dict[str, Any]],
             ttl: int = 0, user_id: str = "", status: str = ""):
        """Replace conversation messages (full overwrite).

        Prefer append_messages() for incremental updates from agent loops.

        Args:
            ttl: Time to live in seconds. 0 = no expiry (default).
            user_id: Owner of the conversation (e.g. OAuth principal).
                     If empty, the conversation has no access restriction.
            status: Conversation status (idle/active/complete/blocked).
                    Empty string preserves existing status.
        """
        with self._store_lock:
            self._ensure_loaded()
            if conversation_id in self._deleted:
                logger.info(f"ConversationStore: refusing to save deleted conversation "
                            f"{conversation_id}")
                return
            existing = self._conversations.get(conversation_id)
            entry = {
                "messages": messages,
                "user_id": user_id or (existing["user_id"] if existing else ""),
                "status": status or (existing.get("status", "idle") if existing else "idle"),
                "created_at": existing["created_at"] if existing else time.time(),
                "expires_at": time.time() + ttl if ttl > 0 else 0,
                "updated_at": time.time(),
                "context_summary": existing.get("context_summary", "") if existing else "",
            }
            self._conversations[conversation_id] = entry
        self._save_to_disk(conversation_id, entry)

    def append_messages(self, conversation_id: str,
                        new_messages: List[Dict[str, Any]],
                        ttl: int = 0, user_id: str = "",
                        status: str = ""):
        """Atomically append messages to the canonical conversation history.

        If the conversation doesn't exist yet, it is created.
        This is the preferred method for agent loops — it never overwrites
        existing messages, only appends.

        Args:
            new_messages: Messages to append (list of dicts with role/content).
            ttl: Time to live in seconds. 0 = no expiry (default).
            user_id: Owner of the conversation.
            status: Conversation status update. Empty = preserve existing.
        """
        if not new_messages:
            return
        with self._store_lock:
            self._ensure_loaded()
            if conversation_id in self._deleted:
                logger.info(f"ConversationStore: refusing to append to deleted "
                            f"conversation {conversation_id}")
                return
            entry = self._conversations.get(conversation_id)
            if entry is None:
                entry = {
                    "messages": [],
                    "user_id": user_id,
                    "status": status or "idle",
                    "created_at": time.time(),
                    "expires_at": time.time() + ttl if ttl > 0 else 0,
                    "updated_at": time.time(),
                    "context_summary": "",
                }
                self._conversations[conversation_id] = entry
            entry["messages"].extend(new_messages)
            entry["updated_at"] = time.time()
            if user_id:
                entry["user_id"] = user_id
            if status:
                entry["status"] = status
            if ttl > 0:
                entry["expires_at"] = time.time() + ttl
            # Snapshot for disk write (under lock, so consistent)
            disk_entry = {
                "messages": list(entry["messages"]),
                "user_id": entry["user_id"],
                "status": entry.get("status", "idle"),
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
                "expires_at": entry["expires_at"],
                "context_summary": entry.get("context_summary", ""),
            }
            if entry.get("extra"):
                disk_entry["extra"] = entry["extra"]
        self._save_to_disk(conversation_id, disk_entry)

    def message_count(self, conversation_id: str) -> int:
        """Return the current number of messages in a conversation."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            return len(entry["messages"]) if entry else 0

    def set_status(self, conversation_id: str, status: str,
                   user_id: str = "") -> bool:
        """Update conversation status. Returns True if updated.

        Args:
            status: One of idle, active, complete, blocked.
            user_id: If given, must match owner.
        """
        if status not in CONV_STATUSES:
            return False
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return False
            entry["status"] = status
            entry["updated_at"] = time.time()
            disk_entry = {
                "messages": list(entry["messages"]),
                "user_id": entry["user_id"],
                "status": entry["status"],
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
                "expires_at": entry["expires_at"],
            }
        self._save_to_disk(conversation_id, disk_entry)
        return True

    def get_metadata(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Return conversation metadata without loading all messages.

        Returns dict with user_id, status, created_at, updated_at, expires_at,
        message_count.  Returns None if not found or expired.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return None
            if entry["expires_at"] > 0 and entry["expires_at"] < time.time():
                return None
            return {
                "user_id": entry.get("user_id", ""),
                "status": entry.get("status", "idle"),
                "created_at": entry.get("created_at", 0),
                "updated_at": entry.get("updated_at", 0),
                "expires_at": entry.get("expires_at", 0),
                "message_count": len(entry.get("messages", [])),
            }

    def set_context_summary(self, conversation_id: str, summary: str) -> bool:
        """Store the LLM context summary for a conversation.

        This is the compacted text that replaces old messages when the
        context window is too large.  Persisted to disk so that after a
        restart the context can be rebuilt without re-summarizing.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            entry["context_summary"] = summary
            entry["updated_at"] = time.time()
            disk_entry = dict(entry, messages=list(entry["messages"]))
        self._save_to_disk(conversation_id, disk_entry)
        return True

    def get_context_summary(self, conversation_id: str) -> str:
        """Return the stored context summary (empty string if none)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return ""
            return entry.get("context_summary", "")

    def set_extra(self, conversation_id: str, key: str, value: Any) -> bool:
        """Store arbitrary extra data on a conversation (plan, state, etc.)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            extra = entry.setdefault("extra", {})
            extra[key] = value
            self._save_to_disk(conversation_id, entry)
            return True

    def get_extra(self, conversation_id: str, key: str,
                  default: Any = None) -> Any:
        """Retrieve extra data stored on a conversation."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return default
            return entry.get("extra", {}).get(key, default)

    def delete_message(self, conversation_id: str, index: int,
                       user_id: str = "") -> bool:
        """Delete a single message by index. Returns True if deleted."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return False
            msgs = entry["messages"]
            if index < 0 or index >= len(msgs):
                return False
            msgs.pop(index)
            entry["updated_at"] = time.time()
            self._save_to_disk(conversation_id, entry)
            return True

    def delete(self, conversation_id: str, user_id: str = "") -> bool:
        """Delete a conversation. Returns True if deleted.

        If *user_id* is given, only deletes if it matches the owner.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return False
            del self._conversations[conversation_id]
            self._deleted.add(conversation_id)
        self._delete_from_disk(conversation_id)
        return True

    def list_conversations(self, user_id: str = "") -> List[Dict[str, Any]]:
        """List active (non-expired) conversations, optionally filtered by user_id."""
        now = time.time()
        result = []
        with self._store_lock:
            self._ensure_loaded()
            for cid, entry in self._conversations.items():
                if entry["expires_at"] > 0 and entry["expires_at"] < now:
                    continue
                if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                    continue
                # Build a preview from the first user message
                preview = ""
                for msg in entry.get("messages", []):
                    if msg.get("role") == "user":
                        preview = msg.get("content", "")[:80]
                        break
                result.append({
                    "conversation_id": cid,
                    "preview": preview,
                    "message_count": len(entry["messages"]),
                    "status": entry.get("status", "idle"),
                    "user_id": entry.get("user_id", ""),
                    "created_at": entry.get("created_at", entry["updated_at"]),
                    "updated_at": entry["updated_at"],
                    "expires_at": entry["expires_at"],
                })
        # Most recent first
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def cleanup(self) -> int:
        """Remove expired conversations. Returns count removed."""
        now = time.time()
        removed = 0
        with self._store_lock:
            self._ensure_loaded()
            expired = [cid for cid, e in self._conversations.items()
                       if e["expires_at"] > 0 and e["expires_at"] < now]
            for cid in expired:
                del self._conversations[cid]
                self._delete_from_disk(cid)
                removed += 1
        if removed:
            logger.info(f"ConversationStore: cleaned up {removed} expired conversations")
        return removed

    def count(self) -> int:
        with self._store_lock:
            self._ensure_loaded()
            return len(self._conversations)

    # -- Disk persistence --

    def _conv_path(self, conversation_id: str) -> Path:
        """Path for a conversation's JSON file."""
        # Sanitize conversation_id for filesystem safety
        safe_id = "".join(c for c in conversation_id if c.isalnum() or c in "-_")
        return self._store_dir / f"{safe_id}.json"

    def _save_to_disk(self, conversation_id: str, entry: Dict[str, Any]):
        """Persist a conversation to disk."""
        # Re-check _deleted to avoid race with concurrent delete()
        if conversation_id in self._deleted:
            return
        try:
            path = self._conv_path(conversation_id)
            data = {
                "conversation_id": conversation_id,
                "user_id": entry.get("user_id", ""),
                "status": entry.get("status", "idle"),
                "created_at": entry.get("created_at", 0),
                "updated_at": entry.get("updated_at", 0),
                "expires_at": entry.get("expires_at", 0),
                "messages": entry.get("messages", []),
                "context_summary": entry.get("context_summary", ""),
            }
            if entry.get("extra"):
                data["extra"] = entry["extra"]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.error(f"ConversationStore: failed to save {conversation_id}: {e}")

    def _delete_from_disk(self, conversation_id: str):
        """Remove a conversation file from disk.

        Also removes any .tmp file that might have been written concurrently.
        """
        try:
            path = self._conv_path(conversation_id)
            tmp = path.with_suffix(".tmp")
            existed = path.exists()
            path.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
            if existed:
                logger.info(f"ConversationStore: deleted {conversation_id} from disk")
            # Brief sleep + re-check to catch concurrent _save_to_disk race
            import time as _time
            _time.sleep(0.05)
            if path.exists():
                path.unlink(missing_ok=True)
                logger.warning(f"ConversationStore: re-deleted {conversation_id} "
                               f"(concurrent write race)")
        except Exception as e:
            logger.error(f"ConversationStore: failed to delete {conversation_id}: {e}")

    def _load_from_disk(self):
        """Load all conversations from disk on startup."""
        if not self._store_dir.exists():
            return
        now = time.time()
        loaded = 0
        expired = 0
        for path in self._store_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cid = data.get("conversation_id", path.stem)
                # Skip expired (expires_at=0 means no expiry)
                exp = data.get("expires_at", 0)
                if exp > 0 and exp < now:
                    path.unlink(missing_ok=True)
                    expired += 1
                    continue
                entry = {
                    "messages": data.get("messages", []),
                    "user_id": data.get("user_id", ""),
                    "status": data.get("status", "idle"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "expires_at": data.get("expires_at", 0),
                    "context_summary": data.get("context_summary", ""),
                }
                if data.get("extra"):
                    entry["extra"] = data["extra"]
                self._conversations[cid] = entry
                loaded += 1
            except Exception as e:
                logger.warning(f"ConversationStore: failed to load {path.name}: {e}")
        if loaded or expired:
            logger.info(f"ConversationStore: loaded {loaded} conversations from disk"
                        f" ({expired} expired removed)")
