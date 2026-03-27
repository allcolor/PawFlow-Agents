"""ConversationStore — JSONL append-only with single commit/read point.

Each conversation is a .jsonl file with one JSON object per line.
ONE _commit() for ALL writes. ONE _read() for ALL reads.
Per-conversation locks ensure atomicity of logical operations.

Line types:
  {"t":"meta", "user_id":"...", "status":"idle", "created_at":N, "expires_at":N}
  {"t":"msg", "role":"...", "content":"...", "msg_id":"...", "source":{}, "ts":N}
  {"t":"msg", ..., "private":true}  (tool calls/results — agent context only)
  {"t":"ctx", "agent":"name", "op":"replace", "data":[...]}
  {"t":"ctx", "agent":"name", "op":"append", "data":[...]}
  {"t":"extra", "key":"...", "value":...}
  {"t":"status", "status":"active"}
"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/conversations"
CONV_STATUSES = ("idle", "active", "complete", "blocked")


class ConversationStore:
    """Singleton JSONL conversation store. Thread-safe, append-only."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or _DEFAULT_DIR)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._conv_locks: Dict[str, threading.Lock] = {}
        self._conv_locks_lock = threading.Lock()
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

    def _get_conv_lock(self, cid: str) -> threading.Lock:
        with self._conv_locks_lock:
            if cid not in self._conv_locks:
                self._conv_locks[cid] = threading.Lock()
            return self._conv_locks[cid]

    def _conv_path(self, cid: str) -> Path:
        safe = "".join(c for c in cid if c.isalnum() or c in "-_:")
        safe = safe.replace(":", "__")
        return self._store_dir / f"{safe}.jsonl"

    # ══════════════════════════════════════════════════════════════════
    #  SINGLE READ POINT
    # ══════════════════════════════════════════════════════════════════

    def _read(self, cid: str, read_fn: Callable):
        """THE ONLY read method. Lock, stream file to read_fn, release.

        read_fn receives an iterator of parsed lines (streamed, not all in RAM).
        Returns whatever read_fn returns.
        """
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
    #  SINGLE WRITE POINT
    # ══════════════════════════════════════════════════════════════════

    def _commit(self, cid: str, operations: List[dict]) -> None:
        """THE ONLY write method. Lock, apply operations, release.

        Operations are dicts with an "op" key:
          {"op":"append", "lines":[...]}           — append lines to file
          {"op":"ctx_replace", "agent":"X", "data":[...]}  — replace agent ctx (with merge+vacuum)
          {"op":"ctx_append", "agent":"X", "data":[...]}   — append to agent ctx
          {"op":"ctx_delete", "agent":"X"}          — delete agent ctx
          {"op":"extra", "key":"K", "value":V}     — set extra
          {"op":"status", "status":"active"}        — set status
          {"op":"rewrite_full", "lines":[...]}     — replace entire file

        All operations are applied atomically under one lock.
        """
        if not operations:
            return
        lock = self._get_conv_lock(cid)
        path = self._conv_path(cid)

        with lock:
            # Classify: do we need a rewrite or just appends?
            needs_rewrite = any(
                op.get("op") in ("ctx_replace", "ctx_delete", "rewrite_full")
                for op in operations
            )

            if needs_rewrite:
                self._apply_with_rewrite(path, operations)
            else:
                self._apply_append_only(path, operations)

            # Invalidate + rebuild cache
            with self._cache_lock:
                self._cache.pop(cid, None)

        # Rebuild cache outside main lock (reads under its own lock)
        self._load_cache(cid)

    def _apply_append_only(self, path: Path, operations: List[dict]) -> None:
        """Fast path: just append lines to end of file."""
        lines_to_append = []
        for op in operations:
            op_type = op.get("op", "")
            if op_type == "append":
                lines_to_append.extend(op.get("lines", []))
            elif op_type == "ctx_append":
                lines_to_append.append({
                    "t": "ctx", "agent": op["agent"], "op": "append",
                    "data": op["data"],
                })
            elif op_type == "extra":
                lines_to_append.append({
                    "t": "extra", "key": op["key"], "value": op["value"],
                })
            elif op_type == "status":
                lines_to_append.append({
                    "t": "status", "status": op["status"],
                })
        if lines_to_append:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for line in lines_to_append:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.error(f"[convstore] append failed: {e}")
                raise

    def _apply_with_rewrite(self, path: Path, operations: List[dict]) -> None:
        """Slow path: stream file, apply transforms, write to tmp, rename.

        Handles ctx_replace (with merge), ctx_delete, and also
        applies any appends in the same pass.
        """
        # Separate operations by type
        replacements: Dict[str, List[dict]] = {}  # agent -> new data
        deletes: set = set()  # agents to delete
        appends: List[dict] = []  # lines to append at end
        full_rewrite = None

        for op in operations:
            op_type = op.get("op", "")
            if op_type == "rewrite_full":
                full_rewrite = op.get("lines", [])
            elif op_type == "ctx_replace":
                replacements[op["agent"]] = op["data"]
            elif op_type == "ctx_delete":
                deletes.add(op["agent"])
            elif op_type == "append":
                appends.extend(op.get("lines", []))
            elif op_type == "ctx_append":
                appends.append({
                    "t": "ctx", "agent": op["agent"], "op": "append",
                    "data": op["data"],
                })
            elif op_type == "extra":
                appends.append({"t": "extra", "key": op["key"], "value": op["value"]})
            elif op_type == "status":
                appends.append({"t": "status", "status": op["status"]})

        if full_rewrite is not None:
            # Complete file replacement
            tmp = path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    for line in full_rewrite:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                tmp.replace(path)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                raise
            return

        # Stream source file, collect state for merges, write to tmp
        #
        # Pass 1 info we need to collect while streaming:
        # - For each agent being replaced: current ctx msg_ids (for merge)
        # - The shared context (for identity check after replace)
        # - Transcript msg_ids per source (for merge: find missed msgs)
        #
        # We stream line by line to keep RAM low.

        # Lightweight tracking — only msg_ids and sources, not full messages
        shared_ctx_ids: List[str] = []  # msg_ids of shared context
        # For merge: transcript msg_ids + source agent (lightweight)
        transcript_index: List[dict] = []  # [{"msg_id":"...", "source_agent":"...", "line_num":N}]
        # For merge: we need the FULL lines of missed messages — but only the missed ones
        # So we track where they are (line numbers) and re-read them in a second pass if needed

        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as dst:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as src:
                        for raw in src:
                            raw = raw.strip()
                            if not raw:
                                continue
                            try:
                                line = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            t = line.get("t", "")
                            agent = line.get("agent", "")

                            # Lightweight transcript index (msg_id + source only)
                            if t == "msg" and not line.get("private"):
                                src_dict = line.get("source", {})
                                src_name = src_dict.get("name", "") if isinstance(src_dict, dict) else ""
                                transcript_index.append({
                                    "msg_id": line.get("msg_id", ""),
                                    "source_agent": src_name,
                                    "line": line,  # keep ref for merge (streaming — already parsed)
                                })

                            # Track shared context msg_ids
                            if t == "ctx" and agent == "":
                                if line.get("op") == "replace":
                                    shared_ctx_ids = [m.get("msg_id", "") for m in line.get("data", [])]
                                elif line.get("op") == "append":
                                    shared_ctx_ids.extend(m.get("msg_id", "") for m in line.get("data", []))

                            # Skip ctx lines for agents being replaced or deleted
                            if t == "ctx" and (agent in replacements or agent in deletes):
                                continue  # vacuum: don't write old ctx lines

                            # Write all other lines as-is
                            dst.write(json.dumps(line, ensure_ascii=False) + "\n")

                # Now handle replacements with merge
                for agent, new_data in replacements.items():
                    final_data = self._merge_ctx_replace(
                        agent, new_data, transcript_index)
                    # Check if final == shared → delete instead of replace
                    final_ids = [m.get("msg_id", "") for m in final_data]
                    if shared_ctx_ids and final_ids == shared_ctx_ids:
                        logger.info(f"[convstore] ctx '{agent}' == shared after replace, removing")
                    else:
                        dst.write(json.dumps({
                            "t": "ctx", "agent": agent, "op": "replace",
                            "data": final_data,
                        }, ensure_ascii=False) + "\n")

                # Append any remaining lines
                for line in appends:
                    dst.write(json.dumps(line, ensure_ascii=False) + "\n")

            tmp.replace(path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            logger.error(f"[convstore] rewrite failed: {e}")
            raise

    @staticmethod
    def _merge_ctx_replace(agent: str, new_data: List[dict],
                           transcript_index: List[dict]) -> List[dict]:
        """Merge missed messages into a context replacement.

        transcript_index: lightweight list of {"msg_id", "source_agent", "line"}
        from the streaming pass.

        Logic:
        1. Find cutoff: last transcript msg_id in new_data that is NOT from this agent
        2. Find transcript msgs AFTER cutoff not from this agent and not in new_data
        3. Those are missed → merge
        """
        if not transcript_index:
            return new_data

        new_ids = {m.get("msg_id") for m in new_data if m.get("msg_id")}
        agent_lower = agent.lower()

        # Find cutoff
        cutoff_idx = -1
        for i, ti in enumerate(transcript_index):
            if (ti["msg_id"] and ti["msg_id"] in new_ids
                    and ti["source_agent"].lower() != agent_lower):
                cutoff_idx = i

        if cutoff_idx < 0:
            return new_data

        # Collect missed
        missed = []
        for ti in transcript_index[cutoff_idx + 1:]:
            if ti["source_agent"].lower() == agent_lower:
                continue
            if ti["msg_id"] and ti["msg_id"] in new_ids:
                continue
            line = ti["line"]
            if line.get("display_only"):
                continue  # NEVER merge display_only into contexts
            msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
            if "ts" in line:
                msg["timestamp"] = line["ts"]
            missed.append(msg)

        if missed:
            logger.info(f"[convstore] ctx_replace '{agent}': merged {len(missed)} "
                        f"missed transcript message(s)")
            return list(new_data) + missed
        return new_data


    # ══════════════════════════════════════════════════════════════════
    #  CACHE
    # ══════════════════════════════════════════════════════════════════

    def _load_cache(self, cid: str) -> dict:
        with self._cache_lock:
            if cid in self._cache:
                return self._cache[cid]

        def _scan(lines):
            c = {"user_id": "", "status": "idle", "created_at": 0,
                 "updated_at": 0, "expires_at": 0, "msg_count": 0,
                 "agents": set(), "extra_keys": set(), "preview": ""}
            for line in lines:
                t = line.get("t", "")
                if t == "meta":
                    c["user_id"] = line.get("user_id", "")
                    c["status"] = line.get("status", "idle")
                    c["created_at"] = line.get("created_at", 0)
                    c["expires_at"] = line.get("expires_at", 0)
                elif t == "msg" and not line.get("private"):
                    c["msg_count"] += 1
                    # First user message = preview
                    if not c["preview"] and line.get("role") == "user":
                        content = line.get("content", "")
                        if isinstance(content, str) and content.strip():
                            c["preview"] = content[:80]
                elif t == "ctx":
                    a = line.get("agent", "")
                    if a:
                        c["agents"].add(a)
                elif t == "extra":
                    c["extra_keys"].add(line.get("key", ""))
                elif t == "status":
                    c["status"] = line.get("status", c["status"])
                c["updated_at"] = max(c["updated_at"], line.get("ts", 0))
            return c

        c = self._read(cid, _scan)
        with self._cache_lock:
            self._cache[cid] = c
        return c

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        count = 0
        for p in self._store_dir.glob("*.jsonl"):
            cid = p.stem.replace("__", ":")
            self._load_cache(cid)
            count += 1
        if count:
            logger.info(f"ConversationStore: loaded {count} conversations from disk")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def exists(self, cid: str) -> bool:
        return self._conv_path(cid).exists()

    # ── Create / Save ─────────────────────────────────────────────────

    def save(self, cid: str, messages: List[Dict], ttl: int = 0,
             user_id: str = "", status: str = ""):
        lines = [{"t": "meta", "user_id": user_id, "status": status or "idle",
                  "created_at": time.time(),
                  "expires_at": time.time() + ttl if ttl > 0 else 0}]
        for m in messages:
            line = {"t": "msg", **m}
            if "ts" not in line and "timestamp" not in line:
                line["ts"] = time.time()
            lines.append(line)
        self._commit(cid, [{"op": "rewrite_full", "lines": lines}])

    # ── Agent flush (main write op) ──────────────────────────────────

    def agent_flush(self, cid: str, agent_name: str,
                    public_messages: List[Dict],
                    private_messages: List[Dict],
                    user_id: str = "", ttl: int = 0):
        now = time.time()
        ops: List[dict] = []
        lines: List[dict] = []

        if not self.exists(cid):
            lines.append({"t": "meta", "user_id": user_id,
                          "status": "idle", "created_at": now,
                          "expires_at": now + ttl if ttl > 0 else 0})

        for m in public_messages:
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)

        for m in private_messages:
            line = {"t": "msg", "private": True, **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)

        if lines:
            ops.append({"op": "append", "lines": lines})

        # Filter display_only — NEVER goes into any context
        ctx_public = [m for m in public_messages if not m.get("display_only")]
        ctx_private = [m for m in private_messages if not m.get("display_only")]
        all_agent = ctx_public + ctx_private
        if all_agent:
            ops.append({"op": "ctx_append", "agent": agent_name, "data": all_agent})

        if ctx_public:
            cache = self._load_cache(cid)
            for other in cache.get("agents", set()):
                if other and other != agent_name:
                    ops.append({"op": "ctx_append", "agent": other,
                                "data": ctx_public})

        self._commit(cid, ops)

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
        lines = []
        if not self.exists(cid):
            lines.append({"t": "meta", "user_id": user_id,
                          "status": status or "idle", "created_at": now,
                          "expires_at": now + ttl if ttl > 0 else 0})
        for m in new_messages:
            line = {"t": "msg", **m}
            if "ts" not in line:
                line["ts"] = now
            lines.append(line)
        ops = [{"op": "append", "lines": lines}]
        if status:
            ops.append({"op": "status", "status": status})
        self._commit(cid, ops)

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
        def _scan(lines):
            data = None
            appends = []
            found = False
            for line in lines:
                if line.get("t") != "ctx" or line.get("agent") != agent_name:
                    continue
                if line.get("op") == "replace":
                    data = list(line.get("data", []))
                    appends = []
                    found = True
                elif line.get("op") == "append" and found:
                    appends.append(line.get("data", []))
            if data is None:
                return None
            for batch in appends:
                data.extend(batch)
            return data
        return self._read(cid, _scan)

    def save_agent_context(self, cid: str, agent_name: str,
                           context_messages: List[Dict]) -> bool:
        if not self.exists(cid):
            return False
        # NEVER put display_only messages in contexts
        clean = [m for m in context_messages if not m.get("display_only")]
        self._commit(cid, [{"op": "ctx_replace", "agent": agent_name or "",
                            "data": clean}])
        return True

    def append_to_agent_context(self, cid: str, agent_name: str,
                                new_messages: List[Dict]) -> bool:
        if not self.exists(cid):
            return False
        clean = [m for m in new_messages if not m.get("display_only")]
        if not clean:
            return True
        self._commit(cid, [{"op": "ctx_append", "agent": agent_name,
                            "data": clean}])
        return True

    def delete_agent_context(self, cid: str, agent_name: str) -> bool:
        if not self.exists(cid):
            return False
        self._commit(cid, [{"op": "ctx_delete", "agent": agent_name}])
        return True

    def save_context(self, cid: str, ctx: List[Dict]) -> bool:
        return self.save_agent_context(cid, "", ctx)

    def load_context(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        return self.load_agent_context(cid, "")

    # ── Transcript read ───────────────────────────────────────────────

    def load(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        def _scan(lines):
            msgs = []
            for line in lines:
                if line.get("t") != "msg" or line.get("private"):
                    continue
                msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
                if "ts" in line:
                    msg["timestamp"] = line["ts"]
                msgs.append(msg)
            return msgs
        return self._read(cid, _scan)

    def load_page(self, cid: str, limit: int = 50, offset: int = 0,
                  user_id: str = "") -> Optional[Dict]:
        """Stream transcript, count total, return only the requested page."""
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        def _scan(lines):
            # Stream: collect only msg_ids/indices, then re-read page
            msgs = []
            for line in lines:
                if line.get("t") != "msg" or line.get("private"):
                    continue
                msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
                if "ts" in line:
                    msg["timestamp"] = line["ts"]
                msgs.append(msg)
            total = len(msgs)
            end = total - offset
            start = max(0, end - limit)
            while start > 0 and msgs[start].get("role") == "tool":
                start -= 1
            return {"messages": msgs[start:end], "total_count": total,
                    "offset": offset, "limit": limit, "has_more": start > 0}
        return self._read(cid, _scan)

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

    def set_status(self, cid: str, status: str, user_id: str = "") -> bool:
        if status not in CONV_STATUSES or not self.exists(cid):
            return False
        self._commit(cid, [{"op": "status", "status": status}])
        return True

    # ── Extras ────────────────────────────────────────────────────────

    def get_extra(self, cid: str, key: str, default: Any = None,
                  user_id: str = "") -> Any:
        if not self.exists(cid):
            return default
        def _scan(lines):
            result = default
            for line in lines:
                if line.get("t") == "extra" and line.get("key") == key:
                    result = line.get("value", default)
            return result
        return self._read(cid, _scan)

    def get_extras(self, cid: str, user_id: str = "") -> Optional[dict]:
        if not self.exists(cid):
            return None
        def _scan(lines):
            extras = {}
            for line in lines:
                if line.get("t") == "extra":
                    extras[line["key"]] = line.get("value")
            return extras
        return self._read(cid, _scan)

    def set_extra(self, cid: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        if not self.exists(cid):
            return False
        self._commit(cid, [{"op": "extra", "key": key, "value": value}])
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

    # ── Delete ────────────────────────────────────────────────────────

    def delete(self, cid: str, user_id: str = "") -> bool:
        path = self._conv_path(cid)
        if not path.exists():
            return False
        lock = self._get_conv_lock(cid)
        with lock:
            path.unlink(missing_ok=True)
        with self._cache_lock:
            self._cache.pop(cid, None)
        prefix = f"{cid}::task::"
        for p in self._store_dir.glob("*.jsonl"):
            sub_cid = p.stem.replace("__", ":")
            if sub_cid.startswith(prefix):
                sub_lock = self._get_conv_lock(sub_cid)
                with sub_lock:
                    p.unlink(missing_ok=True)
                with self._cache_lock:
                    self._cache.pop(sub_cid, None)
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

        # Rewrite: remove this msg_id from transcript and all contexts
        def _remove_from_ctx(data: list) -> list:
            return [m for m in data if m.get("msg_id") != msg_id]

        # Use a custom rewrite that streams and filters
        lock = self._get_conv_lock(cid)
        path = self._conv_path(cid)
        with lock:
            if not path.exists():
                return False
            tmp = path.with_suffix(".tmp")
            removed = False
            try:
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
                        t = line.get("t", "")
                        # Remove from transcript
                        if t == "msg" and line.get("msg_id") == msg_id:
                            removed = True
                            continue
                        # Remove from contexts
                        if t == "ctx" and "data" in line:
                            old_len = len(line["data"])
                            line["data"] = _remove_from_ctx(line["data"])
                            if len(line["data"]) != old_len:
                                removed = True
                        dst.write(json.dumps(line, ensure_ascii=False) + "\n")
                tmp.replace(path)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                logger.error(f"[convstore] delete_message rewrite failed: {e}")
                return False
            with self._cache_lock:
                self._cache.pop(cid, None)
        if removed:
            self._load_cache(cid)
            # Manual context modification → invalidate claude-code sessions
            self.invalidate_claude_sessions(cid)
        return removed

    def delete_messages(self, cid: str, msg_ids: list,
                        user_id: str = "") -> int:
        """Delete multiple messages by msg_id. Returns count of removed messages."""
        if not msg_ids or not self.exists(cid):
            return 0
        ids_to_remove = set(msg_ids)

        def _filter_ctx(data: list) -> list:
            return [m for m in data if m.get("msg_id") not in ids_to_remove]

        lock = self._get_conv_lock(cid)
        path = self._conv_path(cid)
        removed = 0
        with lock:
            if not path.exists():
                return 0
            tmp = path.with_suffix(".tmp")
            try:
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
                        t = line.get("t", "")
                        if t == "msg" and line.get("msg_id") in ids_to_remove:
                            removed += 1
                            continue
                        if t == "ctx" and "data" in line:
                            old_len = len(line["data"])
                            line["data"] = _filter_ctx(line["data"])
                            removed += old_len - len(line["data"])
                        dst.write(json.dumps(line, ensure_ascii=False) + "\n")
                tmp.replace(path)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                logger.error("[convstore] delete_messages rewrite failed: %s", e)
                return 0
            with self._cache_lock:
                self._cache.pop(cid, None)
        if removed:
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
        self._commit(cid, [{"op": "append", "lines": [{
            "t": "msg", "role": "sub_agent_trace", "display_only": True,
            "trace_id": trace_id, "source": source, "content": "",
            "trace": [], "ts": time.time(),
        }]}])
        return True

    def append_display_trace(self, cid: str, trace_id: str,
                             entry_data: Dict, content_update: str = "") -> bool:
        entry_data.setdefault("ts", time.time())
        self._commit(cid, [{"op": "append", "lines": [{
            "t": "trace_update", "trace_id": trace_id,
            "entry": entry_data, "content_update": content_update,
        }]}])
        return True

    # ── Cleanup ───────────────────────────────────────────────────────

    def vacuum(self, cid: str) -> dict:
        """Manual vacuum — remove superseded extras/status lines."""
        # This is a rewrite op with no ctx changes
        # Just filter superseded extras and status
        # (ctx vacuum happens automatically in ctx_replace)
        # For now, no-op — the ctx_replace handles the main bloat
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
