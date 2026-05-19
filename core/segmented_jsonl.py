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

DEFAULT_MAX_ROWS = int(os.getenv("PAWFLOW_JSONL_SEGMENT_ROWS", "25000") or "25000")
_INDEX_NAME = "index.json"


class SegmentedJsonl:
    """Read/write one logical JSONL stream stored in bounded segment files."""

    def __init__(self, flat_path: Path, max_rows: int = DEFAULT_MAX_ROWS):
        self.flat_path = Path(flat_path)
        self.segment_dir = self.flat_path.with_suffix("")
        self.index_path = self.segment_dir / _INDEX_NAME
        self.max_rows = max(1, int(max_rows or DEFAULT_MAX_ROWS))

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
        lines = [line if line.endswith("\n") else line + "\n" for line in lines]
        if not lines:
            return
        if self.flat_path.exists() and not self.is_segmented():
            self.flat_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.flat_path, "a", encoding="utf-8") as fh:
                fh.writelines(lines)
            return

        self.segment_dir.mkdir(parents=True, exist_ok=True)
        index = self._load_index()
        for line in lines:
            current = self._current_segment(index)
            path = self.segment_dir / current["file"]
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            current["rows"] = int(current.get("rows") or 0) + 1
            index["total_rows"] = int(index.get("total_rows") or 0) + 1
        self._write_index(index)

    def replace_dicts(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.replace_lines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    def delete(self) -> None:
        if self.flat_path.exists():
            self.flat_path.unlink()
        if self.segment_dir.exists():
            shutil.rmtree(self.segment_dir)

    def replace_lines(self, lines: Iterable[str]) -> None:
        self.flat_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.segment_dir.with_name(self.segment_dir.name + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        index = {"version": 1, "max_rows": self.max_rows, "segments": [], "total_rows": 0}
        for line in lines:
            line = line if line.endswith("\n") else line + "\n"
            current = self._current_segment(index, tmp_dir)
            with open(tmp_dir / current["file"], "a", encoding="utf-8") as fh:
                fh.write(line)
            current["rows"] = int(current.get("rows") or 0) + 1
            index["total_rows"] = int(index.get("total_rows") or 0) + 1
        (tmp_dir / _INDEX_NAME).write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.segment_dir.exists():
            shutil.rmtree(self.segment_dir)
        tmp_dir.replace(self.segment_dir)
        if self.flat_path.exists():
            self.flat_path.unlink()

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
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("version", 1)
                    data.setdefault("max_rows", self.max_rows)
                    data.setdefault("segments", [])
                    data.setdefault("total_rows", sum(int(s.get("rows") or 0) for s in data.get("segments") or []))
                    return data
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        segments = []
        if self.segment_dir.is_dir():
            for path in sorted(self.segment_dir.glob("*.jsonl")):
                segments.append({"file": path.name, "rows": sum(1 for _ in self._iter_file(path))})
        return {"version": 1, "max_rows": self.max_rows, "segments": segments, "total_rows": sum(s["rows"] for s in segments)}

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

    def _current_segment(self, index: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, Any]:
        segments = index.setdefault("segments", [])
        if segments and int(segments[-1].get("rows") or 0) < self.max_rows:
            return segments[-1]
        name = f"{len(segments):06d}.jsonl"
        item = {"file": name, "rows": 0}
        segments.append(item)
        if root is None:
            root = self.segment_dir
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
