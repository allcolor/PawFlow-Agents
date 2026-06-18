"""Segmented JSONL storage helpers."""

from __future__ import annotations
import logging

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

DEFAULT_MAX_ROWS = int(os.getenv("PAWFLOW_JSONL_SEGMENT_ROWS", "5000") or "5000")
DEFAULT_MAX_BYTES = int(os.getenv("PAWFLOW_JSONL_SEGMENT_BYTES", str(8 * 1024 * 1024)) or str(8 * 1024 * 1024))
_INDEX_NAME = "index.json"
_APPEND_DIAG_MS = float(os.getenv("PAWFLOW_JSONL_APPEND_DIAG_MS", "20") or "20")
_INDEX_CACHE_MAX = int(os.getenv("PAWFLOW_JSONL_INDEX_CACHE_MAX", "256") or "256")
_APPEND_HANDLE_MAX = int(os.getenv("PAWFLOW_JSONL_APPEND_HANDLE_MAX", "128") or "128")
_APPEND_BUFFER_BYTES = int(os.getenv("PAWFLOW_JSONL_APPEND_BUFFER_BYTES", str(1024 * 1024)) or str(1024 * 1024))
_INDEX_FLUSH_ROWS = int(os.getenv("PAWFLOW_JSONL_INDEX_FLUSH_ROWS", "64") or "64")
_INDEX_FLUSH_SECONDS = float(os.getenv("PAWFLOW_JSONL_INDEX_FLUSH_SECONDS", "60.0") or "60.0")
_INDEX_CACHE: Dict[str, Dict[str, Any]] = {}
_INDEX_CACHE_LOCK = threading.RLock()
_APPEND_HANDLES: Dict[str, Dict[str, Any]] = {}
_APPEND_HANDLES_LOCK = threading.RLock()

from core._segmented_jsonl_io import _SegmentedJsonlIOMixin  # noqa: E402




