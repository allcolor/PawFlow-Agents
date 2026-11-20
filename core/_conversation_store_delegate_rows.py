"""Delegate rows of a transcript, per caller, memoised per segment file.

``delegate_status`` and ``delegate_result`` rebuild one caller's delegate
state from the append-only transcript (``load_delegate_state``). On a long
multi-agent conversation that transcript holds millions of delegate rows: the
8.5 GB, 2,209-segment transcript of 2026-11-20 held 2,098,734, and the first
call after a server restart decoded all of them and ran past the MCP client's
read timeout (150 s).

Three things keep a call bounded:

* A line holding none of the values rows are selected on is never decoded.
  Rows are selected on metadata that stays in clear text.
* A caller's state depends only on its own rows: requests it sent, requests
  and replies addressed to it, traces it is the parent of, and trace
  updates. Each segment's rows are partitioned by caller, casefolded, and a
  call reads its caller's rows only (14,945 of the 2,098,734 for one agent).
* A run of the target's non-terminal reply rows (its tool calls and
  intermediate messages, 1.97 M of those rows) for one task changes the
  caller's state exactly as its last row does: the first row of a run closes
  the turn the target answered before and marks the task running, the
  following ones only move its last time and row forward. A run is kept as
  its last row, provided times do not go backwards.

Each segment's partition is memoised against the file's (inode, size, mtime)
signature, so only the segment being appended to is read again. A sealed
segment never changes, so its partition is also persisted in a sidecar,
``delegate_rows/{segment}.jsonl`` next to the segments, keyed on the
segment's device, inode and size like the msg_id sidecars: one line per
caller, decoded only when that caller asks. After a restart only the tail
segment, and segments sealed since the last call, are scanned. The sidecar
holds exactly what the segment holds in clear text (routing metadata and
offsets of shared rows) plus trace rows as they are stored on disk, still
encoded: decoding and secret stripping happen on load.

Shared delegate rows are kept compact: routing metadata plus the row's byte
offset, and ``content`` re-reads one row only for the results a call
actually returns. A reply row that ends the target's turn also carries
``terminal``: its ``turn_final`` is set, or it has no such marker, like rows
that predate it and the caller's copy of a reply delivered while it was busy.
"""

from __future__ import annotations

import heapq
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)

_Signature = Tuple[int, int, int]
_Entry = Tuple[int, Dict[str, Any]]
_SOURCE_KEYS = ("type", "task_id", "from", "to", "target_agent", "kind")
# A delegate row holds one of these values in clear text (see is_delegate_row).
_NEEDLES = (b"agent_delegate", b"trace_update", b"sub_agent_trace")
# Trace updates apply to whichever caller owns the trace, which a segment
# cannot know: every caller reads them.
_ALL = "*"
_SIDECAR_DIR = "delegate_rows"
_SIDECAR_VERSION = 2


def is_delegate_row(row: Dict[str, Any]) -> bool:
    """True for the rows that delegate state is rebuilt from."""
    if _is_trace(row):
        return True
    source = row.get("source")
    return isinstance(source, dict) and source.get("type") == "agent_delegate"


def _is_trace(row: Dict[str, Any]) -> bool:
    return row.get("t") == "trace_update" or row.get("role") == "sub_agent_trace"


