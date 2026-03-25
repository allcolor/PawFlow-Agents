"""ConversationStore v2 — JSONL append-only with single commit point.

Each conversation is a .jsonl file with one JSON object per line.
ALL writes go through _commit(). ALL reads go through _read_lines().
Per-conversation locks ensure atomicity.

Line types:
  {"t":"meta", "user_id":"...", "status":"idle", "created_at":N, "expires_at":N}
  {"t":"msg", "role":"user", "content":"...", "msg_id":"...", "source":{}, "ts":N}
  {"t":"msg", "role":"assistant", "content":"...", "msg_id":"...", "source":{}, "private":true, "ts":N}
  {"t":"ctx", "agent":"name", "op":"replace", "data":[...]}
  {"t":"ctx", "agent":"name", "op":"append", "data":[...]}
  {"t":"extra", "key":"...", "value":...}
  {"t":"status", "status":"active"}
  {"t":"delete_msg", "index":N}

Transcript = all t=msg lines where private!=true (append-only, never modified)
Agent context = last t=ctx replace + subsequent appends for that agent
Extras = last value per key
"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/conversations"

# Valid conversation statuses
CONV_STATUSES = ("idle", "active", "complete", "blocked")


class ConversationStore:
    """Singleton JSONL-based conversation store. Thread-safe, append-only."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or _DEFAULT_DIR)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        # Per-conversation locks (one lock per conv, all ops serialized)
        self._conv_locks: Dict[str, threading.Lock] = {}
        self._conv_locks_lock = threading.Lock()
        # Lightweight cache: meta + extras + message_count + agent list
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
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
        with cls._lock:
            cls._instance = None

    # ── Lock management ───────────────────────────────────────────────

    def _get_conv_lock(self, conversation_id: str) -> threading.Lock:
        with self._conv_locks_lock:
            if conversation_id not in self._conv_locks:
                self._conv_locks[conversation_id] = threading.Lock()
            return self._conv_locks[conversation_id]

    # ── Path ──────────────────────────────────────────────────────────

    def _conv_path(self, conversation_id: str) -> Path:
        safe_id = "".join(c for c in conversation_id if c.isalnum() or c in "-_:")
        safe_id = safe_id.replace(":", "__")
        return self._store_dir / f"{safe_id}.jsonl"

    # ── SINGLE write point ────────────────────────────────────────────

    def _commit(self, conversation_id: str, lines: List[dict]) -> None:
        """THE ONLY method that writes to disk. Lock, append, release."""
        if not lines:
            return
        lock = self._get_conv_lock(conversation_id)
        with lock:
            path = self._conv_path(conversation_id)
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for line in lines:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.error(f"[convstore] write failed {conversation_id}: {e}")
                raise
        # Update cache
        self._update_cache(conversation_id, lines)

    # ── SINGLE read point ─────────────────────────────────────────────

    def _read_lines_unlocked(self, conversation_id: str,
                              filter_fn: Optional[Callable[[dict], bool]] = None
                              ) -> Iterator[dict]:
        """Read lines WITHOUT locking (caller must hold the lock)."""
        path = self._conv_path(conversation_id)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if filter_fn is None or filter_fn(obj):
                        yield obj
        except OSError as e:
            logger.error(f"[convstore] read failed {conversation_id}: {e}")

    def _read_lines(self, conversation_id: str,
                    filter_fn: Optional[Callable[[dict], bool]] = None
                    ) -> Iterator[dict]:
        """Read lines with lock. For public API methods."""
        lock = self._get_conv_lock(conversation_id)
        with lock:
            yield from self._read_lines_unlocked(conversation_id, filter_fn)

    def _read_lines_reversed(self, conversation_id: str,
                              filter_fn: Optional[Callable[[dict], bool]] = None,
                              max_lines: int = 0) -> List[dict]:
        """Read lines from end of file (for finding last ctx replace etc.)."""
        lock = self._get_conv_lock(conversation_id)
        path = self._conv_path(conversation_id)
        if not path.exists():
            return []
        results = []
        with lock:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()  # TODO: optimize with seek from end
                for raw_line in reversed(all_lines):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if filter_fn is None or filter_fn(obj):
                        results.append(obj)
                        if max_lines and len(results) >= max_lines:
                            break
            except OSError:
                pass
        results.reverse()
        return results

    # ── Cache management ──────────────────────────────────────────────

    def _update_cache(self, conversation_id: str, new_lines: List[dict]) -> None:
        with self._cache_lock:
            c = self._cache.setdefault(conversation_id, {
                "user_id": "", "status": "idle", "created_at": 0,
                "updated_at": 0, "expires_at": 0, "msg_count": 0,
                "agents": set(), "extra_keys": set(),
            })
            for line in new_lines:
                t = line.get("t", "")
                if t == "meta":
                    c["user_id"] = line.get("user_id", c["user_id"])
                    c["status"] = line.get("status", c["status"])
                    c["created_at"] = line.get("created_at", c["created_at"])
                    c["expires_at"] = line.get("expires_at", c["expires_at"])
                elif t == "msg" and not line.get("private"):
                    c["msg_count"] = c.get("msg_count", 0) + 1
                elif t == "ctx":
                    agent = line.get("agent", "")
                    if agent:
                        c["agents"].add(agent)
                elif t == "extra":
                    c["extra_keys"].add(line.get("key", ""))
                elif t == "status":
                    c["status"] = line.get("status", c["status"])
                c["updated_at"] = time.time()

    def _load_cache(self, conversation_id: str) -> dict:
        """Load or return cached metadata."""
        with self._cache_lock:
            if conversation_id in self._cache:
                return self._cache[conversation_id]
        # Build cache by scanning file
        c = {
            "user_id": "", "status": "idle", "created_at": 0,
            "updated_at": 0, "expires_at": 0, "msg_count": 0,
            "agents": set(), "extra_keys": set(),
        }
        for line in self._read_lines(conversation_id):
            t = line.get("t", "")
            if t == "meta":
                c["user_id"] = line.get("user_id", "")
                c["status"] = line.get("status", "idle")
                c["created_at"] = line.get("created_at", 0)
                c["expires_at"] = line.get("expires_at", 0)
            elif t == "msg" and not line.get("private"):
                c["msg_count"] += 1
            elif t == "ctx":
                agent = line.get("agent", "")
                if agent:
                    c["agents"].add(agent)
            elif t == "extra":
                c["extra_keys"].add(line.get("key", ""))
            elif t == "status":
                c["status"] = line.get("status", c["status"])
            c["updated_at"] = line.get("ts", c["updated_at"])
        with self._cache_lock:
            self._cache[conversation_id] = c
        return c

    def _ensure_loaded(self):
        """Scan all .jsonl files to build cache on first access."""
        if self._loaded:
            return
        self._loaded = True
        count = 0
        for path in self._store_dir.glob("*.jsonl"):
            cid = path.stem.replace("__", ":")
            self._load_cache(cid)
            count += 1
        if count:
            logger.info(f"ConversationStore: loaded {count} conversations from disk")

    # ── Public API: IDs ───────────────────────────────────────────────

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def exists(self, conversation_id: str) -> bool:
        return self._conv_path(conversation_id).exists()

    # ── Public API: Create / Save ─────────────────────────────────────

    def save(self, conversation_id: str, messages: List[Dict[str, Any]],
             ttl: int = 0, user_id: str = "", status: str = ""):
        """Create or overwrite a conversation. Writes meta + all messages."""
        lines = [{"t": "meta", "user_id": user_id, "status": status or "idle",
                  "created_at": time.time(),
                  "expires_at": time.time() + ttl if ttl > 0 else 0}]
        for m in messages:
            line = {"t": "msg", **m}
            if "ts" not in line and "timestamp" not in line:
                line["ts"] = time.time()
            lines.append(line)
        # Overwrite = delete existing file first
        path = self._conv_path(conversation_id)
        lock = self._get_conv_lock(conversation_id)
        with lock:
            if path.exists():
                path.unlink()
            with self._cache_lock:
                self._cache.pop(conversation_id, None)
        self._commit(conversation_id, lines)

    # ── Public API: Agent flush (THE main write operation) ────────────

    def agent_flush(self, conversation_id: str, agent_name: str,
                    public_messages: List[Dict[str, Any]],
                    private_messages: List[Dict[str, Any]],
                    user_id: str = "", ttl: int = 0) -> None:
        """Atomic: append public to transcript + all contexts,
        append all to agent context. Single _commit call.

        public_messages: user + assistant TEXT → transcript + all contexts
        private_messages: tool_calls + tool results → agent context only
        """
        now = time.time()
        lines: List[dict] = []

        # Ensure conversation exists
        if not self.exists(conversation_id):
            lines.append({"t": "meta", "user_id": user_id,
                          "status": "idle", "created_at": now,
                          "expires_at": now + ttl if ttl > 0 else 0})

        # 1. Transcript: public messages (not private)
        for m in public_messages:
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)

        # 2. Private messages (tool calls, tool results) — transcript with private flag
        for m in private_messages:
            line = {"t": "msg", "private": True, **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)

        # 3. Agent context: append all (public + private)
        all_agent = public_messages + private_messages
        if all_agent:
            lines.append({"t": "ctx", "agent": agent_name, "op": "append",
                          "data": all_agent})

        # 4. Other agents' contexts: append public only
        if public_messages:
            cache = self._load_cache(conversation_id)
            for other in cache.get("agents", set()):
                if other and other != agent_name:
                    lines.append({"t": "ctx", "agent": other, "op": "append",
                                  "data": public_messages})

        self._commit(conversation_id, lines)

    # ── Public API: Append messages (simple, for non-agent callers) ───

    def append_messages(self, conversation_id: str,
                        new_messages: List[Dict[str, Any]],
                        ttl: int = 0, user_id: str = "",
                        status: str = "") -> None:
        """Append messages to transcript. No context update."""
        if not new_messages:
            return
        now = time.time()
        lines: List[dict] = []
        if not self.exists(conversation_id):
            lines.append({"t": "meta", "user_id": user_id,
                          "status": status or "idle", "created_at": now,
                          "expires_at": now + ttl if ttl > 0 else 0})
        for m in new_messages:
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)
        if status:
            lines.append({"t": "status", "status": status})
        self._commit(conversation_id, lines)

    # ── Public API: Context operations ────────────────────────────────

    def load_agent_context(self, conversation_id: str,
                           agent_name: str) -> Optional[List[Dict[str, Any]]]:
        """Load per-agent context: last replace + subsequent appends."""
        if not self.exists(conversation_id):
            return None
        # Scan for ctx lines for this agent
        replace_data = None
        appends: List[List[dict]] = []
        found_replace = False
        # Read all ctx lines, keep last replace + appends after it
        for line in self._read_lines(conversation_id,
                                      lambda l: l.get("t") == "ctx" and l.get("agent") == agent_name):
            if line.get("op") == "replace":
                replace_data = line.get("data", [])
                appends = []  # reset appends after each replace
                found_replace = True
            elif line.get("op") == "append" and found_replace:
                appends.append(line.get("data", []))
        if replace_data is None and not found_replace:
            return None  # no context for this agent
        result = list(replace_data or [])
        for batch in appends:
            result.extend(batch)
        return result

    def save_agent_context(self, conversation_id: str,
                           agent_name: str,
                           context_messages: List[Dict[str, Any]]) -> bool:
        """Replace agent context (compact/rebuild). Atomic. Auto-vacuums.

        Automatically merges any new transcript messages that arrived
        between when the caller loaded the context and now. This prevents
        message loss during long-running compaction.
        """
        if not self.exists(conversation_id):
            return False

        # Load the CURRENT agent context to find new appends since caller read it
        lock = self._get_conv_lock(conversation_id)
        with lock:
            # Read existing ctx for this agent (under lock)
            current_ctx = []
            for line in self._read_lines_unlocked(conversation_id,
                                                   lambda l: l.get("t") == "ctx" and l.get("agent") == agent_name):
                if line.get("op") == "replace":
                    current_ctx = list(line.get("data", []))
                elif line.get("op") == "append":
                    current_ctx.extend(line.get("data", []))

            # Find messages in current ctx that are NOT in the replacement
            # (these were appended between when caller read and now)
            replacement_ids = set()
            for m in context_messages:
                mid = m.get("msg_id", "")
                if mid:
                    replacement_ids.add(mid)
            missed = []
            for m in current_ctx:
                mid = m.get("msg_id", "")
                if mid and mid not in replacement_ids:
                    missed.append(m)
            if missed:
                context_messages = list(context_messages) + missed
                logger.info(f"[convstore] save_agent_context: merged "
                            f"{len(missed)} new message(s) into replacement context")

            # Write under the SAME lock
            path = self._conv_path(conversation_id)
            line = {"t": "ctx", "agent": agent_name or "", "op": "replace",
                    "data": context_messages}
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.error(f"[convstore] write failed: {e}")
                return False
            self._update_cache(conversation_id, [line])

        # Auto-vacuum: the replace makes all prior ctx lines obsolete
        try:
            self.vacuum(conversation_id)
        except Exception as e:
            logger.debug(f"[convstore] auto-vacuum failed: {e}")
        return True

    def append_to_agent_context(self, conversation_id: str,
                                agent_name: str,
                                new_messages: List[Dict[str, Any]]) -> bool:
        """Append to agent context. Creates context if it doesn't exist."""
        if not self.exists(conversation_id):
            return False
        self._commit(conversation_id, [
            {"t": "ctx", "agent": agent_name, "op": "append",
             "data": new_messages}
        ])
        return True

    # ── Public API: Read transcript ───────────────────────────────────

    def load(self, conversation_id: str,
             user_id: str = "") -> Optional[List[Dict[str, Any]]]:
        """Load transcript messages (public only). Returns None if not found."""
        if not self.exists(conversation_id):
            return None
        if user_id:
            cache = self._load_cache(conversation_id)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        messages = []
        for line in self._read_lines(conversation_id,
                                      lambda l: l.get("t") == "msg" and not l.get("private")):
            # Convert to message dict (strip JSONL metadata)
            msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
            if "ts" in line:
                msg["timestamp"] = line["ts"]
            messages.append(msg)
        return messages

    def load_page(self, conversation_id: str, limit: int = 50, offset: int = 0,
                  user_id: str = "") -> Optional[Dict[str, Any]]:
        """Load a page of transcript messages from the end."""
        all_msgs = self.load(conversation_id, user_id=user_id)
        if all_msgs is None:
            return None
        total = len(all_msgs)
        end_idx = total - offset
        start_idx = max(0, end_idx - limit)
        # Don't split tool_call from tool_results
        if start_idx > 0:
            while start_idx > 0:
                msg = all_msgs[start_idx]
                if msg.get("role") == "tool":
                    start_idx -= 1
                else:
                    break
        page = all_msgs[start_idx:end_idx]
        return {
            "messages": page,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": start_idx > 0,
        }

    def message_count(self, conversation_id: str) -> int:
        cache = self._load_cache(conversation_id)
        return cache.get("msg_count", 0)

    # ── Public API: Metadata ──────────────────────────────────────────

    def get_metadata(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        if not self.exists(conversation_id):
            return None
        cache = self._load_cache(conversation_id)
        return {
            "user_id": cache.get("user_id", ""),
            "status": cache.get("status", "idle"),
            "created_at": cache.get("created_at", 0),
            "updated_at": cache.get("updated_at", 0),
            "expires_at": cache.get("expires_at", 0),
            "message_count": cache.get("msg_count", 0),
        }

    def set_status(self, conversation_id: str, status: str,
                   user_id: str = "") -> bool:
        if status not in CONV_STATUSES:
            return False
        if not self.exists(conversation_id):
            return False
        self._commit(conversation_id, [{"t": "status", "status": status}])
        return True

    # ── Public API: Extras ────────────────────────────────────────────

    def get_extra(self, conversation_id: str, key: str,
                  default: Any = None, user_id: str = "") -> Any:
        """Get the latest value for an extra key (scans from end)."""
        if not self.exists(conversation_id):
            return default
        result = default
        for line in self._read_lines(conversation_id,
                                      lambda l: l.get("t") == "extra" and l.get("key") == key):
            result = line.get("value", default)
        return result

    def get_extras(self, conversation_id: str, user_id: str = "") -> Optional[dict]:
        """Get all extras (latest value per key)."""
        if not self.exists(conversation_id):
            return None
        extras = {}
        for line in self._read_lines(conversation_id,
                                      lambda l: l.get("t") == "extra"):
            extras[line["key"]] = line.get("value")
        return extras

    def set_extra(self, conversation_id: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        if not self.exists(conversation_id):
            return False
        self._commit(conversation_id, [{"t": "extra", "key": key, "value": value}])
        return True

    # ── Public API: Delete ────────────────────────────────────────────

    def delete(self, conversation_id: str, user_id: str = "") -> bool:
        if not self.exists(conversation_id):
            return False
        lock = self._get_conv_lock(conversation_id)
        with lock:
            path = self._conv_path(conversation_id)
            path.unlink(missing_ok=True)
        with self._cache_lock:
            self._cache.pop(conversation_id, None)
        # Cascade delete sub-conversations
        prefix = f"{conversation_id}::task::"
        for path in self._store_dir.glob("*.jsonl"):
            cid = path.stem.replace("__", ":")
            if cid.startswith(prefix):
                sub_lock = self._get_conv_lock(cid)
                with sub_lock:
                    path.unlink(missing_ok=True)
                with self._cache_lock:
                    self._cache.pop(cid, None)
        return True

    def delete_message(self, conversation_id: str, index: int,
                       user_id: str = "") -> bool:
        """Mark a message as deleted (append a delete_msg line)."""
        self._commit(conversation_id, [{"t": "delete_msg", "index": index}])
        return True

    # ── Public API: List ──────────────────────────────────────────────

    def list_conversations(self, user_id: str = "") -> List[Dict[str, Any]]:
        self._ensure_loaded()
        result = []
        with self._cache_lock:
            for cid, c in self._cache.items():
                if ":task:" in cid:
                    continue
                if user_id and c.get("user_id") and c["user_id"] != user_id:
                    continue
                if c.get("expires_at", 0) > 0 and c["expires_at"] < time.time():
                    continue
                result.append({
                    "conversation_id": cid,
                    "preview": "",  # lazy — don't scan file for preview
                    "message_count": c.get("msg_count", 0),
                    "status": c.get("status", "idle"),
                    "user_id": c.get("user_id", ""),
                    "created_at": c.get("created_at", 0),
                    "updated_at": c.get("updated_at", 0),
                    "expires_at": c.get("expires_at", 0),
                })
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def list_agent_contexts(self, conversation_id: str) -> Dict[str, str]:
        cache = self._load_cache(conversation_id)
        result = {"*": "messages"}
        for agent in cache.get("agents", set()):
            result[agent] = "diverged"
        return result

    # ── Public API: Context compatibility ─────────────────────────────
    # These exist for backward compat with callers that use save_context/load_context

    def save_context(self, conversation_id: str,
                     context_messages: List[Dict[str, Any]]) -> bool:
        """Save shared context (agent_name="")."""
        return self.save_agent_context(conversation_id, "", context_messages)

    def load_context(self, conversation_id: str,
                     user_id: str = "") -> Optional[List[Dict[str, Any]]]:
        """Load shared context."""
        return self.load_agent_context(conversation_id, "")

    # ── Display trace (sub-agent) ─────────────────────────────────────

    def create_display_trace(self, conversation_id: str, trace_id: str,
                             source: Dict[str, Any],
                             user_id: str = "") -> bool:
        self._commit(conversation_id, [{
            "t": "msg", "role": "sub_agent_trace", "display_only": True,
            "trace_id": trace_id, "source": source, "content": "",
            "trace": [], "ts": time.time(),
        }])
        return True

    def append_display_trace(self, conversation_id: str, trace_id: str,
                             entry_data: Dict[str, Any],
                             content_update: str = "") -> bool:
        """Append to display trace — reads existing trace, appends, writes update."""
        # This is the ONE case where we need read-modify-write
        # We append a trace_update line that the reader merges
        entry_data.setdefault("ts", time.time())
        self._commit(conversation_id, [{
            "t": "trace_update", "trace_id": trace_id,
            "entry": entry_data,
            "content_update": content_update,
        }])
        return True

    # ── Vacuum (compact file, remove obsolete lines) ───────────────────

    def vacuum(self, conversation_id: str) -> dict:
        """Rewrite the JSONL file, removing obsolete lines.

        Removes:
        - ctx append/replace lines before the LAST replace per agent
        - extra lines superseded by a later extra with the same key
        - status lines superseded by a later status
        - delete_msg markers (applied to messages)

        Like PostgreSQL VACUUM — reclaims space without changing semantics.
        """
        lock = self._get_conv_lock(conversation_id)
        path = self._conv_path(conversation_id)
        if not path.exists():
            return {"status": "not_found"}

        with lock:
            # Read all lines
            lines = []
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for raw in f:
                        raw = raw.strip()
                        if raw:
                            try:
                                lines.append(json.loads(raw))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                return {"status": "read_error"}

            before = len(lines)

            # Find last ctx replace per agent
            last_replace_idx: Dict[str, int] = {}
            for i, line in enumerate(lines):
                if line.get("t") == "ctx" and line.get("op") == "replace":
                    last_replace_idx[line.get("agent", "")] = i

            # Find last extra per key
            last_extra_idx: Dict[str, int] = {}
            for i, line in enumerate(lines):
                if line.get("t") == "extra":
                    last_extra_idx[line.get("key", "")] = i

            # Find last status
            last_status_idx = -1
            for i, line in enumerate(lines):
                if line.get("t") == "status":
                    last_status_idx = i

            # Find deleted message indices
            deleted_indices = set()
            for line in lines:
                if line.get("t") == "delete_msg":
                    deleted_indices.add(line.get("index", -1))

            # Filter
            kept = []
            msg_idx = 0
            for i, line in enumerate(lines):
                t = line.get("t", "")
                if t == "ctx":
                    agent = line.get("agent", "")
                    if agent in last_replace_idx and i < last_replace_idx[agent]:
                        continue  # obsolete ctx line
                elif t == "extra":
                    key = line.get("key", "")
                    if key in last_extra_idx and i < last_extra_idx[key]:
                        continue  # superseded extra
                elif t == "status":
                    if i < last_status_idx:
                        continue  # superseded status
                elif t == "delete_msg":
                    continue  # marker consumed
                elif t == "msg" and not line.get("private"):
                    if msg_idx in deleted_indices:
                        msg_idx += 1
                        continue  # deleted message
                    msg_idx += 1
                kept.append(line)

            after = len(kept)
            if after == before:
                return {"status": "clean", "lines": before}

            # Rewrite atomically
            tmp = path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    for line in kept:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                tmp.replace(path)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                return {"status": "write_error", "error": str(e)}

            # Rebuild cache
            with self._cache_lock:
                self._cache.pop(conversation_id, None)
            self._load_cache(conversation_id)

            return {"status": "vacuumed", "before": before, "after": after,
                    "removed": before - after}

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self) -> int:
        """Remove expired conversations. Returns count removed."""
        self._ensure_loaded()
        removed = 0
        now = time.time()
        with self._cache_lock:
            expired = [cid for cid, c in self._cache.items()
                       if c.get("expires_at", 0) > 0 and c["expires_at"] < now]
        for cid in expired:
            self.delete(cid)
            removed += 1
        return removed

    def count(self) -> int:
        self._ensure_loaded()
        with self._cache_lock:
            return len(self._cache)

    # ── Compat helpers ────────────────────────────────────────────────

    @staticmethod
    def filter_display_only(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [m for m in messages if not (isinstance(m, dict) and m.get("display_only"))]

    def set_metadata_field(self, conversation_id: str, field: str, value: Any) -> bool:
        """Set a metadata field (status, user_id, etc.)."""
        return self.set_extra(conversation_id, field, value)
