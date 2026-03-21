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
        self._write_locks: Dict[str, threading.Lock] = {}  # per-conversation write serialization
        self._write_locks_lock = threading.Lock()  # protects _write_locks dict itself
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

    _last_cleanup: float = 0.0

    def _ensure_loaded(self):
        """Load conversations from disk on first access."""
        if self._loaded:
            # Periodic lightweight cleanup (at most once per hour)
            now = time.time()
            if now - self.__class__._last_cleanup > 3600:
                self.__class__._last_cleanup = now
                self._cleanup_expired()
            return
        self._loaded = True
        self._load_from_disk()
        self.__class__._last_cleanup = time.time()

    def _cleanup_expired(self):
        """Lightweight removal of expired entries from memory (no disk I/O).

        Called periodically from _ensure_loaded under _store_lock.
        """
        now = time.time()
        expired = [cid for cid, e in self._conversations.items()
                   if e["expires_at"] > 0 and e["expires_at"] < now]
        for cid in expired:
            self._conversations.pop(cid, None)
        if expired:
            logger.info("ConversationStore: cleaned up %d expired conversations "
                        "(memory)", len(expired))

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

    def load_page(self, conversation_id: str, limit: int = 50, offset: int = 0,
                  user_id: str = "") -> Optional[Dict[str, Any]]:
        """Load a page of messages from the end of conversation.

        Args:
            limit: max messages to return (default 50)
            offset: skip this many messages from the end (0 = most recent)
            user_id: access control

        Returns:
            {messages: [...], total_count: int, offset: int, limit: int, has_more: bool}
            or None if not found/denied
        """
        all_messages = self.load(conversation_id, user_id=user_id)
        if all_messages is None:
            return None

        total = len(all_messages)

        # Slice from the end: offset=0 means last `limit` messages
        end_idx = total - offset
        start_idx = max(0, end_idx - limit)

        # Boundary safety: don't split tool_call from its tool_results
        # If start_idx lands on a "tool" role message, extend backward to include
        # the preceding assistant message with tool_calls
        if start_idx > 0:
            while start_idx > 0:
                msg = all_messages[start_idx]
                role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
                if role == "tool":
                    start_idx -= 1  # include the tool_call that triggered this result
                else:
                    break

        page = all_messages[start_idx:end_idx]
        has_more = start_idx > 0

        return {
            "messages": page,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

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
                "context": existing.get("context") if existing else None,
            }
            self._conversations[conversation_id] = entry
        self._save_to_disk(conversation_id)

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
                    "context": None,
                }
                self._conversations[conversation_id] = entry
            now = time.time()
            for msg in new_messages:
                if "timestamp" not in msg:
                    msg["timestamp"] = now
            entry["messages"].extend(new_messages)
            entry["updated_at"] = time.time()
            if user_id:
                entry["user_id"] = user_id
            if status:
                entry["status"] = status
            if ttl > 0:
                entry["expires_at"] = time.time() + ttl
        self._save_to_disk(conversation_id)

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
        self._save_to_disk(conversation_id)
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

    def load_context(self, conversation_id: str,
                     user_id: str = "") -> Optional[List[Dict[str, Any]]]:
        """Return the persisted LLM context, or None if not yet diverged.

        When context is None the caller should fall back to using messages.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return None
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return None
            ctx = entry.get("context")
            if ctx is None:
                return None
            return list(ctx)

    def save_context(self, conversation_id: str,
                     context_messages: List[Dict[str, Any]]) -> bool:
        """Overwrite the persisted LLM context (after compact/resume/restart_from/rebuild)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            entry["context"] = list(context_messages)
            entry["updated_at"] = time.time()
        self._save_to_disk(conversation_id)
        return True

    def append_to_context(self, conversation_id: str,
                          new_messages: List[Dict[str, Any]]) -> bool:
        """Append to the persisted context. No-op if context is None (not diverged)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if entry.get("context") is None:
                return False
            entry["context"].extend(new_messages)
            entry["updated_at"] = time.time()
        self._save_to_disk(conversation_id)
        return True

    # ── Per-agent context ──────────────────────────────────────────

    def load_agent_context(self, conversation_id: str,
                           agent_name: str) -> Optional[List[Dict[str, Any]]]:
        """Load per-agent context, falling back to shared context then messages.

        Resolution order:
        1. agent_contexts[agent_name] (if exists and not None)
        2. entry["context"] (shared diverged context)
        3. None (caller should use messages)
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return None
            # 1. Per-agent context
            agent_ctxs = entry.get("agent_contexts") or {}
            agent_ctx = agent_ctxs.get(agent_name)
            if agent_ctx is not None:
                return list(agent_ctx)
            # 2. Shared context
            shared = entry.get("context")
            if shared is not None:
                return list(shared)
            # 3. Not diverged
            return None

    def save_agent_context(self, conversation_id: str,
                           agent_name: str,
                           context_messages: List[Dict[str, Any]]) -> bool:
        """Save diverged context for a specific agent.

        If agent_name is empty, saves to the shared context (backward compat).
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if not agent_name:
                entry["context"] = list(context_messages)
            else:
                agent_ctxs = entry.setdefault("agent_contexts", {})
                agent_ctxs[agent_name] = list(context_messages)
            entry["updated_at"] = time.time()
        self._save_to_disk(conversation_id)
        return True

    def append_to_agent_context(self, conversation_id: str,
                                agent_name: str,
                                new_messages: List[Dict[str, Any]]) -> bool:
        """Append to an agent's diverged context.

        If the agent has its own context → append there.
        If shared context exists → fork it for this agent, then append.
        If no context diverged → no-op (returns False).
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            agent_ctxs = entry.get("agent_contexts") or {}
            agent_ctx = agent_ctxs.get(agent_name)
            if agent_ctx is not None:
                # Agent has its own context — append
                agent_ctx.extend(new_messages)
                entry["updated_at"] = time.time()
            elif entry.get("context") is not None:
                # Shared context exists — fork for this agent
                agent_ctxs = entry.setdefault("agent_contexts", {})
                agent_ctxs[agent_name] = list(entry["context"]) + list(new_messages)
                entry["updated_at"] = time.time()
            else:
                return False
        self._save_to_disk(conversation_id)
        return True

    def list_agent_contexts(self, conversation_id: str) -> Dict[str, str]:
        """Return {agent_name: status} where status is 'diverged', 'shared', or 'messages'.

        Also includes '*' for the shared context status.
        """
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return {}
            result = {}
            has_shared = entry.get("context") is not None
            result["*"] = "diverged" if has_shared else "messages"
            agent_ctxs = entry.get("agent_contexts") or {}
            for name, ctx in agent_ctxs.items():
                result[name] = "diverged" if ctx is not None else (
                    "shared" if has_shared else "messages"
                )
            return result

    def set_extra(self, conversation_id: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        """Store arbitrary extra data on a conversation (plan, state, etc.)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            # Access control
            entry_owner = entry.get("user_id", "")
            if user_id and entry_owner and entry_owner != user_id:
                return False
            extra = entry.setdefault("extra", {})
            extra[key] = value
        self._save_to_disk(conversation_id)
        return True

    def get_extra(self, conversation_id: str, key: str,
                  default: Any = None, user_id: str = "") -> Any:
        """Retrieve extra data stored on a conversation."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return default
            # Access control
            entry_owner = entry.get("user_id", "")
            if user_id and entry_owner and entry_owner != user_id:
                return default
            return entry.get("extra", {}).get(key, default)

    def get_extras(self, conversation_id: str, user_id: str = "") -> Optional[dict]:
        """Get all extras for a conversation. Returns None if not found or access denied."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if not entry:
                return None
            # Access control
            entry_owner = entry.get("user_id", "")
            if user_id and entry_owner and entry_owner != user_id:
                return None
            return dict(entry.get("extra", {}))

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
        self._save_to_disk(conversation_id)
        return True

    def delete(self, conversation_id: str, user_id: str = "") -> bool:
        """Delete a conversation. Returns True if deleted.

        If *user_id* is given, only deletes if it matches the owner.
        Also cascade-deletes sub-conversations (task contexts) that start
        with ``{conversation_id}::task::``.
        """
        sub_ids: list = []
        with self._store_lock:
            self._ensure_loaded()
            entry = self._conversations.get(conversation_id)
            if entry is None:
                return False
            if user_id and entry.get("user_id") and entry["user_id"] != user_id:
                return False
            del self._conversations[conversation_id]
            self._deleted.add(conversation_id)
            # Cascade delete sub-conversations (task contexts)
            prefix = f"{conversation_id}::task::"
            sub_ids = [cid for cid in self._conversations
                       if cid.startswith(prefix)]
            for sub_id in sub_ids:
                self._conversations.pop(sub_id, None)
                self._deleted.add(sub_id)
        self._delete_from_disk(conversation_id)
        for sub_id in sub_ids:
            self._delete_from_disk(sub_id)
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
                # Hide sub-conversations (task contexts)
                if ":task:" in cid:
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
        safe_id = "".join(c for c in conversation_id if c.isalnum() or c in "-_:")
        # ':' is invalid in Windows file paths — replace with '__'
        safe_id = safe_id.replace(":", "__")
        return self._store_dir / f"{safe_id}.json"

    def _get_write_lock(self, conversation_id: str) -> threading.Lock:
        """Get or create a per-conversation write lock."""
        with self._write_locks_lock:
            if conversation_id not in self._write_locks:
                self._write_locks[conversation_id] = threading.Lock()
            return self._write_locks[conversation_id]

    def _save_to_disk(self, conversation_id: str, entry: Dict[str, Any] = None):
        """Persist a conversation to disk (serialized per conversation).

        If *entry* is None, re-reads the current in-memory state under lock
        to guarantee the latest version is written.  When *entry* is provided
        it is used as-is (caller already holds a consistent snapshot).
        """
        if conversation_id in self._deleted:
            return
        write_lock = self._get_write_lock(conversation_id)
        with write_lock:
            if conversation_id in self._deleted:
                return
            # Re-snapshot from memory to guarantee latest state
            if entry is None:
                with self._store_lock:
                    mem = self._conversations.get(conversation_id)
                    if mem is None:
                        return
                    entry = {
                        "messages": list(mem.get("messages", [])),
                        "user_id": mem.get("user_id", ""),
                        "status": mem.get("status", "idle"),
                        "created_at": mem.get("created_at", 0),
                        "updated_at": mem.get("updated_at", 0),
                        "expires_at": mem.get("expires_at", 0),
                        "context": list(mem["context"]) if mem.get("context") is not None else None,
                    }
                    if mem.get("agent_contexts"):
                        entry["agent_contexts"] = {
                            k: list(v) if v is not None else None
                            for k, v in mem["agent_contexts"].items()
                        }
                    if mem.get("extra"):
                        entry["extra"] = dict(mem["extra"])
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
                    "context": entry.get("context"),
                }
                if entry.get("agent_contexts"):
                    data["agent_contexts"] = entry["agent_contexts"]
                if entry.get("extra"):
                    data["extra"] = entry["extra"]
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
            except OSError as e:
                logger.error("ConversationStore: failed to save %s to disk: %s",
                             conversation_id, e)
                # Try to free space by cleaning up expired conversations
                try:
                    with self._store_lock:
                        self._cleanup_expired()
                except Exception:
                    pass
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
                    "context": data.get("context"),  # None for old files = not diverged
                }
                if data.get("agent_contexts"):
                    entry["agent_contexts"] = data["agent_contexts"]
                if data.get("extra"):
                    entry["extra"] = data["extra"]
                self._conversations[cid] = entry
                loaded += 1
            except Exception as e:
                logger.warning(f"ConversationStore: failed to load {path.name}: {e}")
        if loaded or expired:
            logger.info(f"ConversationStore: loaded {loaded} conversations from disk"
                        f" ({expired} expired removed)")
