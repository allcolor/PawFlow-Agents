"""Segmented JSONL storage helpers.

A logical JSONL file can be stored either as a legacy flat file
(`transcript.jsonl`) or as a directory of bounded segments (`transcript/`).
The runtime reads both formats, while new/migrated conversations write the
segmented format.
"""

from __future__ import annotations
import logging

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

DEFAULT_MAX_ROWS = int(os.getenv("PAWFLOW_JSONL_SEGMENT_ROWS", "5000") or "5000")
DEFAULT_MAX_BYTES = int(os.getenv("PAWFLOW_JSONL_SEGMENT_BYTES", str(8 * 1024 * 1024)) or str(8 * 1024 * 1024))
_INDEX_NAME = "index.json"
_APPEND_DIAG_MS = float(os.getenv("PAWFLOW_JSONL_APPEND_DIAG_MS", "100") or "100")
_INDEX_CACHE_MAX = int(os.getenv("PAWFLOW_JSONL_INDEX_CACHE_MAX", "256") or "256")
_APPEND_HANDLE_MAX = int(os.getenv("PAWFLOW_JSONL_APPEND_HANDLE_MAX", "128") or "128")
_INDEX_CACHE: Dict[str, Dict[str, Any]] = {}
_INDEX_CACHE_LOCK = threading.RLock()
_APPEND_HANDLES: Dict[str, Dict[str, Any]] = {}
_APPEND_HANDLES_LOCK = threading.RLock()


