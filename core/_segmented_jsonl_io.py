"""Low-level segment-file and index.json I/O for SegmentedJsonl.

Split out of segmented_jsonl.py as a leaf mixin so the file stays <= 800 lines.
These methods perform per-instance disk reads/writes only; the process-global
index cache and append-handle registry (and every method that touches them)
stay in segmented_jsonl.py. Methods here call back into those via self.*
(resolved through the MRO on SegmentedJsonl).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


def _is_windows_wsl_unc_path(path: Path) -> bool:
    if os.name != "nt":
        return False
    value = str(path).replace("/", "\\")
    if value.startswith("\\\\wsl$\\"):
        return True
    if not Path(value).is_absolute():
        try:
            cwd_value = str(Path.cwd() / path).replace("/", "\\")
            return cwd_value.startswith("\\\\wsl$\\")
        except Exception:
            return False
    return False


class _SegmentedJsonlIOMixin:
    """Per-instance segment-file + index.json disk I/O for SegmentedJsonl."""

    def _defer_hot_index_writes(self) -> bool:
        return _is_windows_wsl_unc_path(self.index_path)

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
        rebuilds it from segment files if it is missing or malformed. This hot
        path intentionally avoids tmp+replace/fsync, but keeps the disk index
        current enough that restart does not make the first append count the
        tail segment under the conversation lock.
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

    def _replace_rows_in_path(self, path: Path,
                              rows: List[Dict[str, Any]]) -> None:
        self._close_append_handles(path)
        lines = [json.dumps(row, ensure_ascii=False) + "\n" for row in rows]
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            self._replace_path(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if self.is_segmented():
            index = self._load_index()
            for item in index.get("segments") or []:
                if str(item.get("file") or "") == path.name:
                    item["rows"] = len(rows)
                    item["bytes"] = path.stat().st_size if path.exists() else 0
                    break
            index["total_rows"] = sum(
                int(item.get("rows") or 0)
                for item in index.get("segments") or [])
            self._remember_index(index, flushed=True)
            self._write_index(index)

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
            size = int(current.get("bytes") or 0)
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
