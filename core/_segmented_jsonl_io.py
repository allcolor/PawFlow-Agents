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


_SEGMENT_INDEX_VERSION = 2
_SECRET_SCRUB_VERSION = 1
_ROLE_KEY_BYTES = b'"role":'
_ROLE_STRING_PREFIX = b'{"role": "'


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

    @staticmethod
    def _scrub_segment_signature(path: Path) -> list:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns]

    def scrub_secret_runtime_values(self) -> tuple[int, int]:
        """Remove legacy runtime secrets, rechecking only changed segments.

        Callers serialize this migration with conversation writes. Completion
        is a versioned, atomic cache of file identities, not a replacement for
        read/write sanitization. Appends invalidate only the changed tail;
        restored/replaced files and encryption rewrites invalidate themselves.
        """
        from core.secret_sanitization import strip_secret_runtime_values_counted

        self._flush_own_append_handles()
        paths = self._segment_paths()
        if not paths:
            return 0, 0
        marker_path = self.segment_dir / "secret_scrub.json"
        try:
            previous = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        codec = self.codec
        marker = {"version": _SECRET_SCRUB_VERSION,
                  "decoded": codec is not None, "segments": {}}
        completed = {}
        if (isinstance(previous, dict)
                and previous.get("version") == marker["version"]
                and previous.get("decoded") == marker["decoded"]
                and isinstance(previous.get("segments"), dict)):
            completed = previous["segments"]
        changed_rows = removed_keys = 0
        for path in paths:
            signature = self._scrub_segment_signature(path)
            if completed.get(path.name) == signature:
                marker["segments"][path.name] = signature
                continue
            stored_rows = []
            path_changed = False
            for raw in self._iter_file(path):
                decoded = codec.decode(raw) if codec is not None else raw
                clean, removed = strip_secret_runtime_values_counted(decoded)
                if removed:
                    changed_rows += 1
                    removed_keys += removed
                    path_changed = True
                    raw = codec.encode(clean) if codec is not None else clean
                stored_rows.append(raw)
            if self._scrub_segment_signature(path) != signature:
                raise RuntimeError("Segment changed during secret cleanup")
            if path_changed:
                self._replace_rows_in_path(path, stored_rows)
                signature = self._scrub_segment_signature(path)
            marker["segments"][path.name] = signature
        if marker != previous:
            tmp = marker_path.with_name(
                f"{marker_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(json.dumps(marker, separators=(",", ":")),
                               encoding="utf-8")
                self._replace_path(tmp, marker_path)
            except OSError:
                # A failed cache write must not hide rows or mark the files on
                # disk as migrated. The next process simply checks them again.
                logging.getLogger(__name__).warning(
                    "Could not persist secret cleanup completion", exc_info=True)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).debug(
                        "Could not remove secret cleanup temporary marker", exc_info=True)
        return changed_rows, removed_keys

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
                    item["role_rows"] = sum(
                        1 for row in rows if row.get("role"))
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
        item = {"file": name, "rows": 0, "role_rows": 0, "bytes": 0}
        segments.append(item)
        if all("role_rows" in segment for segment in segments):
            index["version"] = _SEGMENT_INDEX_VERSION
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
    def _line_has_role(line: str) -> bool:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(row, dict) and bool(row.get("role"))

    @classmethod
    def _count_role_rows(cls, path: Path) -> int:
        """Count display rows without decoding ordinary message payloads.

        Canonical messages serialize ``role`` first. Only reordered or nested
        occurrences take the slower JSON path; trace updates have no top-level
        role and are therefore excluded exactly.
        """
        count = 0
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    stripped = raw.lstrip()
                    if (stripped.startswith(_ROLE_STRING_PREFIX)
                            and len(stripped) > len(_ROLE_STRING_PREFIX)
                            and stripped[len(_ROLE_STRING_PREFIX)] != ord('"')):
                        count += 1
                    elif _ROLE_KEY_BYTES in raw:
                        try:
                            row = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if isinstance(row, dict) and row.get("role"):
                            count += 1
        except FileNotFoundError:
            return 0
        return count

    def role_rows_by_path(self) -> Dict[Path, int]:
        """Return exact display-row counts, upgrading old indexes once."""
        self._flush_own_append_handles()
        index = self._load_index()
        changed = False
        counts: Dict[Path, int] = {}
        for item in index.get("segments") or []:
            path = self.segment_dir / str(item.get("file") or "")
            try:
                count = int(item["role_rows"])
            except (KeyError, TypeError, ValueError):
                count = self._count_role_rows(path)
                item["role_rows"] = count
                changed = True
            counts[path] = count
        if changed:
            index["version"] = _SEGMENT_INDEX_VERSION
            self._remember_index(index, flushed=True)
            self._write_index(index)
        return counts

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