class SegmentedJsonl:
    """Read/write one logical JSONL stream stored in bounded segment files."""

    def __init__(self, flat_path: Path, max_rows: int = DEFAULT_MAX_ROWS,
                 max_bytes: int = DEFAULT_MAX_BYTES):
        self.flat_path = Path(flat_path)
        self.segment_dir = self.flat_path.with_suffix("")
        self.index_path = self.segment_dir / _INDEX_NAME
        self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))
        self.max_bytes = max(1, int(max_bytes or DEFAULT_MAX_BYTES))

    def is_segmented(self) -> bool:
        return self.index_path.exists() or self.segment_dir.is_dir()

    def exists(self) -> bool:
        return self.is_segmented() or self.flat_path.exists()

    def iter_paths(self) -> List[Path]:
        if self.is_segmented():
            return self._segment_paths()
        return [self.flat_path] if self.flat_path.exists() else []

    def iter_rows(self) -> Iterator[Dict[str, Any]]:
        for path in self.iter_paths():
            yield from self._iter_file(path)

    def iter_rows_reverse(self) -> Iterator[Dict[str, Any]]:
        paths = self._segment_paths() if self.is_segmented() else ([self.flat_path] if self.flat_path.exists() else [])
        for path in reversed(paths):
            yield from self._iter_file_reverse(path)

    def append_dicts(self, rows: Iterable[Dict[str, Any]]) -> None:
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
        index_missing = not self.index_path.exists()
        if cached is None and self.flat_path.exists() and not self.is_segmented():
            self.flat_path.parent.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            self._append_lines_to_path(self.flat_path, lines)
            mark("flat_write", t0)
            self._log_append_diag(started, len(lines), total_bytes, timings)
            return

        if cached is None:
            t0 = time.monotonic()
            self.segment_dir.mkdir(parents=True, exist_ok=True)
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
        for path, path_lines in pending_by_path.items():
            t0 = time.monotonic()
            self._append_lines_to_path(path, path_lines)
            mark("write", t0)
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
            "write=%.1f flat_write=%.1f remember_index=%.1f index_write=%.1f",
            str(self.flat_path), rows, total_bytes, total_ms,
            timings.get("cache", 0.0),
            timings.get("load_index", 0.0),
            timings.get("current_segment", 0.0),
            timings.get("write", 0.0),
            timings.get("flat_write", 0.0),
            timings.get("remember_index", 0.0),
            timings.get("index_write", 0.0),
        )

    def replace_dicts(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.replace_lines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

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

    def total_rows(self) -> int:
        if self.is_segmented():
            return int(self._load_index().get("total_rows") or 0)
        return sum(1 for _ in self._iter_file(self.flat_path)) if self.flat_path.exists() else 0

    def latest_mtime(self) -> float:
        mtimes = [p.stat().st_mtime for p in self.iter_paths() if p.exists()]
        if self.index_path.exists():
            mtimes.append(self.index_path.stat().st_mtime)
        return max(mtimes) if mtimes else 0.0

    def _load_index(self) -> Dict[str, Any]:
        cache_key = self._cache_key()
        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(cache_key)
            if cached is not None:
                return cached["index"]
        if self.index_path.exists():
            try:
                index_mtime = self.index_path.stat().st_mtime
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("version", 1)
                    data.setdefault("max_rows", self.max_rows)
                    data.setdefault("max_bytes", self.max_bytes)
                    data.setdefault("segments", [])
                    data.setdefault("total_rows", sum(int(s.get("rows") or 0) for s in data.get("segments") or []))
                    segment_paths = sorted(
                        p for p in self.segment_dir.glob("*.jsonl")
                        if p.exists())
                    self._ensure_segment_bytes(data, segment_paths)
                    latest_segment_mtime = max(
                        (p.stat().st_mtime for p in segment_paths),
                        default=0.0)
                    if latest_segment_mtime <= index_mtime:
                        self._remember_index(data, flushed=True)
                        return data
                    data = self._refresh_stale_index(data, index_mtime, segment_paths)
                    self._remember_index(data, flushed=True)
                    if segment_paths:
                        self._write_index_hot(data)
                    return data
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
        self._remember_index(data)
        if segments:
            self._write_index_hot(data)
        return data

    def _refresh_stale_index(self, index: Dict[str, Any], index_mtime: float,
                             segment_paths: List[Path]) -> Dict[str, Any]:
        """Repair a dirty hot index without scanning every old segment.

        Hot appends intentionally keep index.json slightly stale to avoid a
        metadata write per message. After restart, only the open tail segment
        and any files newer than the index can have changed; older sealed
        segments are append-immutable and their stored row counts remain valid.
        """
        old_rows = {
            str(item.get("file") or ""): int(item.get("rows") or 0)
            for item in (index.get("segments") or [])
            if item.get("file")
        }
        last_name = segment_paths[-1].name if segment_paths else ""
        refreshed = []
        for path in segment_paths:
            name = path.name
            rows = old_rows.get(name)
            if rows is None or name == last_name:
                rows = self._count_rows_fast(path)
            refreshed.append({
                "file": name,
                "rows": int(rows or 0),
                "bytes": path.stat().st_size,
            })
        return {
            "version": int(index.get("version") or 1),
            "max_rows": int(index.get("max_rows") or self.max_rows),
            "max_bytes": int(index.get("max_bytes") or self.max_bytes),
            "segments": refreshed,
            "total_rows": sum(int(s.get("rows") or 0) for s in refreshed),
        }

    @staticmethod
    def _ensure_segment_bytes(index: Dict[str, Any],
                              segment_paths: List[Path]) -> None:
        by_name = {p.name: p for p in segment_paths}
        for item in index.get("segments") or []:
            if item.get("bytes") is not None:
                continue
            path = by_name.get(str(item.get("file") or ""))
            item["bytes"] = path.stat().st_size if path and path.exists() else 0

    @staticmethod
    def _count_rows_fast(path: Path) -> int:
        rows = 0
        last = b""
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    rows += chunk.count(b"\n")
                    last = chunk[-1:]
        except FileNotFoundError:
            return 0
        if last and last != b"\n":
            rows += 1
        return rows

    def _cache_key(self) -> str:
        return str(self.segment_dir)

    @staticmethod
    def _append_lines_to_path(path: Path, lines: List[str]) -> None:
        """Append with a hot file handle.

        On Windows/WSL mounts, repeatedly opening and closing the same JSONL
        segment dominates the cost of tiny append-only writes. Keep one
        unbuffered append handle per segment path so each write is immediately
        visible without forcing a Python text-buffer flush on every message.
        """
        key = str(path)
        with _APPEND_HANDLES_LOCK:
            state = _APPEND_HANDLES.get(key)
            if state is None or getattr(state.get("fh"), "closed", True):
                path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "fh": open(path, "ab", buffering=0),
                    "lock": threading.RLock(),
                    "last_used": time.monotonic(),
                }
                _APPEND_HANDLES[key] = state
                SegmentedJsonl._trim_append_handles_locked()
            else:
                state["last_used"] = time.monotonic()
            fh = state["fh"]
            lock = state["lock"]
        with lock:
            fh.write("".join(lines).encode("utf-8"))

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
        total = int(index.get("total_rows") or 0)
        with _INDEX_CACHE_LOCK:
            state = _INDEX_CACHE.setdefault(self._cache_key(), {"index": index})
            if not force:
                state["dirty"] = True
                return
            state["last_flush"] = time.monotonic()
            state["last_rows"] = total
            state["dirty"] = False
        self._write_index_hot(index)

    def _write_index(self, index: Dict[str, Any]) -> None:
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_name(
            f"{self.index_path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self._replace_path(tmp, self.index_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _write_index_hot(self, index: Dict[str, Any]) -> None:
        """Write append-derived index metadata without a rename.

        The JSONL segment row is the durable source of truth. The index only
        records segment row counts for faster reads; `_load_index()` already
        rebuilds it from segment files if it is missing or malformed. Avoiding
        tmp+replace here removes one Windows/WSL rename from every append.
        `_maybe_write_index_hot()` only calls this when a new segment is created
        or an index is missing. Ordinary appends keep the in-memory index current
        and let `_load_index()` rebuild from segments if a future process sees a
        stale disk index.
        Full rewrites still use `_write_index()` through replace_lines().
        """
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.index_path, "w", encoding="utf-8") as fh:
                json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            logging.getLogger(__name__).warning(
                "SegmentedJsonl hot index write failed for %s",
                self.index_path, exc_info=True)

    @staticmethod
    def _replace_path(src: Path, dst: Path) -> None:
        last_err = None
        for attempt in range(6):
            try:
                src.replace(dst)
                return
            except PermissionError as err:
                last_err = err
                if os.name != "nt" or attempt == 5:
                    break
                time.sleep(0.025 * (attempt + 1))
        if last_err:
            raise last_err

    def _segment_bytes(self, item: Dict[str, Any], root: Path) -> int:
        value = item.get("bytes")
        if value is not None:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
        path = root / str(item.get("file") or "")
        size = path.stat().st_size if path.exists() else 0
        item["bytes"] = size
        return size

    def _current_segment(self, index: Dict[str, Any],
                         root: Optional[Path] = None,
                         next_bytes: int = 0) -> Dict[str, Any]:
        segments = index.setdefault("segments", [])
        if root is None:
            root = self.segment_dir
        if segments:
            current = segments[-1]
            rows = int(current.get("rows") or 0)
            size = self._segment_bytes(current, root)
            fits_rows = rows < self.max_rows
            fits_bytes = (
                size == 0 or
                size + max(0, int(next_bytes or 0)) <= self.max_bytes
            )
            if fits_rows and fits_bytes:
                return current
            if current.get("file"):
                self._close_append_handles(root / str(current.get("file")))
        name = f"{len(segments):06d}.jsonl"
        item = {"file": name, "rows": 0, "bytes": 0}
        segments.append(item)
        root.mkdir(parents=True, exist_ok=True)
        (root / name).touch(exist_ok=True)
        return item

    def _segment_paths(self) -> List[Path]:
        index = self._load_index()
        paths = [self.segment_dir / str(s.get("file") or "") for s in index.get("segments") or []]
        existing = [p for p in paths if p.is_file()]
        if existing:
            return existing
        if self.segment_dir.is_dir():
            return sorted(self.segment_dir.glob("*.jsonl"))
        return []

    @staticmethod
    def _iter_file(path: Path) -> Iterator[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            return

    @staticmethod
    def _iter_file_reverse(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[Dict[str, Any]]:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                pos = fh.tell()
                buf = b""
                while pos > 0:
                    n = min(chunk_size, pos)
                    pos -= n
                    fh.seek(pos)
                    buf = fh.read(n) + buf
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
