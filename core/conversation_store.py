"""ConversationStore — directory-based conversation storage.

Each conversation is a directory:
  data/conversations/{user}/{conv_id}/
    transcript.jsonl              — all messages (faithful replay)
    shared.jsonl                  — shared context (public messages for all agents)
    {agent}/context.jsonl         — per-agent LLM context
    extras.json                   — atomic JSON metadata (no duplication)

Transcript line types:
  {"t":"meta", "user_id":"...", "status":"idle", "created_at":N, "expires_at":N}
  {"t":"msg", "role":"...", "content":"...", "msg_id":"...", "source":{}, "ts":N}
  {"t":"msg", ..., "private":true}  (tool calls/results — agent context only)
  {"t":"msg_patch", "msg_id":"...", ...}
  {"t":"status", "status":"active"}
  {"t":"trace_update", "trace_id":"...", ...}

Context files ({agent}/context.jsonl, shared.jsonl):
  One message dict per line (no "t" prefix — raw messages).

Per-conversation locks ensure atomicity of logical operations.
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import core.paths as _paths


class ConversationStore:
    """Singleton JSONL conversation store. Thread-safe, append-only."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or str(_paths.CONVERSATIONS_DIR))
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._conv_locks: Dict[str, threading.Lock] = {}
        self._conv_locks_lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._ctx_cache: Dict[str, Dict[str, List[Dict]]] = {}  # cid -> {agent -> messages}
        self._ctx_cache_lock = threading.Lock()
        self._cid_user: Dict[str, str] = {}  # cid -> user_id (fast lookup, no scan)
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

    def _get_conv_lock(self, cid: str) -> threading.RLock:
        with self._conv_locks_lock:
            if cid not in self._conv_locks:
                self._conv_locks[cid] = threading.RLock()
            return self._conv_locks[cid]

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_.:@")
        return safe.replace(":", "__")

    def _conv_dir(self, cid: str, user_id: str = "") -> Path:
        """Directory for a conversation: {store_dir}/{user}/{conv_id}/"""
        if user_id:
            self._cid_user[cid] = user_id  # cache for future lookups
            return self._store_dir / self._safe_name(user_id) / self._safe_name(cid)
        # Fast lookup from cid→user mapping (populated by _ensure_loaded + save)
        uid = self._cid_user.get(cid)
        if uid:
            return self._store_dir / self._safe_name(uid) / self._safe_name(cid)
        # Fallback: scan user dirs on disk
        if self._store_dir.is_dir():
            for user_dir in self._store_dir.iterdir():
                if user_dir.is_dir():
                    conv_dir = user_dir / self._safe_name(cid)
                    if conv_dir.is_dir():
                        self._cid_user[cid] = user_dir.name  # remember for next time
                        return conv_dir
        raise ValueError(f"Conversation {cid[:16]} not found and no user_id provided")

    def _conv_path(self, cid: str) -> Path:
        """Legacy compat: points to transcript.jsonl for exists() checks."""
        return self._conv_dir(cid) / "transcript.jsonl"

    def _transcript_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "transcript.jsonl"

    def _shared_ctx_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "shared.jsonl"

    def _agent_ctx_path(self, cid: str, agent: str) -> Path:
        safe_agent = self._safe_name(agent) if agent else "_shared"
        return self._conv_dir(cid) / safe_agent / "context.jsonl"

    def _extras_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "extras.json"

    # ── Git per conversation ──────────────────────────────────────────

    def _git(self, cid: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the conversation directory."""
        conv_dir = self._conv_dir(cid)
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(conv_dir), capture_output=True, text=True,
            check=check, timeout=10,
        )

    def _git_init(self, cid: str):
        """Initialize a git repo in the conversation directory (idempotent)."""
        conv_dir = self._conv_dir(cid)
        git_dir = conv_dir / ".git"
        if git_dir.exists():
            return
        try:
            self._git(cid, "init", "-q")
            # Configure for this repo only (no user-level config needed)
            self._git(cid, "config", "user.email", "pawflow@local")
            self._git(cid, "config", "user.name", "PawFlow")
            # Initial commit with whatever exists
            self._git(cid, "add", "-A")
            self._git(cid, "commit", "-m", "init", "--allow-empty", "-q")
            logger.debug("[convstore] git init for %s", cid[:8])
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] git init failed for %s: %s", cid[:8], e)

    def git_snapshot(self, cid: str, message: str = ""):
        """Commit current state as a snapshot (called after agent turn end).

        Uses selective git add (known files only) instead of git add -A
        to avoid scanning the entire working tree on large repos.

        Uses the per-conversation lock to prevent race conditions when
        multiple agents flush simultaneously.
        """
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return
        lock = self._get_conv_lock(cid)
        with lock:
            try:
                # Selective add: transcript + shared + extras + all agent contexts
                files = ["transcript.jsonl", "shared.jsonl", "extras.json", "bindings.json"]
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and entry.name != ".git":
                        ctx = entry / "context.jsonl"
                        if ctx.exists():
                            files.append(f"{entry.name}/context.jsonl")
                self._git(cid, "add", "--", *files, check=False)
                # Commit only if something staged
                diff = self._git(cid, "diff", "--cached", "--quiet", check=False)
                if diff.returncode == 0:
                    return  # nothing staged
                msg = message or f"snapshot {time.strftime('%H:%M:%S')}"
                self._git(cid, "commit", "-m", msg, "-q")
                logger.debug("[convstore] git snapshot for %s: %s", cid[:8], msg)
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.warning("[convstore] git snapshot failed for %s: %s", cid[:8], e)

    def git_log(self, cid: str, limit: int = 20) -> List[Dict]:
        """List recent git commits for a conversation."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return []
        try:
            result = self._git(cid, "log", f"--max-count={limit}",
                               "--format=%H\t%at\t%s")
            entries = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if len(parts) >= 3:
                    entries.append({
                        "hash": parts[0],
                        "timestamp": int(parts[1]),
                        "message": parts[2],
                    })
            return entries
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return []

    def git_rollback(self, cid: str, commit_hash: str) -> bool:
        """Rollback conversation to a previous commit."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self._git(cid, "checkout", commit_hash, "--", ".")
            # Reload cache from rolled-back state
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._invalidate_ctx_cache(cid)
            self._reload_cache(cid)
            # Commit the rollback as a new snapshot
            self.git_snapshot(cid, f"rollback to {commit_hash[:8]}")
            logger.info("[convstore] rolled back %s to %s", cid[:8], commit_hash[:8])
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] rollback failed for %s: %s", cid[:8], e)
            return False

    def git_diff(self, cid: str, commit_a: str = "HEAD~1", commit_b: str = "HEAD") -> str:
        """Get diff between two commits."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return ""
        try:
            result = self._git(cid, "diff", commit_a, commit_b, check=False)
            return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""

    # ── Cross-file UUID invariant ────────────────────────────────────
    #
    # msg_id IS a UUID — universally unique by construction. A given
    # logical message is created ONCE (LLMMessage.__post_init__ mints
    # its uuid) and the same object flows through every write path via
    # dict(msg) transforms that never touch msg_id. So the invariant
    # is preserved by construction: no runtime heuristic needed.
    #
    # If the same logical content appears with two different msg_ids,
    # that's a caller bug (someone rebuilt the LLMMessage instead of
    # reusing it). Fix the caller. Do NOT try to "realign" here by
    # guessing which row is the canonical one from ts/content — the
    # msg_id IS the identity.

    # ── Context file helpers ──────────────────────────────────────────

    def _append_ctx_file(self, cid: str, agent: str, messages: List[Dict]):
        """Append messages to an agent's context file (dedup by msg_id)."""
        path = self._agent_ctx_path(cid, agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Collect existing msg_ids to avoid duplicates
        existing_ids = set()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mid = json.loads(line).get("msg_id", "")
                        if mid:
                            existing_ids.add(mid)
                    except json.JSONDecodeError:
                        pass
        with open(path, "a", encoding="utf-8") as f:
            for m in messages:
                self._validate_message(m)
                mid = m.get("msg_id", "")
                if mid in existing_ids:
                    continue  # skip duplicate
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                existing_ids.add(mid)

    @staticmethod
    def _prefix_content(content, prefix: str):
        """Prefix content with a tag. Handles both string and multipart (list) content."""
        if isinstance(content, str):
            return f"{prefix}\n{content}"
        if isinstance(content, list):
            # Multipart: prepend text block with prefix
            return [{"type": "text", "text": prefix}] + list(content)
        return f"{prefix}\n{content}"

    @staticmethod
    def _strip_prefix(content, prefix: str):
        """Strip a prefix tag from content. Handles both string and multipart (list)."""
        if isinstance(content, str):
            full = prefix + "\n"
            return content[len(full):] if content.startswith(full) else content
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text" and first.get("text") == prefix:
                return content[1:]
        return content

    @staticmethod
    def _agent_prefix(agent_name: str, source: Dict) -> str:
        """Build the [Agent X]: or [Agent X in Task Y]: or [Agent X in /btw]: prefix."""
        task_id = source.get("task_id", "")
        if task_id:
            return f"[Agent {agent_name} in Task {task_id}]:"
        if source.get("btw"):
            return f"[Agent {agent_name} in /btw]:"
        return f"[Agent {agent_name}]:"

    @staticmethod
    def _user_prefix(target: str, source: Dict) -> str:
        """Build the [User to agent X]: or [User to agent X in /btw]: prefix."""
        if source.get("btw"):
            return f"[User to agent {target} in /btw]:"
        return f"[User to agent {target}]:"

    @staticmethod
    def _transform_for_shared(msg: Dict) -> Dict:
        """Transform a message for the shared (agent-neutral) context.

        ALL messages are prefixed — shared belongs to no agent.
        - Agent messages: role→user, content prefixed [Agent X]: or [Agent X in Task Y]:
        - User messages: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE (filtered upstream
          in agent_flush). If we're called on one, return as-is rather
          than mislabel it.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — caller must not broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            m["role"] = "user"
            m["content"] = ConversationStore._prefix_content(
                m.get("content", ""), ConversationStore._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            if target:
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._user_prefix(target, src))

        return m

    @staticmethod
    def _transform_for_other_agent(msg: Dict, receiving_agent: str) -> Dict:
        """Transform a message for injection into a specific agent's context.

        - Own agent messages WITHOUT task: unchanged (role=assistant)
        - Own agent messages FROM task: prefixed [Agent X in Task Y]: (task is a sub-context)
        - Other agent messages: role→user, content prefixed [Agent X]:
        - User messages to receiving_agent: unchanged
        - User messages to other agent: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE — private A↔B
          channel, filtered upstream. Returned as-is as a safety net.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — should not be broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            is_own = (agent_name == receiving_agent)
            is_sub_context = bool(src.get("task_id")) or bool(src.get("btw"))
            # Own messages from sub-contexts (task, btw) are prefixed
            # Own messages from normal conv are NOT prefixed (role=assistant)
            if not is_own or is_sub_context:
                m["role"] = "user"
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            is_btw_msg = bool(src.get("btw"))
            # btw user messages are ALWAYS prefixed (sub-context, even for target agent)
            # Normal user messages only prefixed for non-target agents
            if target and (target != receiving_agent or is_btw_msg):
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._user_prefix(target, src))

        return m

    @staticmethod
    def _personalize_from_shared(msg: Dict, agent_name: str) -> Dict:
        """Personalize a shared-context message for a specific agent.

        Reverses _transform_for_shared for this agent's own NON-TASK messages:
        - [Agent {me}]: (no task) → strip prefix, role=assistant
        - [Agent {me} in Task Y]: → keep prefix (task = sub-context, not own response)
        - [User to agent {me}]: → strip prefix
        - Everything else: keep as-is (already prefixed for "others")
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent" and src.get("name") == agent_name:
            # Only un-prefix own messages that are NOT from a sub-context
            if not src.get("task_id") and not src.get("btw"):
                m["content"] = ConversationStore._strip_prefix(
                    m.get("content", ""), f"[Agent {agent_name}]:")
                m["role"] = "assistant"
            # Sub-context messages (task, btw) stay prefixed

        elif src_type == "user" and src.get("target_agent") == agent_name:
            m["content"] = ConversationStore._strip_prefix(
                m.get("content", ""), f"[User to agent {agent_name}]:")

        return m

    def _append_shared_ctx(self, cid: str, messages: List[Dict]):
        """Append transformed messages to the shared context file (dedup by msg_id)."""
        path = self._shared_ctx_path(cid)
        # Collect existing msg_ids to avoid duplicates
        existing_ids = set()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mid = json.loads(line).get("msg_id", "")
                        if mid:
                            existing_ids.add(mid)
                    except json.JSONDecodeError:
                        pass
        with open(path, "a", encoding="utf-8") as f:
            for m in messages:
                self._validate_message(m)
                mid = m.get("msg_id", "")
                if mid in existing_ids:
                    continue  # skip duplicate
                xf = self._transform_for_shared(m)
                f.write(json.dumps(xf, ensure_ascii=False) + "\n")
                existing_ids.add(mid)

    def _read_ctx_file(self, path: Path) -> List[Dict]:
        """Read all messages from a context JSONL file."""
        if not path.exists():
            return []
        result = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        result.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return result

    def _write_ctx_file(self, path: Path, messages: List[Dict]):
        """Overwrite a context file with messages (atomic: tmp + rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for m in messages:
                self._validate_message(m)
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        tmp.replace(path)

    def _read_extras(self, cid: str) -> dict:
        """Read extras from the atomic JSON file."""
        path = self._extras_path(cid)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _write_extras(self, cid: str, data: dict):
        """Atomically write extras JSON (tmp + rename)."""
        path = self._extras_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ══════════════════════════════════════════════════════════════════
    #  SINGLE READ POINT
    # ══════════════════════════════════════════════════════════════════

    def _read(self, cid: str, read_fn: Callable):
        """THE ONLY read method. Lock, stream file to read_fn, release."""
        lock = self._get_conv_lock(cid)
        path = self._conv_path(cid)
        with lock:
            if not path.exists():
                return read_fn(iter([]))
            try:
                with open(path, "r", encoding="utf-8") as f:
                    def _iter():
                        for raw in f:
                            raw = raw.strip()
                            if raw:
                                try:
                                    yield json.loads(raw)
                                except json.JSONDecodeError:
                                    continue
                    return read_fn(_iter())
            except OSError as e:
                logger.error(f"[convstore] read failed {cid}: {e}")
                return read_fn(iter([]))



    # ══════════════════════════════════════════════════════════════════
    #  CACHE
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _scan_cache(lines):
        c = {"user_id": "", "status": "idle", "created_at": 0,
             "updated_at": 0, "expires_at": 0, "msg_count": 0,
             "agents": set(), "extra_keys": set(), "extras": {}, "preview": ""}
        for line in lines:
            t = line.get("t", "")
            if t == "meta":
                c["user_id"] = line.get("user_id", "")
                c["status"] = line.get("status", "idle")
                c["created_at"] = line.get("created_at", 0)
                c["expires_at"] = line.get("expires_at", 0)
            elif t == "msg":
                c["msg_count"] += 1
                if not c["preview"] and line.get("role") == "user":
                    content = line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        c["preview"] = content[:80]
            elif t == "extra":
                _ekey = line.get("key", "")
                c["extra_keys"].add(_ekey)
                c["extras"][_ekey] = line.get("value")
                if _ekey == "title":
                    c["title"] = line.get("value", "")
            elif t == "status":
                c["status"] = line.get("status", c["status"])
            c["updated_at"] = max(c["updated_at"], line.get("ts", 0))
        return c

    def _load_cache(self, cid: str) -> dict:
        with self._cache_lock:
            if cid in self._cache:
                return self._cache[cid]
        return self._reload_cache(cid)

    def _reload_cache(self, cid: str) -> dict:
        """Read file from disk and atomically swap cache entry.

        Extras are loaded from the separate extras.json file (not from JSONL).
        No gap where the entry is absent — list_conversations always
        sees either the old or new value, never missing.
        """
        c = self._read(cid, self._scan_cache)
        # Merge extras from extras.json file (source of truth)
        extras_data = self._read_extras(cid)
        if extras_data:
            c["extras"] = extras_data
            c["extra_keys"] = set(extras_data.keys())
            if "title" in extras_data:
                c["title"] = extras_data["title"]
            # Use meta from extras for cache fields
            c["user_id"] = extras_data.get("_meta_user_id", c.get("user_id", ""))
            if extras_data.get("_meta_created_at"):
                c["created_at"] = max(c["created_at"], extras_data["_meta_created_at"])
                c["updated_at"] = max(c["updated_at"], extras_data["_meta_created_at"])
        # Scan agent context directories
        conv_dir = self._conv_dir(cid)
        if conv_dir.is_dir():
            for entry in conv_dir.iterdir():
                if entry.is_dir() and (entry / "context.jsonl").exists():
                    agent = entry.name.replace("__", ":")
                    if agent != "_shared":
                        c["agents"].add(agent)
        with self._cache_lock:
            self._cache[cid] = c
        return c

    def _ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:  # class-level lock (also used for singleton)
            if self._loaded:
                return
            self._loaded = True
        count = 0
        # Scan data/conversations/{user}/{conv_id}/ directories
        # First pass: populate cid→user mapping so _conv_dir never needs to scan
        for user_dir in self._store_dir.iterdir():
            if not user_dir.is_dir():
                continue
            uid = user_dir.name
            for conv_dir in user_dir.iterdir():
                if not conv_dir.is_dir():
                    continue
                if not (conv_dir / "transcript.jsonl").exists() and not (conv_dir / "extras.json").exists():
                    continue
                cid = conv_dir.name.replace("__", ":")
                self._cid_user[cid] = uid
                self._load_cache(cid)
                count += 1
        if count:
            logger.info(f"ConversationStore: loaded {count} conversations from disk")

    @staticmethod
    def _validate_message(m: Dict):
        """Every message MUST have msg_id and timestamp. No exceptions."""
        role = m.get("role", "")
        if role in ("system",):
            return  # system prompts are ephemeral, no msg_id needed
        if not m.get("msg_id"):
            raise ValueError(
                f"BUG: message without msg_id — role={role}, "
                f"content={str(m.get('content', ''))[:80]}")
        if not m.get("ts") and not m.get("timestamp"):
            raise ValueError(
                f"BUG: message without timestamp — role={role}, "
                f"msg_id={m.get('msg_id')}")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def exists(self, cid: str) -> bool:
        try:
            return self._conv_dir(cid).is_dir()
        except ValueError:
            return False

    # ── Create / Save ─────────────────────────────────────────────────

    def save(self, cid: str, messages: List[Dict], ttl: int = 0,
             user_id: str = "", status: str = ""):
        _now = time.time()
        if not user_id:
            raise ValueError("user_id is required to create a conversation")
        self._conv_dir(cid, user_id=user_id).mkdir(parents=True, exist_ok=True)

        # Write transcript
        meta_line = {"t": "meta", "user_id": user_id, "status": status or "idle",
                     "created_at": _now, "ts": _now,
                     "expires_at": _now + ttl if ttl > 0 else 0}
        with open(self._transcript_path(cid), "w", encoding="utf-8") as f:
            f.write(json.dumps(meta_line, ensure_ascii=False) + "\n")
            for m in messages:
                self._validate_message(m)
                line = {"t": "msg", **m}
                if "ts" not in line and "timestamp" not in line:
                    line["ts"] = _now
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        # Write extras with metadata
        extras = {
            "_meta_user_id": user_id,
            "_meta_created_at": _now,
            "_meta_expires_at": _now + ttl if ttl > 0 else 0,
            "_meta_status": status or "idle",
        }
        self._write_extras(cid, extras)

        # Initialize git repo
        self._git_init(cid)

        # Update cache
        self._reload_cache(cid)

    # ── Agent flush (main write op) ──────────────────────────────────

    def agent_flush(self, cid: str, agent_name: str,
                    public_messages: List[Dict],
                    private_messages: List[Dict],
                    user_id: str = "", ttl: int = 0):
        now = time.time()
        lock = self._get_conv_lock(cid)

        if not self.exists(cid):
            if not user_id:
                raise ValueError("user_id required for new conversation")
            self.save(cid, [], user_id=user_id, ttl=ttl)

        # Dedup: skip messages already in transcript
        existing_ids = self._get_transcript_msg_ids(cid)

        # Build transcript lines (public + private)
        transcript_lines = []
        for m in public_messages:
            self._validate_message(m)
            mid = m.get("msg_id")
            if mid and mid in existing_ids:
                continue
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            transcript_lines.append(line)

        for m in private_messages:
            self._validate_message(m)
            mid = m.get("msg_id")
            if mid and mid in existing_ids:
                continue
            line = {"t": "msg", "private": True, **m}
            if "ts" not in line:
                line["ts"] = now
            transcript_lines.append(line)

        with lock:
            # 1. Append to transcript
            if transcript_lines:
                with open(self._transcript_path(cid), "a", encoding="utf-8") as f:
                    for line in transcript_lines:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")

            # Filter display_only — NEVER goes into any context
            ctx_public = [m for m in public_messages if not m.get("display_only")]
            ctx_private = [m for m in private_messages if not m.get("display_only")]

            # Split out agent_delegate messages — private A↔B channel,
            # bypasses shared + other agents entirely. They still go to
            # the from/to contexts with appropriate tagging.
            _delegate_msgs = [m for m in ctx_public
                              if (m.get("source") or {}).get("type") == "agent_delegate"]
            ctx_public = [m for m in ctx_public
                          if (m.get("source") or {}).get("type") != "agent_delegate"]

            # 2. Append to agent context file (the flushing agent's own ctx)
            all_agent = ctx_public + ctx_private
            if all_agent:
                self._append_ctx_file(cid, agent_name, all_agent)

            # 2b. Route agent_delegate messages. They ARE visible to the
            #     shared transcript and to other agents' contexts (so
            #     everyone sees who delegated what), but each recipient
            #     gets a prefix adapted to its perspective:
            #       - FROM's ctx:     [delegate <from> → <to>]: ...
            #       - TO's ctx:       Voici un message de l'agent '<from>': ...
            #       - shared + others: [<from> to agent <to>]: ...
            _shared_delegate_extra = []
            for _dm in _delegate_msgs:
                _src = _dm.get("source") or {}
                _from = _src.get("from", "") or agent_name
                _to = _src.get("to", "")
                if not _to:
                    continue
                # FROM's own ctx
                _for_from = dict(_dm)
                _for_from["content"] = self._prefix_content(
                    _for_from.get("content", ""),
                    f"[delegate {_from} → {_to}]:")
                self._append_ctx_file(cid, _from, [_for_from])
                # TO's ctx — role coerced to user so the target reads it
                # as an inbound instruction, with an explicit attribution.
                _for_to = dict(_dm)
                if _for_to.get("role") == "assistant":
                    _for_to["role"] = "user"
                _kind = _src.get("kind")
                if _kind == "reply":
                    _attr = (f"Here is agent '{_from}''s reply to your "
                             f"delegate:")
                else:
                    _attr = f"Here is a message from agent '{_from}':"
                _for_to["content"] = self._prefix_content(
                    _for_to.get("content", ""), _attr)
                self._append_ctx_file(cid, _to, [_for_to])
                # Shared view: visible to everyone else in the conv.
                _for_shared = dict(_dm)
                if _for_shared.get("role") == "assistant":
                    _for_shared["role"] = "user"
                _for_shared["content"] = self._prefix_content(
                    _for_shared.get("content", ""),
                    f"[{_from} to agent {_to}]:")
                _shared_delegate_extra.append(_for_shared)

            # 3. Append to shared context + transformed to other agents' contexts
            # Shared context = conversation only — NO tool results, NO context injections.
            # Assistant messages WITH tool_calls: keep the text, strip tool_calls.
            shared_msgs = []
            for m in ctx_public:
                if m.get("role") == "tool":
                    continue  # tool results never in shared
                if (m.get("source") or {}).get("type") == "context":
                    continue  # system/context injections never in shared
                if m.get("tool_calls"):
                    # Keep the assistant text, drop tool_calls
                    m_copy = dict(m)
                    m_copy.pop("tool_calls", None)
                    m_copy.pop("tool_call_id", None)
                    # Only include if there's actual text content
                    content = m_copy.get("content", "")
                    if content and str(content).strip():
                        shared_msgs.append(m_copy)
                else:
                    shared_msgs.append(m)
            # Attach the prefixed delegate copies to the shared stream so
            # every other agent sees the routing too.
            if _shared_delegate_extra:
                shared_msgs.extend(_shared_delegate_extra)
            if shared_msgs:
                self._append_shared_ctx(cid, shared_msgs)
                cache = self._load_cache(cid)
                # Skip the delegate from/to from the "other agents"
                # broadcast — they already received their tailored copy
                # in step 2b.
                _delegate_parties = {
                    (m.get("source") or {}).get("from", "")
                    for m in _delegate_msgs
                } | {
                    (m.get("source") or {}).get("to", "")
                    for m in _delegate_msgs
                }
                for other in cache.get("agents", set()):
                    if not other or other == agent_name:
                        continue
                    if other in _delegate_parties:
                        # Already handled in step 2b with a private copy.
                        transformed = [self._transform_for_other_agent(m, other)
                                       for m in shared_msgs
                                       if m not in _shared_delegate_extra]
                    else:
                        transformed = [self._transform_for_other_agent(m, other)
                                       for m in shared_msgs]
                    self._append_ctx_file(cid, other, transformed)

        self._invalidate_ctx_cache(cid)
        self._reload_cache(cid)
        # Git snapshot after agent turn
        self.git_snapshot(cid, f"agent:{agent_name}")

    # ── Append messages (simple) ──────────────────────────────────────

    def append_messages(self, cid: str, new_messages: List[Dict],
                        ttl: int = 0, user_id: str = "", status: str = ""):
        if not new_messages:
            return
        # Dedup: skip messages whose msg_id already exists in transcript
        if self.exists(cid):
            existing_ids = self._get_transcript_msg_ids(cid)
            deduped = []
            for m in new_messages:
                mid = m.get("msg_id")
                if mid and mid in existing_ids:
                    continue  # already in transcript
                deduped.append(m)
            if not deduped:
                return
            new_messages = deduped

        now = time.time()
        lock = self._get_conv_lock(cid)

        # Create conv if needed
        if not self.exists(cid):
            if not user_id:
                raise ValueError("user_id required for new conversation")
            self.save(cid, [], user_id=user_id, ttl=ttl, status=status or "idle")

        # Build transcript lines
        transcript_lines = []
        for m in new_messages:
            self._validate_message(m)
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            transcript_lines.append(line)
        if status:
            transcript_lines.append({"t": "status", "status": status, "ts": now})

        # Filter context-eligible messages
        ctx_msgs = [m for m in new_messages
                    if not m.get("private") and not m.get("display_only")
                    and m.get("role") != "tool" and not m.get("tool_calls")]

        with lock:
            # 1. Append to transcript
            if transcript_lines:
                with open(self._transcript_path(cid), "a", encoding="utf-8") as f:
                    for line in transcript_lines:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")

            # 2. Propagate to shared + all agent contexts (with transformation)
            if ctx_msgs:
                self._append_shared_ctx(cid, ctx_msgs)
                cache = self._load_cache(cid)
                for agent in cache.get("agents", set()):
                    if agent:
                        transformed = [self._transform_for_other_agent(m, agent)
                                       for m in ctx_msgs]
                        self._append_ctx_file(cid, agent, transformed)

        self._invalidate_ctx_cache(cid)
        self._reload_cache(cid)

    def _get_transcript_msg_ids(self, cid: str) -> set:
        """Get all msg_ids from transcript lines (cached via _read)."""
        def _scan(lines):
            ids = set()
            for line in lines:
                if line.get("t") == "msg":
                    mid = line.get("msg_id")
                    if mid:
                        ids.add(mid)
            return ids
        return self._read(cid, _scan) or set()

    # ── Context ops ───────────────────────────────────────────────────

    def load_agent_context(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load agent context from {agent}/context.jsonl file.

        If agent_name is set but no context file exists, returns None
        (caller falls back to shared via load_context).
        If agent_name is empty, loads from shared.jsonl directly.
        """
        with self._ctx_cache_lock:
            if cid in self._ctx_cache and agent_name in self._ctx_cache[cid]:
                cached = self._ctx_cache[cid][agent_name]
                return list(cached) if cached is not None else None

        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        result = self._read_ctx_file(path) or None
        with self._ctx_cache_lock:
            self._ctx_cache.setdefault(cid, {})[agent_name] = result
        return result

    def load_shared_for_agent(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load shared context personalized for a specific agent.

        Shared stores agent-neutral messages (all prefixed).
        This reverses prefixes for the agent's own messages.
        """
        raw = self._read_ctx_file(self._shared_ctx_path(cid))
        if not raw:
            return None
        return [self._personalize_from_shared(m, agent_name) for m in raw]

    def _invalidate_ctx_cache(self, cid: str, agent_name: str = ""):
        with self._ctx_cache_lock:
            if agent_name:
                if cid in self._ctx_cache:
                    self._ctx_cache[cid].pop(agent_name, None)
            else:
                self._ctx_cache.pop(cid, None)

    def save_agent_context(self, cid: str, agent_name: str,
                           context_messages: List[Dict]) -> bool:
        """Write agent context to {agent}/context.jsonl (full replace)."""
        if not self.exists(cid):
            return False
        clean = [m for m in context_messages if not m.get("display_only")]
        # Cross-file UUID invariant: the same logical message must carry
        # the same msg_id here as in the transcript — preserved by
        # construction (msg_id is minted once in LLMMessage.__post_init__
        # and flows through every write path via dict(msg) transforms).
        for _m in clean:
            self._validate_message(_m)
        if agent_name:
            self._write_ctx_file(self._agent_ctx_path(cid, agent_name), clean)
        else:
            self._write_ctx_file(self._shared_ctx_path(cid), clean)
        self._invalidate_ctx_cache(cid, agent_name)
        return True

    def append_to_agent_context(self, cid: str, agent_name: str,
                                new_messages: List[Dict]) -> bool:
        """Append messages to agent context file."""
        if not self.exists(cid):
            return False
        clean = [m for m in new_messages if not m.get("display_only")]
        if not clean:
            return True
        if agent_name:
            self._append_ctx_file(cid, agent_name, clean)
        else:
            self._append_shared_ctx(cid, clean)
        self._invalidate_ctx_cache(cid, agent_name)
        return True

    def delete_agent_context(self, cid: str, agent_name: str) -> bool:
        """Delete agent context file + directory."""
        if not self.exists(cid):
            return False
        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        if path.exists():
            path.unlink()
        # Remove empty agent directory
        if agent_name and path.parent.is_dir():
            try:
                path.parent.rmdir()  # only succeeds if empty
            except OSError:
                pass
        self._invalidate_ctx_cache(cid, agent_name)
        # Reload main cache so agents set is updated
        with self._cache_lock:
            self._cache.pop(cid, None)
        self._reload_cache(cid)
        return True

    def save_context(self, cid: str, ctx: List[Dict]) -> bool:
        return self.save_agent_context(cid, "", ctx)

    def load_context(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        return self.load_agent_context(cid, "")

    # ── Transcript read ───────────────────────────────────────────────

    def _scan_transcript(self, lines) -> List[Dict]:
        """Scan JSONL lines into transcript messages (with patches applied)."""
        msgs = []
        patches = {}
        trace_updates = {}  # trace_id -> list of (entry, content_update)
        for line in lines:
            t = line.get("t", "")
            if t == "msg_patch":
                mid = line.get("msg_id", "")
                if mid:
                    patches[mid] = {k: v for k, v in line.items()
                                    if k not in ("t", "msg_id")}
                continue
            if t == "trace_update":
                tid = line.get("trace_id", "")
                if tid:
                    trace_updates.setdefault(tid, []).append(
                        (line.get("entry") or {}, line.get("content_update") or ""))
                continue
            if t != "msg":
                continue
            msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
            if "ts" in line:
                msg["timestamp"] = line["ts"]
            msgs.append(msg)
        if patches:
            for msg in msgs:
                mid = msg.get("msg_id", "")
                if mid and mid in patches:
                    msg.update(patches[mid])
        if trace_updates:
            for msg in msgs:
                if msg.get("role") != "sub_agent_trace":
                    continue
                tid = msg.get("trace_id", "")
                ups = trace_updates.get(tid)
                if not ups:
                    continue
                trace = list(msg.get("trace") or [])
                content = msg.get("content", "") or ""
                for entry, cu in ups:
                    if entry:
                        trace.append(entry)
                    if cu:
                        content += cu
                msg["trace"] = trace
                msg["content"] = content
        return msgs

    def load(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        """Load entire transcript (all messages)."""
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        return self._read(cid, self._scan_transcript)

    def load_page(self, cid: str, limit: int = 50, offset: int = 0,
                  user_id: str = "") -> Optional[Dict]:
        """Load a paginated slice of the transcript.

        Reads from the END of the JSONL file — only parses the lines needed.
        For a 2000-message conversation with limit=50, offset=0, this reads
        ~50 lines from the tail instead of scanning all 2000.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        path = self._conv_path(cid)
        total = self.message_count(cid)
        # _read_tail reads the file without holding the conv lock.
        # This avoids blocking _commit (set_status, append) while reading
        # large files. The file is append-only so reading stale data is safe
        # (we might miss the very last line, but that's acceptable for pagination).
        if not path.exists():
            return {"messages": [], "total_count": 0, "offset": 0,
                    "limit": limit, "has_more": False}
        try:
            result = self._read_tail(path, total, limit, offset)
            return result
        except Exception as e:
            logger.error("[convstore] load_page failed %s: %s", cid, e)
            return {"messages": [], "total_count": total, "offset": offset,
                    "limit": limit, "has_more": False}

    def _read_tail(self, path: Path, total_msgs: int, limit: int, offset: int) -> Dict:
        """Read the last (offset + limit) msg lines from the JSONL, return the page.

        Algorithm:
        1. Seek to end of file
        2. Read backwards in chunks to collect enough lines
        3. Parse only msg and msg_patch records
        4. Slice to the requested page
        """
        need = offset + limit + 20  # extra margin for msg_patch records + tool alignment
        _CHUNK = 8192

        with open(path, "rb") as f:
            f.seek(0, 2)  # end
            file_size = f.tell()
            if file_size == 0:
                return {"messages": [], "total_count": 0, "offset": offset,
                        "limit": limit, "has_more": False}

            # Read backwards in chunks, collect raw lines
            raw_lines = []
            pos = file_size
            remainder = b""
            msg_count = 0

            _lines_collected = 0
            while pos > 0 and _lines_collected < need:
                chunk_size = min(_CHUNK, pos)
                pos -= chunk_size
                f.seek(pos)
                chunk = f.read(chunk_size) + remainder
                remainder = b""

                parts = chunk.split(b"\n")
                if pos > 0:
                    remainder = parts[0]
                    parts = parts[1:]

                for raw in reversed(parts):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        line = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    t = line.get("t", "")
                    if t == "msg":
                        msg_count += 1
                    if t in ("msg", "msg_patch", "trace_update"):
                        raw_lines.append(line)
                        _lines_collected += 1

                if _lines_collected >= need:
                    break

            # If we still have a remainder from the very start of file
            if remainder:
                raw = remainder.strip()
                if raw:
                    try:
                        line = json.loads(raw)
                        t = line.get("t", "")
                        if t in ("msg", "msg_patch", "trace_update"):
                            raw_lines.append(line)
                            if t == "msg":
                                msg_count += 1
                    except json.JSONDecodeError:
                        pass

            # raw_lines is in reverse order (newest first) — reverse to chronological
            raw_lines.reverse()

            # Apply scan_transcript logic (separate msgs from patches)
            msgs = []
            patches = {}
            trace_updates = {}
            for line in raw_lines:
                t = line.get("t", "")
                if t == "msg_patch":
                    mid = line.get("msg_id", "")
                    if mid:
                        patches[mid] = {k: v for k, v in line.items()
                                        if k not in ("t", "msg_id")}
                    continue
                if t == "trace_update":
                    tid = line.get("trace_id", "")
                    if tid:
                        trace_updates.setdefault(tid, []).append(
                            (line.get("entry") or {}, line.get("content_update") or ""))
                    continue
                if t != "msg":
                    continue
                msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
                if "ts" in line:
                    msg["timestamp"] = line["ts"]
                msgs.append(msg)

            if patches:
                for msg in msgs:
                    mid = msg.get("msg_id", "")
                    if mid and mid in patches:
                        msg.update(patches[mid])
            if trace_updates:
                for msg in msgs:
                    if msg.get("role") != "sub_agent_trace":
                        continue
                    tid = msg.get("trace_id", "")
                    ups = trace_updates.get(tid)
                    if not ups:
                        continue
                    trace = list(msg.get("trace") or [])
                    content = msg.get("content", "") or ""
                    for entry, cu in ups:
                        if entry:
                            trace.append(entry)
                        if cu:
                            content += cu
                    msg["trace"] = trace
                    msg["content"] = content

            # Slice: msgs is chronological, we want the last `limit` before `offset`
            total_tail = len(msgs)
            end = total_tail - offset
            start = max(0, end - limit)
            # Don't split a tool_call from its tool results
            while start > 0 and msgs[start].get("role") == "tool":
                start -= 1
            page = msgs[start:end] if end > 0 else []
            has_more = (total_msgs - offset - len(page)) > 0

            return {"messages": page, "total_count": total_msgs,
                    "offset": offset, "limit": limit, "has_more": has_more}

    def patch_message(self, cid: str, msg_id: str, **fields) -> None:
        """Patch attributes on an existing message (appends a msg_patch record)."""
        if not msg_id or not fields:
            return
        lock = self._get_conv_lock(cid)
        line = {"t": "msg_patch", "msg_id": msg_id, "ts": time.time(), **fields}
        with lock:
            with open(self._transcript_path(cid), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def message_count(self, cid: str) -> int:
        return self._load_cache(cid).get("msg_count", 0)

    # ── Metadata ──────────────────────────────────────────────────────

    def get_metadata(self, cid: str) -> Optional[Dict]:
        if not self.exists(cid):
            return None
        c = self._load_cache(cid)
        return {"user_id": c.get("user_id", ""), "status": c.get("status", "idle"),
                "created_at": c.get("created_at", 0), "updated_at": c.get("updated_at", 0),
                "expires_at": c.get("expires_at", 0), "message_count": c.get("msg_count", 0)}

    # ── Extras ────────────────────────────────────────────────────────

    def get_extra_cached(self, cid: str, key: str, default: Any = None) -> Any:
        """Get extra from extras.json file."""
        return self._read_extras(cid).get(key, default)

    def get_extra(self, cid: str, key: str, default: Any = None,
                  user_id: str = "") -> Any:
        if not self.exists(cid):
            return default
        return self._read_extras(cid).get(key, default)

    def get_extras(self, cid: str, user_id: str = "") -> Optional[dict]:
        if not self.exists(cid):
            return None
        return dict(self._read_extras(cid))

    def set_extra(self, cid: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        if not self.exists(cid):
            # File gone but extras may still exist — clean up cache
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False
        lock = self._get_conv_lock(cid)
        with lock:
            data = self._read_extras(cid)
            data[key] = value
            self._write_extras(cid, data)
        # Update in-memory cache for list_conversations (title, updated_at)
        with self._cache_lock:
            if cid in self._cache:
                self._cache[cid]["extra_keys"].add(key)
                self._cache[cid].setdefault("extras", {})[key] = value
                if key == "title":
                    self._cache[cid]["title"] = value
                self._cache[cid]["updated_at"] = time.time()
        return True

    def invalidate_claude_sessions(self, cid: str) -> None:
        """Clear all claude-code session IDs for this conversation.

        Called when the user manually modifies context (delete message,
        manual compact, etc.). Forces a fresh session on next message.
        """
        extras = self.get_extras(cid) or {}
        for key in list(extras.keys()):
            if key.startswith("claude_session:"):
                self.set_extra(cid, key, "")
                logger.info("Invalidated claude session '%s' for conv %s", key, cid[:8])

    # ── Bindings (repository associations) ────────────────────────────

    def _bindings_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "bindings.json"

    def get_bindings(self, cid: str) -> Dict[str, list]:
        """Read all bindings for a conversation.

        Returns dict like {"agents": [{"name": "x", "scope": "global"}, ...], ...}
        """
        path = self._bindings_path(cid)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def set_bindings(self, cid: str, bindings: Dict[str, list]) -> None:
        """Replace all bindings for a conversation."""
        path = self._bindings_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)

    def add_binding(self, cid: str, rtype: str, name: str,
                    scope: str = "global") -> None:
        """Add a single binding (idempotent)."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.setdefault(rtype, [])
            if not any(e["name"] == name for e in entries):
                entries.append({"name": name, "scope": scope})
                self.set_bindings(cid, data)

    def remove_binding(self, cid: str, rtype: str, name: str) -> bool:
        """Remove a binding by name. Returns True if found and removed."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.get(rtype, [])
            before = len(entries)
            entries = [e for e in entries if e["name"] != name]
            if len(entries) == before:
                return False
            data[rtype] = entries
            self.set_bindings(cid, data)
            return True

    def list_bound(self, cid: str, rtype: str) -> List[Dict]:
        """List all bound items of a given type for a conversation."""
        return self.get_bindings(cid).get(rtype, [])

    # ── Delete ────────────────────────────────────────────────────────

    def delete(self, cid: str, user_id: str = "") -> bool:
        import os, shutil, stat
        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir():
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False

        def _force_remove(func, path, _exc_info):
            """Force-remove read-only files (git pack objects on Windows)."""
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)

        lock = self._get_conv_lock(cid)
        with lock:
            shutil.rmtree(conv_dir, onerror=_force_remove)
        with self._cache_lock:
            self._cache.pop(cid, None)
        self._conv_locks.pop(cid, None)
        self._cid_user.pop(cid, None)
        # Clean up all conv-scoped resources
        try:
            from core.file_store import FileStore
            FileStore.instance().delete_by(conversation_id=cid)
        except Exception:
            pass
        self._invalidate_ctx_cache(cid)
        return True

    def delete_message(self, cid: str, msg_id: str = "", index: int = -1,
                       user_id: str = "") -> bool:
        """Delete a message by msg_id from transcript + all contexts. Atomic."""
        if not msg_id and index < 0:
            return False
        if not self.exists(cid):
            return False

        # If we only have index, resolve to msg_id first
        if not msg_id and index >= 0:
            def _find_id(lines):
                count = 0
                for line in lines:
                    if line.get("t") == "msg" and not line.get("private"):
                        if count == index:
                            return line.get("msg_id", "")
                        count += 1
                return ""
            msg_id = self._read(cid, _find_id)
            if not msg_id:
                return False

        removed = self._remove_msg_ids_from_files(cid, {msg_id})
        return removed > 0

    def delete_messages(self, cid: str, msg_ids: list,
                        user_id: str = "") -> int:
        """Delete multiple messages by msg_id. Returns count of removed messages."""
        if not msg_ids or not self.exists(cid):
            return 0
        return self._remove_msg_ids_from_files(cid, set(msg_ids))

    def _remove_msg_ids_from_files(self, cid: str, ids: set) -> int:
        """Remove messages by msg_id from transcript + shared + all agent contexts."""
        lock = self._get_conv_lock(cid)
        removed = 0

        def _rewrite_jsonl(path: Path) -> int:
            """Rewrite a JSONL file, removing lines with matching msg_id. Returns count removed.

            Also removes:
              - sub_agent_trace messages whose trace_id matches an id in
                `ids` (legacy traces persisted without msg_id used the
                trace_id as the deletion key);
              - their associated trace_update lines (orphan otherwise).
            """
            if not path.exists():
                return 0
            count = 0
            # First pass: collect trace_ids that will be deleted (so we
            # can drop their trace_update follow-ups in the same pass).
            trace_ids_to_drop = set()
            try:
                with open(path, "r", encoding="utf-8") as src:
                    for raw in src:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            line = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if line.get("role") != "sub_agent_trace":
                            continue
                        _tid = line.get("trace_id", "")
                        if not _tid:
                            continue
                        if line.get("msg_id") in ids or _tid in ids:
                            trace_ids_to_drop.add(_tid)
            except Exception:
                pass

            tmp = path.with_suffix(".tmp")
            with open(path, "r", encoding="utf-8") as src, \
                 open(tmp, "w", encoding="utf-8") as dst:
                for raw in src:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        line = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if line.get("msg_id") in ids:
                        count += 1
                        continue
                    # Legacy sub_agent_trace without msg_id — match on trace_id
                    if (line.get("role") == "sub_agent_trace"
                            and line.get("trace_id") in ids):
                        count += 1
                        continue
                    # Drop orphan trace_update lines for deleted traces
                    if (line.get("t") == "trace_update"
                            and line.get("trace_id") in trace_ids_to_drop):
                        continue
                    dst.write(json.dumps(line, ensure_ascii=False) + "\n")
            if count:
                tmp.replace(path)
            else:
                tmp.unlink(missing_ok=True)
            return count

        with lock:
            # 1. Transcript
            removed += _rewrite_jsonl(self._transcript_path(cid))
            # 2. Shared context
            _rewrite_jsonl(self._shared_ctx_path(cid))
            # 3. All agent contexts
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and (entry / "context.jsonl").exists():
                        _rewrite_jsonl(entry / "context.jsonl")

        with self._cache_lock:
            self._cache.pop(cid, None)
        if removed:
            self._invalidate_ctx_cache(cid)  # clear ALL agent ctx caches
            self._load_cache(cid)
            self.invalidate_claude_sessions(cid)
        return removed

    # ── List ──────────────────────────────────────────────────────────

    def list_conversations(self, user_id: str = "") -> List[Dict]:
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
                    "title": c.get("title", ""),
                    "preview": c.get("preview", ""),
                    "message_count": c.get("msg_count", 0),
                    "status": c.get("status", "idle"),
                    "user_id": c.get("user_id", ""),
                    "created_at": c.get("created_at", 0),
                    "updated_at": c.get("updated_at", 0),
                    "expires_at": c.get("expires_at", 0),
                })
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def list_agent_contexts(self, cid: str) -> Dict[str, str]:
        c = self._load_cache(cid)
        result = {"*": "messages"}
        for a in c.get("agents", set()):
            result[a] = "diverged"
        return result

    # ── Display trace ─────────────────────────────────────────────────

    def create_display_trace(self, cid: str, trace_id: str,
                             source: Dict, user_id: str = "") -> bool:
        import uuid as _uuid
        lock = self._get_conv_lock(cid)
        # msg_id is required for the context editor's delete path
        # (selection sends msg_ids; without one the row is not deletable).
        line = {"t": "msg", "role": "sub_agent_trace", "display_only": True,
                "msg_id": _uuid.uuid4().hex,
                "trace_id": trace_id, "source": source, "content": "",
                "trace": [], "ts": time.time()}
        with lock:
            with open(self._transcript_path(cid), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True

    def append_display_trace(self, cid: str, trace_id: str,
                             entry_data: Dict, content_update: str = "") -> bool:
        entry_data.setdefault("ts", time.time())
        lock = self._get_conv_lock(cid)
        line = {"t": "trace_update", "trace_id": trace_id,
                "entry": entry_data, "content_update": content_update}
        with lock:
            with open(self._transcript_path(cid), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True

    # ── Cleanup ───────────────────────────────────────────────────────

    def vacuum(self, cid: str) -> dict:
        """Manual vacuum — no-op (extras are now atomic JSON, contexts are separate files)."""
        return {"status": "ok"}

    def cleanup(self) -> int:
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

    # ── Compat ────────────────────────────────────────────────────────

    @staticmethod
    def filter_display_only(msgs: List[Dict]) -> List[Dict]:
        return [m for m in msgs if not (isinstance(m, dict) and m.get("display_only"))]

    def set_metadata_field(self, cid: str, field: str, value: Any) -> bool:
        return self.set_extra(cid, field, value)
