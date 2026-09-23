"""Delegate rows of a transcript, memoised per segment file.

``delegate_status`` and ``delegate_result`` rebuild a caller's delegate state
from the append-only transcript. On a long multi-agent conversation that
transcript holds hundreds of thousands of rows across hundreds of segments,
and decoding and sanitising every row took tens of seconds per call. Only the
segment being appended to changes between calls, so each segment's delegate
rows are kept in memory against the file's (inode, size, mtime) signature and
only a changed or new segment is read again. Rows are selected on metadata
that stays in clear text before any content is decoded.

Shared delegate messages are by far the most numerous delegate rows and carry
the full request or reply text. They are kept compact: routing metadata plus
the row's byte offset, and ``content`` re-reads one row only for the results a
call actually returns.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterator, List, Tuple

from core.segmented_jsonl import SegmentedJsonl

_Signature = Tuple[int, int, int]
_SOURCE_KEYS = ("type", "task_id", "from", "to", "target_agent", "kind")


def is_delegate_row(row: Dict[str, Any]) -> bool:
    """True for the rows that delegate state is rebuilt from."""
    if row.get("t") == "trace_update" or row.get("role") == "sub_agent_trace":
        return True
    source = row.get("source")
    return isinstance(source, dict) and source.get("type") == "agent_delegate"


def _iter_offsets(path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    try:
        with open(path, "rb") as fh:
            offset = 0
            for raw in fh:
                start, offset = offset, offset + len(raw)
                if not raw.strip():
                    continue
                try:
                    yield start, json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
    except FileNotFoundError:
        return


def _decoded(log: SegmentedJsonl, raw: Dict[str, Any]) -> Dict[str, Any]:
    from core.secret_sanitization import strip_secret_runtime_values

    row = log.codec.decode(raw) if log.codec is not None else raw
    return strip_secret_runtime_values(row)


class DelegateRowMemo:
    """Per-conversation cache of delegate rows keyed by segment signature."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segments: Dict[str, Dict[str, Tuple[_Signature, List[Dict]]]] = {}

    def rows(self, cid: str, log: SegmentedJsonl) -> List[Dict[str, Any]]:
        """Return delegate rows in transcript order.

        Shared delegate rows omit ``content`` and carry ``_ref`` instead.
        """
        with self._lock:
            known = dict(self._segments.get(cid) or {})
        current: Dict[str, Tuple[_Signature, List[Dict]]] = {}
        out: List[Dict[str, Any]] = []
        for path in log.iter_paths():
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                continue
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            key = str(path)
            cached = known.get(key)
            if cached is not None and cached[0] == signature:
                rows = cached[1]
            else:
                rows = self._read_segment(log, key)
            current[key] = (signature, rows)
            out.extend(rows)
        with self._lock:
            self._segments[cid] = current
        return out

    @staticmethod
    def _read_segment(log: SegmentedJsonl, path: str) -> List[Dict[str, Any]]:
        rows = []
        for offset, raw in _iter_offsets(path):
            if not is_delegate_row(raw):
                continue
            source = raw.get("source")
            if (raw.get("t") == "trace_update"
                    or raw.get("role") == "sub_agent_trace"):
                rows.append(_decoded(log, raw))
                continue
            rows.append({
                "role": raw.get("role"),
                "timestamp": raw.get("timestamp"),
                "ts": raw.get("ts"),
                "source": {k: source[k] for k in _SOURCE_KEYS if k in source},
                "_ref": (path, offset, str(source.get("task_id") or "")),
            })
        return rows

    @staticmethod
    def content(log: SegmentedJsonl, ref: Tuple[str, int, str]) -> Any:
        """Read the content of one compact row, or "" if it moved."""
        path, offset, task_id = ref
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                raw = json.loads(fh.readline())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return ""
        source = raw.get("source") if isinstance(raw, dict) else None
        if not isinstance(source, dict) or str(source.get("task_id") or "") != task_id:
            return ""
        return _decoded(log, raw).get("content")


DELEGATE_ROWS = DelegateRowMemo()