def _iter_offsets(path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Yield (offset, row) for the lines that may be delegate rows."""
    try:
        with open(path, "rb") as fh:
            offset = 0
            for raw in fh:
                start, offset = offset, offset + len(raw)
                if not any(needle in raw for needle in _NEEDLES):
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


def _time(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("timestamp") or row.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stored(raw: Dict[str, Any]) -> Dict[str, Any]:
    """What a partition keeps of a delegate row: what the replay needs."""
    if _is_trace(raw):
        return raw
    source = raw.get("source")
    out = {
        "role": raw.get("role"),
        "timestamp": raw.get("timestamp"),
        "ts": raw.get("ts"),
        "source": {k: source[k] for k in _SOURCE_KEYS if k in source},
    }
    if "turn_final" in raw:
        out["turn_final"] = raw["turn_final"]
    return out


def _callers(stored: Dict[str, Any]) -> List[str]:
    """The callers, casefolded, whose delegate state this row can change.

    Mirrors ``load_delegate_state``: a request counts for its sender and, as
    contact, for its target; a reply for its target; a trace for its parent.
    """
    if stored.get("t") == "trace_update":
        return [_ALL]
    source = stored.get("source")
    if not isinstance(source, dict):
        return []
    if stored.get("role") == "sub_agent_trace":
        parent = str(source.get("parent_agent") or "").casefold()
        return [parent] if parent else []
    if not source.get("task_id"):
        return []
    sender = str(source.get("from") or "").casefold()
    target = str(source.get("to") or "").casefold()
    if source.get("kind") == "reply":
        return [target] if target else []
    callers = [sender] if sender else []
    if target and target != sender:
        callers.append(target)
    return callers


def _run_key(stored: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(task_id, sender) of a non-terminal reply row a run may collapse into."""
    source = stored.get("source")
    if (_is_trace(stored) or not isinstance(source, dict)
            or source.get("kind") != "reply" or stored.get("role") == "user"
            or stored.get("turn_final", True)):
        return None
    return (str(source.get("task_id") or ""),
            str(source.get("from") or "").casefold())


def _partition(pairs: Iterable[_Entry]) -> Dict[str, List[_Entry]]:
    """Stored delegate rows per caller, runs of non-terminal replies collapsed."""
    lists: Dict[str, List[_Entry]] = {}
    last: Dict[str, Tuple[Optional[Tuple[str, str]], float]] = {}
    for offset, raw in pairs:
        if not is_delegate_row(raw):
            continue
        stored = _stored(raw)
        run, at = _run_key(stored), _time(stored)
        for caller in _callers(stored):
            entries = lists.setdefault(caller, [])
            previous = last.get(caller)
            if (run is not None and previous is not None
                    and previous[0] == run and at >= previous[1]):
                entries[-1] = (offset, stored)
            else:
                entries.append((offset, stored))
            last[caller] = (run, at)
    return lists


def _row(log: SegmentedJsonl, path: str, offset: int,
         stored: Dict[str, Any]) -> Dict[str, Any]:
    """The replay row of one stored delegate row."""
    if _is_trace(stored):
        return _decoded(log, stored)
    source = stored["source"]
    row = {
        "role": stored.get("role"),
        "timestamp": stored.get("timestamp"),
        "ts": stored.get("ts"),
        "source": dict(source),
        "_ref": (path, offset, str(source.get("task_id") or "")),
    }
    if source.get("kind") == "reply" and stored.get("turn_final", True):
        row["terminal"] = True
    return row


def _identity(path: Path) -> Optional[List[int]]:
    """Device, inode and size: a sealed segment keeps all three."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_size]


def _header(identity: List[int]) -> Dict[str, Any]:
    return {"v": _SIDECAR_VERSION, "segment": identity}


def _sidecar_path(log: SegmentedJsonl, path: Path) -> Path:
    return Path(log.segment_dir) / _SIDECAR_DIR / f"{path.name}.jsonl"


def _read_sidecar(log: SegmentedJsonl,
                  path: Path) -> Optional[Dict[str, Union[bytes, List]]]:
    """The segment's partition, each caller's entries still encoded."""
    identity = _identity(path)
    if identity is None:
        return None
    try:
        with open(_sidecar_path(log, path), "rb") as fh:
            if json.loads(fh.readline() or b"null") != _header(identity):
                return None
            out: Dict[str, Union[bytes, List]] = {}
            for line in fh:
                name, tab, body = line.partition(b"\t")
                if tab:
                    out[json.loads(name)] = body
            return out
    except (OSError, ValueError, TypeError):
        return None


def _write_sidecar(log: SegmentedJsonl, path: Path, identity: List[int],
                   lists: Dict[str, List[_Entry]]) -> None:
    sidecar = _sidecar_path(log, path)
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp = sidecar.with_name(f"{sidecar.name}.{uuid.uuid4().hex}.tmp")
        lines = [json.dumps(_header(identity))]
        lines.extend(
            json.dumps(caller, ensure_ascii=False) + "\t"
            + json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
            for caller, entries in lists.items())
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, sidecar)
    except OSError:
        logger.debug("delegate rows sidecar write failed for %s", path,
                     exc_info=True)


class _Segment:
    """One segment's partition; each caller's replay rows built on demand."""

    __slots__ = ("signature", "settled", "entries", "rows")

    def __init__(self, signature: _Signature, settled: bool,
                 entries: Dict[str, Union[bytes, List]]) -> None:
        self.signature = signature
        self.settled = settled
        self.entries = entries
        self.rows: Dict[str, List[Dict[str, Any]]] = {}

    def _entries(self, caller: str) -> List:
        entries = self.entries.get(caller) or []
        if isinstance(entries, bytes):
            entries = json.loads(entries)
            self.entries[caller] = entries
        return entries

    def rows_for(self, log: SegmentedJsonl, path: str,
                 caller: str) -> List[Dict[str, Any]]:
        rows = self.rows.get(caller)
        if rows is None:
            merged = heapq.merge(self._entries(caller), self._entries(_ALL),
                                 key=lambda entry: entry[0])
            rows = [_row(log, path, offset, stored)
                    for offset, stored in merged]
            self.rows[caller] = rows
        return rows


class DelegateRowMemo:
    """Per-conversation cache of delegate rows keyed by segment signature."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segments: Dict[str, Dict[str, _Segment]] = {}

    def rows(self, cid: str, log: SegmentedJsonl,
             caller: str) -> List[Dict[str, Any]]:
        """Return the delegate rows that can change ``caller``'s state.

        Rows are in transcript order. Shared delegate rows omit ``content``
        and carry ``_ref`` instead.
        """
        caller = str(caller or "").casefold()
        with self._lock:
            known = dict(self._segments.get(cid) or {})
        current: Dict[str, _Segment] = {}
        out: List[Dict[str, Any]] = []
        paths = log.iter_paths()
        for index, path in enumerate(paths):
            # Every segment but the last is sealed and never changes again.
            sealed = index < len(paths) - 1
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                continue
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            key = str(path)
            segment = known.get(key)
            # A segment cached as the tail is read once more when it seals,
            # to give it its sidecar.
            if (segment is None or segment.signature != signature
                    or (sealed and not segment.settled)):
                segment = self._load_segment(log, Path(path), sealed, signature)
            current[key] = segment
            out.extend(segment.rows_for(log, key, caller))
        with self._lock:
            self._segments[cid] = current
        return out

    @staticmethod
    def _load_segment(log: SegmentedJsonl, path: Path, sealed: bool,
                      signature: _Signature) -> _Segment:
        """A segment's partition, from its sidecar when it is sealed and has one.

        A sealed segment is settled once its sidecar was read or written (a
        failed write is not retried).
        """
        entries = _read_sidecar(log, path) if sealed else None
        if entries is None:
            identity = _identity(path) if sealed else None
            lists = _partition(_iter_offsets(str(path)))
            # A segment that changed while it was read keeps the answer for
            # this call, but its sidecar would already be stale.
            if identity is not None and _identity(path) == identity:
                _write_sidecar(log, path, identity, lists)
            entries = dict(lists)
        return _Segment(signature, sealed, entries)

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
