"""ConversationStore transcript load/page + display traces + context-usage."""

import json
import logging
import shutil
import subprocess  # nosec B404 -- fixed argv, no shell
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

    def load_delegate_state(self, cid: str, caller: str, user_id: str = "",
                            finished_limit: int = 100,
                            response_limit: int = 200_000) -> Dict[str, List[Dict]]:
        """Rebuild one caller's delegate state from the append-only transcript.

        Shared delegate requests and replies already carry the same task_id.
        Isolated and flash delegates persist a sub_agent_trace anchor followed
        by trace_update rows. Streaming those rows is the durable counterpart
        to the process-local live registry and finished-result ring: context
        compaction or a runtime restart cannot make an acknowledged delegate
        disappear from delegate_status or delegate_result.

        The scan is O(transcript rows) but keeps only delegate state in memory;
        it never materializes the full conversation.
        """
        empty = {"live": [], "finished": []}
        if not caller or not self.exists(cid):
            return empty
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return empty
        log = self._transcript_log(cid)
        if not log.exists():
            return empty

        caller_key = str(caller).casefold()
        response_limit = max(0, int(response_limit))
        finished_limit = max(0, int(finished_limit))
        active: Dict[str, Dict[str, Any]] = {}
        traces: Dict[str, Dict[str, Any]] = {}
        finished: Dict[str, Dict[str, Any]] = {}
        order = 0

        def _time(value: Dict[str, Any]) -> float:
            try:
                return float(value.get("timestamp") or value.get("ts") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def _text(value: Any) -> str:
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return "".join(
                    str(block.get("text") or "")
                    for block in value if isinstance(block, dict)
                    and block.get("type") == "text"
                )
            return str(value or "")

        def _finish(task_id: str, target: str, status: str, error: str,
                    response: str, finished_at: float, mode: str) -> None:
            nonlocal order
            started = active.pop(task_id, None) or {}
            trace = traces.pop(task_id, None) or {}
            started_at = float(
                started.get("started_at") or trace.get("started_at") or 0.0)
            target = str(
                target or started.get("target") or trace.get("target") or "")
            mode = str(mode or started.get("mode") or trace.get("mode")
                       or "isolated")
            duration_ms = (
                max(0.0, finished_at - started_at) * 1000
                if started_at and finished_at else 0.0
            )
            order += 1
            finished[task_id] = {
                "caller": caller,
                "target": target,
                "task_id": task_id,
                "status": str(status or "completed"),
                "error": str(error or "")[:500],
                "duration_ms": duration_ms,
                "finished_at": finished_at,
                "response": str(response or "")[:response_limit],
                "mode": mode,
                "_order": order,
            }

        for row in log.iter_rows():
            if self._is_trace_update_row(row):
                task_id = str(row.get("trace_id") or "")
                trace = traces.get(task_id)
                if trace is None:
                    continue
                content_update = _text(row.get("content_update"))
                if content_update:
                    trace["response"] = (
                        str(trace.get("response") or "") + content_update
                    )[:response_limit]
                entry = row.get("entry") or {}
                if isinstance(entry, dict) and entry.get("type") == "done":
                    _finish(
                        task_id,
                        str(trace.get("target") or ""),
                        str(entry.get("status") or "completed"),
                        str(entry.get("error") or ""),
                        str(trace.get("response") or ""),
                        _time(entry) or _time(row),
                        str(trace.get("mode") or "isolated"),
                    )
                continue

            source = row.get("source") or {}
            if not isinstance(source, dict):
                continue
            role = str(row.get("role") or "")
            if role == "sub_agent_trace":
                if str(source.get("parent_agent") or "").casefold() != caller_key:
                    continue
                task_id = str(
                    source.get("task_id") or row.get("trace_id") or "")
                if not task_id:
                    continue
                target = str(source.get("name") or "")
                mode = "flash" if "::flash::" in target else "isolated"
                finished.pop(task_id, None)
                trace = {
                    "caller": caller,
                    "target": target,
                    "task_id": task_id,
                    "started_at": _time(row),
                    "pending_messages": 0,
                    "mode": mode,
                    "status": "pending",
                    "response": _text(row.get("content"))[:response_limit],
                }
                traces[task_id] = trace
                active[task_id] = {
                    key: value for key, value in trace.items()
                    if key != "response"
                }
                for entry in row.get("trace") or []:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("type") == "done":
                        _finish(
                            task_id, target,
                            str(entry.get("status") or "completed"),
                            str(entry.get("error") or ""),
                            str(trace.get("response") or ""),
                            _time(entry), mode,
                        )
                continue

            if source.get("type") != "agent_delegate":
                continue
            task_id = str(source.get("task_id") or "")
            if not task_id:
                continue
            is_reply = source.get("kind") == "reply"
            if (not is_reply
                    and str(source.get("from") or "").casefold() == caller_key):
                target = str(
                    source.get("to") or source.get("target_agent") or "")
                finished.pop(task_id, None)
                active[task_id] = {
                    "caller": caller,
                    "target": target,
                    "task_id": task_id,
                    "started_at": _time(row),
                    "pending_messages": 0,
                    "mode": "shared",
                    "status": "pending",
                }
            elif (is_reply
                  and str(source.get("to") or "").casefold() == caller_key):
                _finish(
                    task_id,
                    str(source.get("from") or ""),
                    "completed",
                    "",
                    _text(row.get("content")),
                    _time(row),
                    "shared",
                )

        ordered_finished = sorted(
            finished.values(),
            key=lambda item: (item.get("finished_at") or 0.0,
                              item.get("_order") or 0),
        )
        if finished_limit:
            ordered_finished = ordered_finished[-finished_limit:]
        else:
            ordered_finished = []
        for item in ordered_finished:
            item.pop("_order", None)
        return {
            "live": sorted(
                active.values(),
                key=lambda item: item.get("started_at") or 0.0,
            ),
            "finished": ordered_finished,
        }

    def load_range_by_msg_id(self, cid: str,
                             from_msg_id: str,
                             to_msg_id: str,
                             user_id: str = "",
                             max_messages: int = 0) -> Optional[List[Dict]]:
        """Load messages in [from_msg_id, to_msg_id] inclusive.

        Used by read_history(action="range") — drives the bucket nav hints
        that let an agent zoom from a bucket summary back to the exact
        original messages. Returns [] if either id is missing or out of
        order. Returns None when the conversation doesn't exist / the
        user doesn't own it.

        Bounded. Rows are collected only between the two anchors, the scan
        stops at the closing one, and ``max_messages`` caps what is ever held
        at once. This used to materialise the entire transcript just to slice
        it: on a 257k-message conversation that is hundreds of megabytes and
        a full sort per call, and read_history is called in chains -- a
        handful of them was enough to bring the server to its knees.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        if not from_msg_id or not to_msg_id:
            return []
        log = self._transcript_log(cid)
        if not log.exists():
            return []
        raw: List[Dict[str, Any]] = []
        inside = False
        closed = False
        for row in log.iter_rows():
            if self._is_trace_update_row(row):
                if inside:
                    raw.append(row)
                continue
            if not row.get("role"):
                continue
            # Break on the display row *after* the closing anchor, so the
            # trace_update rows that trail it are still absorbed.
            if closed:
                break
            if not inside:
                if row.get("msg_id") != from_msg_id:
                    continue
                inside = True
            if max_messages <= 0 or len(raw) < max_messages:
                raw.append(row)
            if row.get("msg_id") == to_msg_id:
                closed = True
        # An unterminated range is not a partial answer: to_msg_id missing or
        # sitting before from_msg_id both mean the caller's anchors are wrong.
        if not inside or not closed:
            return []
        return self._compose_display_traces(raw)

    #: Display rows composed at once by the bounded readers below. Large
    #: enough that a trace_update rarely falls outside its anchor's window,
    #: small enough that reading the largest transcript on the instance costs
    #: this many rows of memory instead of all of them.
    _WINDOW_CHUNK = 2000

    def iter_display_windows(self, cid: str, chunk: int = 0):
        """Yield ``(start_index, messages)`` across the whole transcript.

        The bounded counterpart of :meth:`load`, for the callers that really
        must look at every message -- a search, a date range, a filtered
        count. They keep only what they return, so walking the largest
        conversation costs one window, not the conversation.

        ``start_index`` is the absolute display index of the window's first
        message, which is what the callers render as ``[#n]``.
        """
        chunk = int(chunk or self._WINDOW_CHUNK)
        if chunk <= 0 or not self.exists(cid):
            return
        log = self._transcript_log(cid)
        if not log.exists():
            return
        raw: List[Dict[str, Any]] = []
        seen = 0
        start_index = 0
        for row in log.iter_rows():
            if self._is_trace_update_row(row):
                raw.append(row)
                continue
            if not row.get("role"):
                continue
            raw.append(row)
            seen += 1
            if seen >= chunk:
                msgs = self._compose_display_traces(raw)
                yield start_index, msgs
                start_index += len(msgs)
                raw = []
                seen = 0
        if seen:
            yield start_index, self._compose_display_traces(raw)

    def iter_display_search_windows(self, cid: str, terms: List[str]):
        """Return plaintext windows whose segment contains a search term.

        ``None`` requests the exact fallback for encrypted rows. Candidate rows
        still pass through decoding, sanitizing and composing.
        """
        if not self.exists(cid):
            return iter(())
        log = self._transcript_log(cid)
        if log.codec is not None:
            return None
        paths = log.iter_paths()
        if not paths:
            return iter(())
        patterns = []
        for term in terms:
            term = str(term or "")
            if not term:
                continue
            # Match the representation written by json.dumps, including quotes,
            # newlines and backslashes, without putting user text in argv.
            patterns.append(json.dumps(term, ensure_ascii=False)[1:-1])
        if not patterns:
            return iter(())
        lowered = [pattern.lower() for pattern in patterns]
        ascii_patterns = all(pattern.isascii() for pattern in lowered)
        matched = None
        searcher = shutil.which("rg")
        if searcher:
            argv = [searcher, "--files-with-matches", "--fixed-strings",
                    "--ignore-case", "--no-messages", "--file", "-"]
        else:
            searcher = shutil.which("grep") if ascii_patterns else None
            argv = ([searcher, "-F", "-i", "-l", "-f", "-"]
                    if searcher else [])
        if searcher:
            try:
                result = subprocess.run(  # nosec B603 -- fixed executable + argv
                    argv + [str(path) for path in paths],
                    input="\n".join(patterns) + "\n",
                    capture_output=True, text=True, timeout=10, check=False)
                if result.returncode in (0, 1):
                    matched = {line.strip() for line in result.stdout.splitlines()
                               if line.strip()}
                else:
                    logger.debug("history search prefilter failed: %s",
                                 result.stderr.strip())
            except (OSError, subprocess.SubprocessError):
                logger.debug("history search prefilter unavailable",
                             exc_info=True)
        if matched is None:
            needles = ([pattern.encode("ascii") for pattern in lowered]
                       if ascii_patterns else lowered)
            matched = set()
            try:
                for path in paths:
                    haystack = (path.read_bytes().lower() if ascii_patterns
                                else path.read_text(
                                    encoding="utf-8", errors="replace").lower())
                    if any(needle in haystack for needle in needles):
                        matched.add(str(path))
            except OSError:
                logger.debug("history search Python prefilter failed",
                             exc_info=True)
                return None
        if not matched:
            return iter(())
        selected = {i for i, path in enumerate(paths) if str(path) in matched}
        selected |= {i - 1 for i in selected if i}
        # A version-1 index is upgraded here. Share the append lock while its
        # active-segment count is measured and persisted so no row can land
        # between those operations.
        with self._get_conv_lock(cid):
            role_rows = log.role_rows_by_path()

        def _windows():
            from core.secret_sanitization import strip_secret_runtime_values

            start_index = 0
            last_match = max(selected)
            raw: List[Dict[str, Any]] = []
            seen = 0
            for i, path in enumerate(paths):
                if i > last_match:
                    break
                if i not in selected:
                    if seen:
                        messages = self._compose_display_traces(raw)
                        yield start_index, messages
                        start_index += len(messages)
                        raw = []
                        seen = 0
                    start_index += int(role_rows.get(path, 0))
                    continue
                for row in log._iter_file(path):
                    if self._is_trace_update_row(row):
                        raw.append(strip_secret_runtime_values(row))
                        continue
                    if not row.get("role"):
                        continue
                    raw.append(strip_secret_runtime_values(row))
                    seen += 1
                    if seen >= self._WINDOW_CHUNK:
                        messages = self._compose_display_traces(raw)
                        yield start_index, messages
                        start_index += len(messages)
                        raw = []
                        seen = 0
            if seen:
                yield start_index, self._compose_display_traces(raw)

        return _windows()

    def load_window_by_index(self, cid: str, start: int, count: int) -> List[Dict]:
        """``count`` display messages from absolute display index ``start``.

        Reads forward and stops as soon as the window is closed, so the cost
        is the distance to the window plus its size -- never the whole file.
        """
        if count <= 0 or start < 0 or not self.exists(cid):
            return []
        log = self._transcript_log(cid)
        if not log.exists():
            return []
        raw: List[Dict[str, Any]] = []
        idx = 0
        taken = 0
        for row in log.iter_rows():
            if self._is_trace_update_row(row):
                if taken:
                    raw.append(row)
                continue
            if not row.get("role"):
                continue
            if taken >= count:
                break
            if idx >= start:
                raw.append(row)
                taken += 1
            idx += 1
        return self._compose_display_traces(raw)

    def find_display_index(self, cid: str, msg_id: str = "", seq: int = 0,
                           ts: float = 0.0, backward: bool = False) -> int:
        """Absolute display index of an anchor, or -1 when there is none.

        Exactly one anchor is used, in the order msg_id, seq, ts. An exact
        ``seq`` wins; failing that ``backward`` picks the last row at or
        before it and forward picks the first at or after. Costs one streaming
        pass and O(1) memory: the row is tested and dropped.
        """
        if not self.exists(cid):
            return -1
        log = self._transcript_log(cid)
        if not log.exists():
            return -1
        idx = -1
        best = -1
        for row in log.iter_rows():
            if self._is_trace_update_row(row) or not row.get("role"):
                continue
            idx += 1
            if msg_id:
                if row.get("msg_id") == msg_id:
                    return idx
                continue
            if seq:
                row_seq = int(row.get("seq") or 0)
                if row_seq == seq:
                    return idx
                if not row_seq:
                    continue
                if backward:
                    if row_seq <= seq:
                        best = idx
                elif row_seq >= seq:
                    return idx
                continue
            if ts and float(row.get("ts") or row.get("timestamp") or 0.0) >= ts:
                return idx
        return best

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
        patched_streams = 0
        transcript_patched = 0
        with lock:
            transcript_patched = _patch_stream(self._transcript_path(cid))
            patched_streams += transcript_patched
            patched_streams += _patch_stream(self._shared_ctx_path(cid))
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        patched_streams += _patch_stream(entry / "context.jsonl")
            if transcript_patched:
                self.bump_transcript_generation(cid)
        if not patched_streams:
            # Callers patch durable markers (turn_final, gauges) that later
            # reconstruction depends on. Matching no row is a silent data loss,
            # not a no-op — say so.
            logger.warning("[conv-store:%s] patch_message matched no row for "
                           "msg_id=%s fields=%s", cid[:8], msg_id,
                           sorted(fields))
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