class SegmentedJsonl(_SegmentedJsonlIOMixin):
    """Read/write one logical JSONL stream stored in bounded segment files."""

    def __init__(self, flat_path: Path, max_rows: int = DEFAULT_MAX_ROWS,
                 max_bytes: int = DEFAULT_MAX_BYTES, codec: Any = None):
        self.flat_path = Path(flat_path)
        self.segment_dir = self.flat_path.with_suffix("")
        self.index_path = self.segment_dir / _INDEX_NAME
        self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))
        self.max_bytes = max(1, int(max_bytes or DEFAULT_MAX_BYTES))
        # Optional row codec (e.g. core.conversation_cipher.RowCodec): when
        # set, content fields are encrypted on write and decrypted on read so
        # every path through this class is consistent. Metadata stays clear,
        # so msg_id-keyed operations (truncate/patch/delete) still work without
        # the key. None == passthrough (identical to the unencrypted path).
        self.codec = codec

    def is_segmented(self) -> bool:
        return self.index_path.exists() or self.segment_dir.is_dir()

    def exists(self) -> bool:
        return self.is_segmented()

    def iter_paths(self) -> List[Path]:
        self._flush_own_append_handles()
        return self._segment_paths()

    def iter_rows(self) -> Iterator[Dict[str, Any]]:
        codec = self.codec
        for path in self.iter_paths():
            for row in self._iter_file(path):
                yield codec.decode(row) if codec is not None else row

    def iter_rows_reverse(self) -> Iterator[Dict[str, Any]]:
        self._flush_own_append_handles()
        codec = self.codec
        paths = self._segment_paths()
        for path in reversed(paths):
            for row in self._iter_file_reverse(path):
                yield codec.decode(row) if codec is not None else row

    def append_dicts(self, rows: Iterable[Dict[str, Any]]) -> None:
        if self.codec is not None:
            rows = [self.codec.encode(row) for row in rows]
        lines = [json.dumps(row, ensure_ascii=False) + "\n" for row in rows]
        if not lines:
            return
        self.append_lines(lines)

    def append_lines(self, lines: Iterable[str]) -> None:
        started = time.monotonic()
        timings: Dict[str, float] = {}

        def mark(name: str, t0: float) -> None:
            timings[name] = timings.get(name, 0.0) + ((time.monotonic() - t0) * 1000.0)

        lines = [line if line.endswith("\n") else line + "\n" for line in lines]
        if not lines:
            return
        line_sizes = [len(line.encode("utf-8")) for line in lines]
        total_bytes = sum(line_sizes)
        cache_key = self._cache_key()
        t0 = time.monotonic()
        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(cache_key)
            if cached is not None:
                cached["last_used"] = time.monotonic()
        mark("cache", t0)
        # The hot path must not touch filesystem metadata when the index is
        # already cached. Python running on Windows against a WSL UNC path can
        # spend 100ms+ in a single Path.exists() call even when the later
        # append itself is sub-millisecond.
        index_exists = False
        index_missing = False
        segment_dir_exists = False
        if cached is None:
            index_exists = self.index_path.exists()
            index_missing = not index_exists
            segment_dir_exists = self.segment_dir.is_dir()

        if cached is None:
            t0 = time.monotonic()
            self.segment_dir.mkdir(parents=True, exist_ok=True)
            if index_missing and not segment_dir_exists:
                index = {
                    "version": 1,
                    "max_rows": self.max_rows,
                    "max_bytes": self.max_bytes,
                    "segments": [],
                    "total_rows": 0,
                }
                self._remember_index(index)
            else:
                index = self._load_index()
            mark("load_index", t0)
        else:
            index = cached["index"]
        created_segment = False
        pending_by_path: Dict[Path, List[str]] = {}
        for line, line_bytes in zip(lines, line_sizes):
            t0 = time.monotonic()
            before = len(index.get("segments") or [])
            current = self._current_segment(index, next_bytes=line_bytes)
            mark("current_segment", t0)
            created_segment = created_segment or len(index.get("segments") or []) > before
            path = self.segment_dir / current["file"]
            pending_by_path.setdefault(path, []).append(line)
            current["rows"] = int(current.get("rows") or 0) + 1
            current["bytes"] = int(current.get("bytes") or 0) + line_bytes
            index["total_rows"] = int(index.get("total_rows") or 0) + 1
        append_detail = {
            "handle_cache": 0.0,
            "handle_open": 0.0,
            "handle_lock_wait": 0.0,
            "write_call": 0.0,
            "buffer_flush": 0.0,
        }
        for path, path_lines in pending_by_path.items():
            t0 = time.monotonic()
            self._append_lines_to_path(
                path, path_lines, append_detail, ensure_parent=False)
            mark("write", t0)
        for key, value in append_detail.items():
            timings[key] = timings.get(key, 0.0) + value
        t0 = time.monotonic()
        self._remember_index(index)
        mark("remember_index", t0)
        t0 = time.monotonic()
        self._maybe_write_index_hot(index, force=(index_missing or created_segment))
        mark("index_write", t0)
        self._log_append_diag(started, len(lines), total_bytes, timings)

    def _log_append_diag(self, started: float, rows: int, total_bytes: int,
                         timings: Dict[str, float]) -> None:
        total_ms = (time.monotonic() - started) * 1000.0
        if total_ms < _APPEND_DIAG_MS:
            return
        logging.getLogger(__name__).warning(
            "[segjsonl] append slow path=%s rows=%d bytes=%d total_ms=%.1f "
            "cache=%.1f load_index=%.1f current_segment=%.1f "
            "write=%.1f handle_cache=%.1f handle_open=%.1f "
            "handle_lock_wait=%.1f write_call=%.1f buffer_flush=%.1f "
            "remember_index=%.1f index_write=%.1f",
            str(self.flat_path), rows, total_bytes, total_ms,
            timings.get("cache", 0.0),
            timings.get("load_index", 0.0),
            timings.get("current_segment", 0.0),
            timings.get("write", 0.0),
            timings.get("handle_cache", 0.0),
            timings.get("handle_open", 0.0),
            timings.get("handle_lock_wait", 0.0),
            timings.get("write_call", 0.0),
            timings.get("buffer_flush", 0.0),
            timings.get("remember_index", 0.0),
            timings.get("index_write", 0.0),
        )

    def replace_dicts(self, rows: Iterable[Dict[str, Any]]) -> None:
        if self.codec is not None:
            rows = (self.codec.encode(row) for row in rows)
        self.replace_lines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    def truncate_after_msg_id(self, msg_id: str) -> Dict[str, Any]:
        """Keep rows through ``msg_id`` and discard everything after it.

        This is the hot path for restart-from. For segmented logs it searches
        segments from the tail, rewrites only the segment that contains the
        boundary row, and unlinks later segments. It avoids materializing the
        whole logical stream just to drop a suffix.
        """
        msg_id = str(msg_id or "").strip()
        if not msg_id or not self.exists():
            return {"found": False, "kept_rows": 0, "boundary": None}

        self._flush_own_append_handles()

        paths = sorted(self.segment_dir.glob("*.jsonl")) if self.segment_dir.is_dir() else []
        if not paths:
            return {"found": False, "kept_rows": 0, "boundary": None}

        index = self._load_index()
        indexed = {
            str(item.get("file") or ""): dict(item)
            for item in index.get("segments") or []
        }

        target_idx = -1
        target_rows: List[Dict[str, Any]] = []
        boundary = None
        boundary_row_idx = -1
        for idx in range(len(paths) - 1, -1, -1):
            rows = list(self._iter_file(paths[idx]))
            for row_idx in range(len(rows) - 1, -1, -1):
                row = rows[row_idx]
                if row.get("msg_id") == msg_id:
                    target_idx = idx
                    target_rows = rows
                    boundary = row
                    boundary_row_idx = row_idx
                    break
            if target_idx >= 0:
                break

        if target_idx < 0:
            return {"found": False, "kept_rows": 0, "boundary": None}

        kept_target_rows = target_rows[:boundary_row_idx + 1]
        target_path = paths[target_idx]
        self._replace_rows_in_path(target_path, kept_target_rows)

        for path in paths[target_idx + 1:]:
            self._close_append_handles(path)
            try:
                path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

        segments = []
        total_rows = 0
        for path in paths[:target_idx]:
            item = indexed.get(path.name)
            if item is None:
                item = {
                    "file": path.name,
                    "rows": sum(1 for _ in self._iter_file(path)),
                    "bytes": path.stat().st_size if path.exists() else 0,
                }
            else:
                item["file"] = path.name
            total_rows += int(item.get("rows") or 0)
            segments.append(item)

        target_item = {
            "file": target_path.name,
            "rows": len(kept_target_rows),
            "bytes": target_path.stat().st_size if target_path.exists() else 0,
        }
        total_rows += len(kept_target_rows)
        segments.append(target_item)

        new_index = {
            "version": 1,
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "segments": segments,
            "total_rows": total_rows,
        }
        self._remember_index(new_index, flushed=True)
        self._write_index(new_index)
        return {"found": True, "kept_rows": total_rows, "boundary": boundary}

    def prewarm_append(self) -> None:
        """Create the first append handle before a latency-sensitive write."""
        cache_key = self._cache_key()
        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(cache_key)
            index = cached["index"] if cached is not None else None
        if index is None:
            self.segment_dir.mkdir(parents=True, exist_ok=True)
            index = self._load_index()
        else:
            self.segment_dir.mkdir(parents=True, exist_ok=True)
        current = self._current_segment(index)
        # If prewarm creates an empty segment, persist the matching empty index
        # now. Otherwise the next reader/seed path sees a segment directory
        # with no index and falls back to glob/rebuild work in a user path.
        self._remember_index(index, flushed=True)
        if not self.index_path.exists():
            self._write_index_hot(index)
        self._append_lines_to_path(
            self.segment_dir / current["file"], [], ensure_parent=False)

    def delete(self) -> None:
        self._close_append_handles(self.segment_dir)
        self._close_append_handles(self.flat_path)
        if self.flat_path.exists():
            self.flat_path.unlink()
        if self.segment_dir.exists():
            shutil.rmtree(self.segment_dir)
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE.pop(self._cache_key(), None)

    def replace_lines(self, lines: Iterable[str]) -> None:
        self._close_append_handles(self.segment_dir)
        self._close_append_handles(self.flat_path)
        self.flat_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.segment_dir.with_name(self.segment_dir.name + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        index = {
            "version": 1,
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "segments": [],
            "total_rows": 0,
        }
        pending_by_path: Dict[Path, List[str]] = {}
        for line in lines:
            line = line if line.endswith("\n") else line + "\n"
            line_bytes = len(line.encode("utf-8"))
            current = self._current_segment(index, tmp_dir, next_bytes=line_bytes)
            pending_by_path.setdefault(tmp_dir / current["file"], []).append(line)
            current["rows"] = int(current.get("rows") or 0) + 1
            current["bytes"] = int(current.get("bytes") or 0) + line_bytes
            index["total_rows"] = int(index.get("total_rows") or 0) + 1
        for path, path_lines in pending_by_path.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(path_lines)
        (tmp_dir / _INDEX_NAME).write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.segment_dir.exists():
            shutil.rmtree(self.segment_dir)
        tmp_dir.replace(self.segment_dir)
        if self.flat_path.exists():
            self.flat_path.unlink()
        self._remember_index(index, flushed=True)

    def rewrite(self, transform: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> int:
        changed = 0
        out: List[Dict[str, Any]] = []
        for row in self.iter_rows():
            new_row = transform(dict(row))
            if new_row is None:
                changed += 1
                continue
            if new_row != row:
                changed += 1
            out.append(new_row)
        if changed:
            self.replace_dicts(out)
        return changed

    def patch_first_by_msg_id(self, msg_id: str,
                              fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Patch one message row without rewriting every segment."""
        if not msg_id or not fields or not self.exists():
            return None
        paths = self._segment_paths()
        for path in reversed(paths):
            self.flush_append_handles(path)
            rows = list(self._iter_file(path))
            patched: Optional[Dict[str, Any]] = None
            changed = False
            codec = self.codec
            for idx, row in enumerate(rows):
                if row.get("msg_id") != msg_id:
                    continue
                # Work in decoded (plaintext) space so caller-supplied fields
                # merge correctly and change-detection is logical, then
                # re-encode for storage. msg_id is clear, so the match above
                # needs no key.
                decoded = codec.decode(row) if codec is not None else row
                updated = dict(decoded)
                updated.update(fields)
                changed = updated != decoded
                rows[idx] = codec.encode(updated) if codec is not None else updated
                patched = updated
                break
            if patched is None:
                continue
            if changed:
                self._replace_rows_in_path(path, rows)
            return patched
        return None

    def delete_by_msg_ids(self, msg_ids: set) -> int:
        """Delete rows matching msg_id/trace_id, rewriting touched segments only."""
        targets = {str(mid) for mid in (msg_ids or set()) if str(mid)}
        if not targets or not self.exists():
            return 0
        paths = self._segment_paths()
        deleted = 0
        for path in paths:
            self.flush_append_handles(path)
            rows = list(self._iter_file(path))
            kept = [
                row for row in rows
                if row.get("msg_id") not in targets
                and row.get("trace_id") not in targets
            ]
            if len(kept) == len(rows):
                continue
            deleted += len(rows) - len(kept)
            self._replace_rows_in_path(path, kept)
        return deleted

    def total_rows(self) -> int:
        self._flush_own_append_handles()
        return int(self._rebuild_index_from_segments().get("total_rows") or 0)

    def latest_mtime(self) -> float:
        self._flush_own_append_handles()
        mtimes = [p.stat().st_mtime for p in self.iter_paths() if p.exists()]
        if self.index_path.exists():
            mtimes.append(self.index_path.stat().st_mtime)
        return max(mtimes) if mtimes else 0.0

    def _flush_own_append_handles(self) -> None:
        self.flush_append_handles(self.segment_dir)
        self.flush_append_handles(self.flat_path)

    def _load_index(self) -> Dict[str, Any]:
        cache_key = self._cache_key()
        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(cache_key)
            if cached is not None:
                return cached["index"]
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("version", 1)
                    data.setdefault("max_rows", self.max_rows)
                    data.setdefault("max_bytes", self.max_bytes)
                    data.setdefault("segments", [])
                    data.setdefault("total_rows", sum(int(s.get("rows") or 0) for s in data.get("segments") or []))
                    self._remember_index(data, flushed=True)
                    return data
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        data = {
            "version": 1,
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "segments": [],
            "total_rows": 0,
        }
        self._remember_index(data)
        return data

    def _rebuild_index_from_segments(self) -> Dict[str, Any]:
        segments = []
        if self.segment_dir.is_dir():
            for path in sorted(self.segment_dir.glob("*.jsonl")):
                segments.append({
                    "file": path.name,
                    "rows": sum(1 for _ in self._iter_file(path)),
                    "bytes": path.stat().st_size,
                })
        data = {
            "version": 1,
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "segments": segments,
            "total_rows": sum(s["rows"] for s in segments),
        }
        self._remember_index(data, flushed=True)
        if segments and not self._defer_hot_index_writes():
            self._write_index_hot(data)
        return data

    def _cache_key(self) -> str:
        return str(self.segment_dir)

    @staticmethod
    def _append_lines_to_path(path: Path, lines: List[str],
                              timings: Optional[Dict[str, float]] = None,
                              ensure_parent: bool = True) -> None:
        """Append with a hot file handle.

        On Windows/WSL mounts, repeated tiny writes through the host path can
        stall. Keep one buffered append handle per segment path and flush only
        when the buffer fills, when a reader needs current contents, or before
        the writer publishes SSE events.
        """
        key = str(path)
        started = time.monotonic()
        with _APPEND_HANDLES_LOCK:
            state = _APPEND_HANDLES.get(key)
            if state is None or getattr(state.get("fh"), "closed", True):
                open_started = time.monotonic()
                if ensure_parent:
                    path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "fh": open(path, "ab", buffering=max(0, _APPEND_BUFFER_BYTES)),
                    "lock": threading.RLock(),
                    "last_used": time.monotonic(),
                    "buffered_bytes": 0,
                }
                _APPEND_HANDLES[key] = state
                SegmentedJsonl._trim_append_handles_locked()
                if timings is not None:
                    timings["handle_open"] = timings.get("handle_open", 0.0) + (
                        (time.monotonic() - open_started) * 1000.0)
            else:
                state["last_used"] = time.monotonic()
            fh = state["fh"]
            lock = state["lock"]
        if timings is not None:
            timings["handle_cache"] = timings.get("handle_cache", 0.0) + (
                (time.monotonic() - started) * 1000.0)
        lock_started = time.monotonic()
        with lock:
            if timings is not None:
                timings["handle_lock_wait"] = timings.get("handle_lock_wait", 0.0) + (
                    (time.monotonic() - lock_started) * 1000.0)
            write_started = time.monotonic()
            payload = "".join(lines).encode("utf-8")
            fh.write(payload)
            state["buffered_bytes"] = int(state.get("buffered_bytes") or 0) + len(payload)
            if timings is not None:
                timings["write_call"] = timings.get("write_call", 0.0) + (
                    (time.monotonic() - write_started) * 1000.0)
            if int(state.get("buffered_bytes") or 0) >= max(1, _APPEND_BUFFER_BYTES):
                flush_started = time.monotonic()
                fh.flush()
                state["buffered_bytes"] = 0
                if timings is not None:
                    timings["buffer_flush"] = timings.get("buffer_flush", 0.0) + (
                        (time.monotonic() - flush_started) * 1000.0)

    @staticmethod
    def _trim_append_handles_locked() -> None:
        """Close least-recently-used append handles while lock is held."""
        limit = max(1, _APPEND_HANDLE_MAX)
        overflow = len(_APPEND_HANDLES) - limit
        if overflow <= 0:
            return
        victims = sorted(
            _APPEND_HANDLES.items(),
            key=lambda item: float(item[1].get("last_used") or 0.0),
        )[:overflow]
        for key, state in victims:
            lock = state.get("lock")
            if lock is not None and not lock.acquire(blocking=False):
                continue
            _APPEND_HANDLES.pop(key, None)
            try:
                state["fh"].flush()
                state["fh"].close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            finally:
                if lock is not None:
                    lock.release()

    @staticmethod
    def _close_append_handles(root: Path) -> None:
        root_s = str(root)
        prefix = root_s + os.sep
        with _APPEND_HANDLES_LOCK:
            keys = [
                key for key in _APPEND_HANDLES
                if key == root_s or key.startswith(prefix)
            ]
            states = [_APPEND_HANDLES.pop(key) for key in keys]
        for state in states:
            try:
                state["fh"].flush()
                state["fh"].close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def close_append_handles(root: Path) -> None:
        SegmentedJsonl._close_append_handles(root)

    @staticmethod
    def invalidate_index_cache(root: Path) -> None:
        root_s = str(root)
        prefix = root_s + os.sep
        with _INDEX_CACHE_LOCK:
            keys = [
                key for key in _INDEX_CACHE
                if key == root_s or key.startswith(prefix)
            ]
            for key in keys:
                _INDEX_CACHE.pop(key, None)

    @staticmethod
    def close_all_append_handles() -> None:
        with _APPEND_HANDLES_LOCK:
            states = list(_APPEND_HANDLES.values())
            _APPEND_HANDLES.clear()
        for state in states:
            try:
                state["fh"].flush()
                state["fh"].close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def flush_append_handles(root: Path) -> None:
        root_s = str(root)
        prefix = root_s + os.sep
        with _APPEND_HANDLES_LOCK:
            states = [
                state for key, state in _APPEND_HANDLES.items()
                if key == root_s or key.startswith(prefix)
            ]
        for state in states:
            lock = state.get("lock")
            fh = state.get("fh")
            if fh is None or getattr(fh, "closed", True):
                continue
            try:
                with lock:
                    fh.flush()
                    state["buffered_bytes"] = 0
                    state["last_used"] = time.monotonic()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        SegmentedJsonl.flush_dirty_indexes(root)

    @staticmethod
    def flush_dirty_indexes(root: Path, force: bool = False) -> None:
        root_s = str(root)
        prefix = root_s + os.sep
        now = time.monotonic()
        pending: List[Dict[str, Any]] = []
        with _INDEX_CACHE_LOCK:
            for key, state in _INDEX_CACHE.items():
                if key != root_s and not key.startswith(prefix):
                    continue
                if not state.get("dirty"):
                    continue
                index = state.get("index")
                if not isinstance(index, dict):
                    continue
                total = int(index.get("total_rows") or 0)
                last_flush = float(state.get("last_flush") or 0.0)
                last_rows = int(state.get("last_rows") or 0)
                if (not force
                        and total - last_rows < max(1, _INDEX_FLUSH_ROWS)
                        and now - last_flush < max(0.0, _INDEX_FLUSH_SECONDS)):
                    continue
                state["last_flush"] = now
                state["last_rows"] = total
                state["dirty"] = False
                pending.append({"key": key, "index": index})
        for item in pending:
            SegmentedJsonl(Path(item["key"] + ".jsonl"))._write_index_hot(item["index"])

    @staticmethod
    def flush_all_append_handles() -> None:
        with _APPEND_HANDLES_LOCK:
            states = list(_APPEND_HANDLES.values())
        for state in states:
            lock = state.get("lock")
            fh = state.get("fh")
            if fh is None or getattr(fh, "closed", True):
                continue
            try:
                with lock:
                    fh.flush()
                    state["buffered_bytes"] = 0
                    state["last_used"] = time.monotonic()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _remember_index(self, index: Dict[str, Any], flushed: bool = False) -> None:
        now = time.monotonic()
        total = int(index.get("total_rows") or 0)
        with _INDEX_CACHE_LOCK:
            state = _INDEX_CACHE.setdefault(self._cache_key(), {})
            state["index"] = index
            state["last_used"] = now
            state.setdefault("last_flush", 0.0)
            state.setdefault("last_rows", 0)
            if flushed:
                state["last_flush"] = now
                state["last_rows"] = total
            self._trim_index_cache_locked()

    @staticmethod
    def _trim_index_cache_locked() -> None:
        limit = max(1, _INDEX_CACHE_MAX)
        overflow = len(_INDEX_CACHE) - limit
        if overflow <= 0:
            return
        victims = sorted(
            _INDEX_CACHE.items(),
            key=lambda item: float(item[1].get("last_used") or 0.0),
        )[:overflow]
        for key, _state in victims:
            _INDEX_CACHE.pop(key, None)

    def _maybe_write_index_hot(self, index: Dict[str, Any], force: bool = False) -> None:
        """Persist hot index metadata when it materially helps readers.

        Segment rows are the durable source of truth; index.json is a cache.
        When Python runs on Windows against a WSL UNC path, rewriting this
        cache file can be hundreds of milliseconds slower than appending the
        actual JSONL row.
        Do not let the wall-clock flush interval run inside append_message's
        conversation lock. The timed flush is handled by flush_dirty_indexes(),
        outside the append hot path; appends only write the index when creating
        a new stream/segment or after enough rows have accumulated.
        """
        total = int(index.get("total_rows") or 0)
        now = time.monotonic()
        should_write = force
        defer_write = self._defer_hot_index_writes()
        with _INDEX_CACHE_LOCK:
            state = _INDEX_CACHE.setdefault(self._cache_key(), {"index": index})
            state["index"] = index
            last_rows = int(state.get("last_rows") or 0)
            rows_due = total - last_rows >= max(1, _INDEX_FLUSH_ROWS)
            should_write = should_write or rows_due
            if defer_write:
                state["dirty"] = True
                state["last_used"] = now
                should_write = False
            elif should_write:
                state["last_flush"] = now
                state["last_rows"] = total
                state["dirty"] = False
            else:
                state["dirty"] = True
                state["last_used"] = now
        if not should_write:
            return
        self._write_index_hot(index)

