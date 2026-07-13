"""ConversationStore transcript load/page + display traces + context-usage."""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsTranscriptMixin:
    """transcript load/page + display traces + context-usage."""

    @staticmethod
    def _is_trace_update_row(row: Dict[str, Any]) -> bool:
        return row.get("t") == "trace_update"

    @staticmethod
    def _apply_trace_update(anchor: Dict[str, Any],
                            update: Dict[str, Any]) -> None:
        entry = update.get("entry") or {}
        content_update = update.get("content_update") or ""
        if entry:
            trace = list(anchor.get("trace") or [])
            trace.append(entry)
            anchor["trace"] = trace
        if content_update:
            anchor["content"] = (anchor.get("content") or "") + content_update

    @classmethod
    def _compose_display_traces(cls, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge append-only trace_update rows into their display trace anchor."""
        out: List[Dict[str, Any]] = []
        anchors: Dict[str, Dict[str, Any]] = {}
        pending: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            if cls._is_trace_update_row(row):
                trace_id = row.get("trace_id") or ""
                if not trace_id:
                    continue
                anchor = anchors.get(trace_id)
                if anchor is not None:
                    cls._apply_trace_update(anchor, row)
                else:
                    pending.setdefault(trace_id, []).append(row)
                continue
            if not row.get("role"):
                continue
            msg = dict(row)
            if msg.get("role") == "sub_agent_trace":
                trace_id = msg.get("trace_id") or ""
                if trace_id:
                    anchors[trace_id] = msg
                    for update in pending.pop(trace_id, []):
                        cls._apply_trace_update(msg, update)
            out.append(msg)
        return out

    def _scan_transcript(self, lines) -> List[Dict]:
        """Scan JSONL lines into canonical transcript messages."""
        rows = []
        for line in lines:
            if not line.get("role") and not self._is_trace_update_row(line):
                continue
            rows.append(dict(line))
        # Sort by (creation ts, creation seq) — see _read_ctx_file for
        # rationale. Same invariant: order = creation, not file position.
        rows.sort(key=lambda m: (
            m.get("timestamp") or m.get("ts") or 0.0,
            m.get("seq") or 0,
        ))
        return self._compose_display_traces(rows)

    def load(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        """Load entire transcript (all messages)."""
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        return self._read(cid, self._scan_transcript)

    def load_range_by_msg_id(self, cid: str,
                             from_msg_id: str,
                             to_msg_id: str,
                             user_id: str = "") -> Optional[List[Dict]]:
        """Load messages in [from_msg_id, to_msg_id] inclusive.

        Used by read_history(action="range") — drives the bucket nav hints
        that let an agent zoom from a bucket summary back to the exact
        original messages. Returns [] if either id is missing or out of
        order. Returns None when the conversation doesn't exist / the
        user doesn't own it.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        if not from_msg_id or not to_msg_id:
            return []
        all_msgs = self._read(cid, self._scan_transcript)
        if not all_msgs:
            return []
        start = end = -1
        for i, m in enumerate(all_msgs):
            mid = m.get("msg_id") if isinstance(m, dict) else getattr(m, "msg_id", "")
            if mid == from_msg_id and start < 0:
                start = i
            if mid == to_msg_id:
                end = i
        if start < 0 or end < 0 or end < start:
            return []
        return all_msgs[start:end + 1]

    def load_page(self, cid: str, limit: int = 50, offset: int = 0,
                  user_id: str = "", before_msg_id: str = "") -> Optional[Dict]:
        """Load a paginated slice of the transcript.

        Reads from the END of the JSONL file — only parses the lines needed.
        For a 2000-message conversation with limit=50, offset=0, this reads
        ~50 lines from the tail instead of scanning all 2000.
        When before_msg_id resolves, its exact position takes precedence over
        offset so callers can continue from a rendered message without drift.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        log = self._transcript_log(cid)
        total = self.message_count(cid)
        # _read_tail reads the file without holding the conv lock.
        # This avoids blocking _commit (set_status, append) while reading
        # large files. The file is append-only so reading stale data is safe
        # (we might miss the very last line, but that's acceptable for pagination).
        if not log.exists():
            if total > 0:
                cached = self._reload_cache(cid)
                self._persist_recomputed_hot_metadata(cid, cached)
            return {"messages": [], "total_count": 0, "offset": 0,
                    "limit": limit, "has_more": False}
        try:
            resolved_offset = offset
            if before_msg_id:
                cursor_offset = self._offset_after_msg_id(log, before_msg_id)
                if cursor_offset is not None:
                    resolved_offset = cursor_offset
            result = self._read_tail(log, total, limit, resolved_offset)
            if resolved_offset == 0 and total > 0 and not result.get("messages"):
                cached = self._reload_cache(cid)
                corrected_total = int(cached.get("msg_count") or 0)
                if corrected_total != total:
                    self._persist_recomputed_hot_metadata(cid, cached)
                    result = self._read_tail(log, corrected_total, limit, resolved_offset)
            return result
        except Exception as e:
            logger.error("[convstore] load_page failed %s: %s", cid, e)
            return {"messages": [], "total_count": total, "offset": offset,
                    "limit": limit, "has_more": False}

    @staticmethod
    def _offset_after_msg_id(log: SegmentedJsonl, msg_id: str) -> Optional[int]:
        """Return the number of message rows at or after the requested message."""
        offset = 0
        for line in log.iter_rows_reverse():
            if not line.get("role"):
                continue
            offset += 1
            if line.get("msg_id") == msg_id:
                return offset
        return None

    def _read_tail(self, log: SegmentedJsonl, total_msgs: int, limit: int, offset: int) -> Dict:
        """Read the last (offset + limit) display rows from a logical JSONL."""
        need = offset + limit + 20  # extra margin for detail-row alignment
        raw_lines = []
        display_seen = 0
        pending_trace_ids = set()
        for line in log.iter_rows_reverse():
            if self._is_trace_update_row(line):
                raw_lines.append(line)
                trace_id = line.get("trace_id") or ""
                if trace_id:
                    pending_trace_ids.add(trace_id)
                continue
            if line.get("role"):
                raw_lines.append(line)
                display_seen += 1
                if line.get("role") == "sub_agent_trace":
                    pending_trace_ids.discard(line.get("trace_id") or "")
                if display_seen >= need and not pending_trace_ids:
                    break
        raw_lines.reverse()

        msgs = self._compose_display_traces([dict(line) for line in raw_lines])

        # Slice: msgs is chronological, we want the last `limit` before `offset`
        total_tail = len(msgs)
        end = total_tail - offset
        start = max(0, end - limit)
        # Don't split technical child rows from their assistant anchor.
        while start > 0 and msgs[start].get("role") in ("thinking", "tool_call", "tool"):
            start -= 1
        page = msgs[start:end] if end > 0 else []
        has_more = (total_msgs - offset - len(page)) > 0

        return {"messages": page, "total_count": total_msgs,
                "offset": offset, "limit": limit, "has_more": has_more}

    def patch_message(self, cid: str, msg_id: str, **fields) -> None:
        """Update an existing message row in transcript and contexts."""
        if not msg_id or not fields:
            return
        patched_line: Dict[str, Any] = {}

        def _patch_stream(path: Path) -> int:
            nonlocal patched_line
            log = self._content_seg(cid, path)
            if not log.exists():
                return 0
            patched = log.patch_first_by_msg_id(msg_id, fields)
            if not patched:
                return 0
            if not patched_line:
                patched_line = patched
            return 1

        lock = self._get_conv_lock(cid)
        with lock:
            _patch_stream(self._transcript_path(cid))
            _patch_stream(self._shared_ctx_path(cid))
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        _patch_stream(entry / "context.jsonl")
        self._invalidate_ctx_cache(cid)
        if patched_line:
            self._notify_bg_transcript_chars(
                cid, self._row_payload_chars(patched_line))
            self._maybe_persist_context_usage_from_patch(cid, patched_line)

    def _maybe_persist_context_usage_from_patch(self, cid: str, line: Dict[str, Any]) -> None:
        source = line.get("source")
        entry = self._context_usage_entry_from_source(source, line.get("ts"))
        if not entry:
            return
        name, usage_entry = entry
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            self._merge_context_usage_locked(cid, data, name, usage_entry)

    @staticmethod
    def _context_usage_entry_from_source(source: Any, ts: Any = None):
        if not isinstance(source, dict):
            return None
        name = source.get("name") or source.get("agent")
        used = source.get("context_used")
        max_tokens = source.get("context_max")
        if not name or used is None or max_tokens is None:
            return None
        try:
            used_i = int(used)
            max_i = int(max_tokens)
        except (TypeError, ValueError):
            return None
        if max_i <= 0:
            return None
        pct = source.get("context_pct")
        try:
            pct_f = float(pct) if pct is not None else used_i / max_i
        except (TypeError, ValueError):
            pct_f = used_i / max_i
        try:
            ts_f = float(ts) if ts is not None else time.time()
        except (TypeError, ValueError):
            ts_f = time.time()
        return name, {"used": used_i, "max": max_i, "pct": pct_f, "updated_at": ts_f}

    def _merge_context_usage_locked(self, cid: str, data: Dict[str, Any],
                                    name: str, usage_entry: Dict[str, Any]) -> bool:
        usage = dict(data.get("context_usage") or {})
        prev = usage.get(name)
        if isinstance(prev, dict) and float(prev.get("updated_at") or 0) > float(usage_entry.get("updated_at") or 0):
            return False
        usage[name] = usage_entry
        data["context_usage"] = usage
        self._write_extras(cid, data)

        with self._cache_lock:
            if cid in self._cache:
                self._cache[cid]["extra_keys"].add("context_usage")
                self._cache[cid].setdefault("extras", {})["context_usage"] = usage
                self._cache[cid]["updated_at"] = time.time()
        return True

    def _scan_context_usage_from_transcript(self, cid: str,
                                            usage: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        """Scan transcript context usage without holding the conv lock."""
        usage = dict(usage or {})
        log = self._transcript_log(cid)
        transcript_mtime = log.latest_mtime()
        if not transcript_mtime:
            return usage, 0.0
        if self._context_usage_repair_mtime.get(cid, 0) >= transcript_mtime:
            return usage, transcript_mtime
        for line in log.iter_rows():
            entry = self._context_usage_entry_from_source(
                line.get("source"), line.get("ts"))
            if not entry:
                continue
            name, usage_entry = entry
            prev = usage.get(name)
            if (not isinstance(prev, dict)
                    or float(prev.get("updated_at") or 0) <= float(usage_entry.get("updated_at") or 0)):
                usage[name] = usage_entry
        return usage, transcript_mtime

    def _repair_context_usage_from_transcript(self, cid: str,
                                              data: Dict[str, Any]) -> Dict[str, Any]:
        usage = dict(data.get("context_usage") or {})
        usage, transcript_mtime = self._scan_context_usage_from_transcript(
            cid, usage)
        if not transcript_mtime:
            return usage
        self._context_usage_repair_mtime[cid] = transcript_mtime
        if usage != data.get("context_usage"):
            lock = self._get_extras_lock(cid)
            with lock:
                latest = self._merge_hot_metadata_snapshot(
                    cid, self._read_extras(cid))
                latest_usage = dict(latest.get("context_usage") or {})
                for name, usage_entry in usage.items():
                    prev = latest_usage.get(name)
                    if (not isinstance(prev, dict)
                            or float(prev.get("updated_at") or 0) <= float(usage_entry.get("updated_at") or 0)):
                        latest_usage[name] = usage_entry
                usage = latest_usage
                if latest_usage != latest.get("context_usage"):
                    latest["context_usage"] = latest_usage
                    self._write_extras(cid, latest)
                    with self._cache_lock:
                        if cid in self._cache:
                            self._cache[cid]["extra_keys"].add("context_usage")
                            self._cache[cid].setdefault("extras", {})["context_usage"] = usage
                            self._cache[cid]["updated_at"] = time.time()
        return usage
