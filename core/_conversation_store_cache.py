"""ConversationStore transcript/cache read + hot-metadata persistence."""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)
import core._conversation_store_base as _csb  # noqa: E402


class _CsCacheMixin:
    """transcript/cache read + hot-metadata persistence."""

    def _read(self, cid: str, read_fn: Callable):
        """THE ONLY transcript read method.

        Do not hold the conversation write lock while scanning the full
        transcript. The file is append-only; a concurrent partial final row is
        ignored by the JSON decoder and will be visible on the next read.
        """
        log = self._transcript_log(cid)
        if not log.exists():
            return read_fn(iter([]))
        try:
            return read_fn(log.iter_rows())
        except OSError as e:
            logger.error(f"[convstore] read failed {cid}: {e}")
            return read_fn(iter([]))

    @staticmethod
    def _iter_jsonl_reverse(path: Path, chunk_size: int = 1024 * 1024):
        """Yield JSONL rows from the end of a file without loading it all."""
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                buf = b""
                while pos > 0:
                    n = min(chunk_size, pos)
                    pos -= n
                    f.seek(pos)
                    buf = f.read(n) + buf
                    lines = buf.split(b"\n")
                    buf = lines[0]
                    for raw in reversed(lines[1:]):
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            yield json.loads(raw.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            continue
                raw = buf.strip()
                if raw:
                    try:
                        yield json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        return
        except FileNotFoundError:
            return
        except OSError as e:
            logger.error("[convstore] reverse read failed %s: %s", path, e)
            return

    def load_transcript_seq_range(self, cid: str, first_seq: int,
                                  last_seq: int) -> List[Dict]:
        """Load transcript rows in seq range without a full transcript scan.

        Seq is monotonic in transcript file order, so reverse scanning can stop
        as soon as it sees a row before ``first_seq``. This is used by bg bucket
        trace extraction, where ranges are normally near the tail.
        """
        if not self.exists(cid):
            return []
        first_seq = int(first_seq or 0)
        last_seq = int(last_seq or 0)
        if first_seq <= 0 or last_seq < first_seq:
            return []
        rows: List[Dict] = []
        for row in self._transcript_log(cid).iter_rows_reverse():
            seq = int(row.get("seq") or 0)
            if seq > last_seq:
                continue
            if seq < first_seq:
                break
            rows.append(row)
        rows.reverse()
        return rows

    @staticmethod
    def _scan_cache(lines):
        c = {"user_id": "", "status": "idle", "created_at": 0,
             "updated_at": 0, "expires_at": 0, "msg_count": 0,
             "agents": set(), "extra_keys": set(), "extras": {}, "preview": "",
             "_max_seq": 0}
        for line in lines:
            seq = line.get("seq")
            if isinstance(seq, int) and seq > c["_max_seq"]:
                c["_max_seq"] = seq
            if line.get("role"):
                c["msg_count"] += 1
                if not c["preview"] and line.get("role") == "user":
                    content = line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        c["preview"] = content[:80]
            c["updated_at"] = max(c["updated_at"], line.get("ts", 0))
        return c

    @staticmethod
    def _count_message_rows(log: SegmentedJsonl) -> int:
        return sum(1 for row in log.iter_rows() if row.get("role"))

    def _load_cache(self, cid: str) -> dict:
        with self._cache_lock:
            if cid in self._cache:
                return self._cache[cid]
        return self._load_cache_metadata(cid)

    def _reload_cache(self, cid: str) -> dict:
        """Read file from disk and atomically swap cache entry.

        Extras are loaded from the separate extras.json file (not from JSONL).
        No gap where the entry is absent — list_conversations always
        sees either the old or new value, never missing.
        """
        c = self._read(cid, self._scan_cache)
        try:
            from core.llm_client import _seed_persisted_seq
            _seed_persisted_seq(cid, int(c.get("_max_seq") or 0))
        except Exception:
            logger.debug("persisted seq seed failed for %s", cid[:8], exc_info=True)
        c.pop("_max_seq", None)
        # Merge extras from extras.json file (source of truth)
        extras_data = self._read_extras(cid)
        if extras_data:
            c["extras"] = extras_data
            c["extra_keys"] = set(extras_data.keys())
            if "title" in extras_data:
                c["title"] = extras_data["title"]
            # Use meta from extras for cache fields
            c["user_id"] = extras_data.get("_meta_user_id", c.get("user_id", ""))
            c["status"] = extras_data.get("_meta_status", c.get("status", "idle"))
            if extras_data.get("_meta_created_at"):
                c["created_at"] = max(c["created_at"], extras_data["_meta_created_at"])
                c["updated_at"] = max(c["updated_at"], extras_data["_meta_created_at"])
        # Only declared conversation agents are routable agent contexts.
        # Arbitrary context directories can exist from older bugs/backups;
        # never let their folder names create pseudo-agents such as
        # "background" or a user id.
        conv_agents = c.get("extras", {}).get("conv_agents") or {}
        declared_agents = set()
        if isinstance(conv_agents, dict) and conv_agents:
            declared_agents.update(self._canon_agent(a) for a in conv_agents if a)
            c["agents"].update(declared_agents)
        with self._cache_lock:
            self._cache[cid] = c
            self._append_agents_cache[cid] = set(declared_agents)
        if declared_agents:
            self._prune_invalid_agent_context_dirs(cid, declared_agents)
        return c

    @staticmethod
    def _cache_ts(line: Dict[str, Any]) -> float:
        try:
            return float(line.get("ts") or line.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _latest_transcript_line(self, cid: str) -> Dict[str, Any]:
        try:
            log = self._transcript_log(cid)
            if log.segment_dir.is_dir():
                for path in sorted(log.segment_dir.glob("*.jsonl"), reverse=True):
                    for line in SegmentedJsonl._iter_file_reverse(path):
                        return line
            if log.flat_path.exists():
                for line in SegmentedJsonl._iter_file_reverse(log.flat_path):
                    return line
        except Exception:
            logger.debug("latest transcript row read failed for %s", cid[:8], exc_info=True)
        return {}

    def peek_persisted_max_seq(self, cid: str) -> int:
        """Return the latest persisted seq without scanning the transcript body."""
        max_seq = 0
        try:
            max_seq = int((self._read_extras(cid) or {}).get("_meta_max_seq") or 0)
        except Exception:
            logger.debug("metadata max seq read failed for %s", cid[:8], exc_info=True)
        try:
            max_seq = max(max_seq, int(self._latest_transcript_line(cid).get("seq") or 0))
        except (TypeError, ValueError):
            pass
        return max_seq

    def _load_cache_metadata(self, cid: str, user_id: str = "") -> dict:
        """Warm list/ownership cache without scanning the transcript body."""
        extras_data = self._read_extras(cid)
        log = self._transcript_log(cid)
        latest = self._latest_transcript_line(cid) if log.exists() else {}
        c = {"user_id": user_id or extras_data.get("_meta_user_id", ""),
             "status": extras_data.get("_meta_status", "idle"),
             "created_at": extras_data.get("_meta_created_at", 0),
             "updated_at": extras_data.get("_meta_updated_at", 0),
             "expires_at": extras_data.get("_meta_expires_at", 0),
             "msg_count": int(extras_data.get("_meta_msg_count") or 0),
             "agents": set(), "extra_keys": set(extras_data.keys()),
             "extras": extras_data, "preview": extras_data.get("_meta_preview", "")}
        if "_meta_msg_count" not in extras_data and log.exists():
            c["msg_count"] = self._count_message_rows(log)
        if latest:
            c["updated_at"] = max(float(c.get("updated_at") or 0), self._cache_ts(latest))
        if "title" in extras_data:
            c["title"] = extras_data["title"]
        max_seq = int(extras_data.get("_meta_max_seq") or 0)
        try:
            max_seq = max(max_seq, int(latest.get("seq") or 0))
        except (TypeError, ValueError):
            pass
        try:
            from core.llm_client import _seed_persisted_seq
            _seed_persisted_seq(cid, max_seq)
        except Exception:
            logger.debug("persisted seq seed failed for %s", cid[:8], exc_info=True)
        conv_agents = extras_data.get("conv_agents") or {}
        declared_agents = set()
        if isinstance(conv_agents, dict) and conv_agents:
            declared_agents.update(self._canon_agent(a) for a in conv_agents if a)
            c["agents"].update(declared_agents)
        with self._cache_lock:
            self._cache[cid] = c
            self._append_agents_cache[cid] = set(declared_agents)
        self._schedule_prune_invalid_agent_context_dirs(cid, declared_agents)
        return c

    def _schedule_prune_invalid_agent_context_dirs(self, cid: str,
                                                   declared_agents: set) -> None:
        if not declared_agents:
            return
        try:
            _csb._HOT_METADATA_EXECUTOR.submit(
                self._prune_invalid_agent_context_dirs,
                cid, set(declared_agents))
        except Exception:
            logger.debug("invalid context-dir prune schedule failed", exc_info=True)

    def _prune_invalid_agent_context_dirs(self, cid: str,
                                          declared_agents: set) -> None:
        """Delete private context dirs that do not belong to declared agents."""
        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir():
            return
        skip = {".git", "transcript", "shared", "summaries",
                "_jsonl_migration_backup"}
        for entry in conv_dir.iterdir():
            if not entry.is_dir() or entry.name in skip:
                continue
            agent = self._canon_agent(entry.name.replace("__", ":"))
            if agent in declared_agents:
                continue
            if not (self._jsonl_exists(entry / "context.jsonl")
                    or (entry / "context").is_dir()):
                continue
            try:
                shutil.rmtree(entry)
                logger.warning(
                    "[convstore] pruned invalid agent context dir %s/%s",
                    cid[:8], agent)
            except Exception:
                logger.warning(
                    "[convstore] failed to prune invalid context dir %s/%s",
                    cid[:8], agent, exc_info=True)

    def _cache_agents_for_append(self, cid: str) -> set:
        """Return known agents without rescanning the transcript hot path."""
        with self._cache_lock:
            append_cached = self._append_agents_cache.get(cid)
            if append_cached is not None:
                return set(append_cached)
            cached = self._cache.get(cid)
            if cached is not None:
                agents = set(cached.get("agents", set()))
                if agents:
                    self._append_agents_cache[cid] = set(agents)
                    return agents
                self._append_agents_cache[cid] = set()
                return set()
                self._append_agents_cache[cid] = set()
                return set()

        # This method runs under the per-conversation append lock.  A cache
        # miss must stay cheap: _reload_cache() scans transcript.jsonl, which
        # can be tens of thousands of rows and will block every queued user
        # message while the append lock is held.  Routable agents are declared
        # in extras.conv_agents, so read that small sidecar directly instead.
        extras_data = self._read_extras(cid)
        conv_agents = extras_data.get("conv_agents") or {}
        agents = set()
        if isinstance(conv_agents, dict) and conv_agents:
            agents.update(self._canon_agent(a) for a in conv_agents if a)
        with self._cache_lock:
            self._append_agents_cache[cid] = set(agents)
        return agents

    def _note_cache_append(self, cid: str, transcript_line: Optional[Dict],
                           agents: set) -> None:
        """Apply append_message side effects to the in-memory cache.

        append_message is the hot path for every streamed assistant block,
        tool_call, and tool_result. Rescanning transcript.jsonl after each
        append makes long conversations slower on every write; the fields
        affected by an append are trivial to update in memory.
        """
        with self._cache_lock:
            if agents:
                self._append_agents_cache.setdefault(cid, set()).update(
                    self._canon_agent(a) for a in agents if a)
            cached = self._cache.get(cid)
            if cached is None:
                return
            if transcript_line is not None:
                cached["msg_count"] = int(cached.get("msg_count") or 0) + 1
                if not cached.get("preview") and transcript_line.get("role") == "user":
                    content = transcript_line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        cached["preview"] = content[:80]
                cached["updated_at"] = max(
                    cached.get("updated_at", 0), transcript_line.get("ts", 0))
            if agents:
                cached.setdefault("agents", set()).update(
                    self._canon_agent(a) for a in agents if a)
            if transcript_line is not None:
                self._update_cached_hot_metadata_locked(cached, transcript_line)
        if transcript_line is not None:
            self._persist_hot_metadata(cid, transcript_line)

    def _update_cached_hot_metadata_locked(self, cached: Dict[str, Any],
                                           transcript_line: Dict[str, Any]) -> None:
        """Update restart metadata in the warm cache without disk I/O.

        Caller must hold `_cache_lock`.
        """
        extras = cached.setdefault("extras", {})
        extra_keys = cached.setdefault("extra_keys", set())
        if transcript_line.get("role"):
            count = max(int(cached.get("msg_count") or 0),
                        int(extras.get("_meta_msg_count") or 0) + 1)
            extras["_meta_msg_count"] = count
            extra_keys.add("_meta_msg_count")
            if not extras.get("_meta_preview") and transcript_line.get("role") == "user":
                content = transcript_line.get("content", "")
                if isinstance(content, str) and content.strip():
                    extras["_meta_preview"] = content[:80]
                    extra_keys.add("_meta_preview")
        ts = self._cache_ts(transcript_line)
        if ts:
            extras["_meta_updated_at"] = max(
                float(extras.get("_meta_updated_at") or 0), ts)
            extra_keys.add("_meta_updated_at")
        try:
            seq = int(transcript_line.get("seq") or 0)
            if seq:
                extras["_meta_max_seq"] = max(
                    int(extras.get("_meta_max_seq") or 0), seq)
                extra_keys.add("_meta_max_seq")
        except (TypeError, ValueError):
            pass

    def _hot_metadata_snapshot(self, cid: str) -> Dict[str, Any]:
        with self._cache_lock:
            extras = (self._cache.get(cid) or {}).get("extras") or {}
            return {k: extras[k] for k in _HOT_METADATA_KEYS if k in extras}

    def _merge_hot_metadata_snapshot(self, cid: str,
                                     data: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = self._hot_metadata_snapshot(cid)
        if snapshot:
            data.update(snapshot)
        return data

    def _persist_hot_metadata(self, cid: str, transcript_line: Dict[str, Any]) -> None:
        snapshot = self._hot_metadata_snapshot(cid)
        if not snapshot:
            return
        try:
            count = int(snapshot.get("_meta_msg_count") or 0)
        except (TypeError, ValueError):
            count = 0
        now = time.monotonic()
        with self._cache_lock:
            state = self._hot_metadata_flush.setdefault(cid, {})
            last_attempt = float(state.get("last_attempt") or 0.0)
            last_count = int(state.get("last_count") or 0)
            due_by_time = (now - last_attempt) >= _csb._HOT_METADATA_FLUSH_INTERVAL_SEC
            due_by_count = (count - last_count) >= _csb._HOT_METADATA_FLUSH_MSG_DELTA
            if last_attempt and not (due_by_time or due_by_count):
                return
            if state.get("running"):
                return
            state["last_attempt"] = now
            state["running"] = True

        _csb._HOT_METADATA_EXECUTOR.submit(
            self._persist_hot_metadata_worker, cid, snapshot, count, now)

    def _persist_hot_metadata_worker(self, cid: str, snapshot: Dict[str, Any],
                                     count: int, started_at: float) -> None:
        try:
            lock = self._get_extras_lock(cid)
            if not lock.acquire(blocking=False):
                return
            try:
                data = self._read_extras(cid)
                data.update(snapshot)
                try:
                    # Hot metadata is a startup/read cache derived from the
                    # transcript. Never let a transient Windows handle on
                    # extras.json reject the actual message append.
                    self._write_extras(cid, data, attempts=1)
                except PermissionError as _pe:
                    logger.warning(
                        "[convstore:%s] hot metadata extras write skipped: %s",
                        cid[:8], _pe)
                    return
            finally:
                lock.release()
            with self._cache_lock:
                state = self._hot_metadata_flush.setdefault(cid, {})
                state["last_count"] = count
                state["last_success"] = started_at
        finally:
            with self._cache_lock:
                state = self._hot_metadata_flush.setdefault(cid, {})
                state["running"] = False

    def _persist_recomputed_hot_metadata(self, cid: str,
                                         cached: Dict[str, Any]) -> None:
        """Persist hot metadata after non-append transcript mutations.

        Appends can update `_meta_msg_count` incrementally. Deletes and
        rewrites must replace it with the recomputed transcript count, or a
        fresh page load will show phantom messages from stale metadata.
        """
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            data["_meta_msg_count"] = int(cached.get("msg_count") or 0)
            data["_meta_preview"] = cached.get("preview", "") or ""
            data["_meta_updated_at"] = cached.get("updated_at") or 0
            self._write_extras(cid, data)
        with self._cache_lock:
            current = self._cache.get(cid)
            if current is not None:
                current["msg_count"] = int(cached.get("msg_count") or 0)
                current["preview"] = cached.get("preview", "") or ""
                current["updated_at"] = cached.get("updated_at") or 0
                current["extras"] = dict(data)
                current["extra_keys"] = set(data.keys())

    def _ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:  # class-level lock (also used for singleton)
            if self._loaded:
                return
            # Hold the lock across the scan so concurrent callers (boot-time
            # cleanup_orphan_claude_sessions) wait for the cache to be fully
            # populated. Previously we set _loaded=True BEFORE the scan,
            # which let those callers observe a half-empty cache and treat
            # live convs as orphans (safety net caught it, but it logged
            # a "cache race" warning for every live conv).
            count = 0
            for user_dir in self._store_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                uid = user_dir.name
                for conv_dir in user_dir.iterdir():
                    if not conv_dir.is_dir():
                        continue
                    if (not SegmentedJsonl(conv_dir / "transcript.jsonl").exists()
                            and not (conv_dir / "extras.json").exists()):
                        continue
                    cid = conv_dir.name.replace("__", ":")
                    self._cid_user[cid] = uid
                    self._load_cache_metadata(cid, uid)
                    count += 1
            self._loaded = True
        if count:
            logger.info(f"ConversationStore: loaded {count} conversations from disk")

    def _reconcile_list_cache_from_disk(self, user_id: str = "") -> None:
        """Ensure list_conversations includes conversation dirs created on disk.

        The warm cache is intentionally metadata-only and long-lived. Rewrite
        operations such as restart_from can invalidate one conversation cache
        entry while leaving the process loaded; the sidebar must still reflect
        the durable conversation directories on the next list request.
        """
        roots = []
        if user_id:
            roots.append(self._store_dir / user_id)
        else:
            try:
                roots.extend(p for p in self._store_dir.iterdir() if p.is_dir())
            except FileNotFoundError:
                return

        for user_dir in roots:
            if not user_dir.is_dir():
                continue
            uid = user_dir.name
            for conv_dir in user_dir.iterdir():
                if not conv_dir.is_dir():
                    continue
                if (not SegmentedJsonl(conv_dir / "transcript.jsonl").exists()
                        and not (conv_dir / "extras.json").exists()):
                    continue
                cid = conv_dir.name.replace("__", ":")
                with self._cache_lock:
                    cached = cid in self._cache
                if cached:
                    continue
                self._cid_user[cid] = uid
                self._load_cache_metadata(cid, uid)
