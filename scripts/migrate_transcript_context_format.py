#!/usr/bin/env python3
"""Migrate all conversation streams to the provider-turn row format.

The target format is identical for transcript, shared context, and per-agent
contexts. Provider turns are stored as separate linked rows:

  assistant anchor:  role=assistant, msg_id=A, content="" or text
  thinking row:      role=thinking,  msg_id=T, parent_message_id=A
  tool call row:     role=tool_call, msg_id=C, parent_message_id=A,
                     tool_call_id=tc_123
  tool result row:   role=tool,      msg_id=R, parent_message_id=C,
                     tool_call_id=tc_123

Legacy rows with ``t=msg`` are unwrapped. Assistant ``thinking`` fields become
``role=thinking`` rows. Assistant ``tool_calls`` arrays become one
``role=tool_call`` row per call. Existing tool result rows keep their
``tool_call_id`` and are linked back to the matching tool_call row.

Run from the PawFlow root:

    python scripts/migrate_transcript_context_format.py --dry-run
    python scripts/migrate_transcript_context_format.py --apply

The default is dry-run. Apply mode writes through ``SegmentedJsonl`` so flat and
segmented streams are handled the same way. Changed streams are backed up as
flat JSONL under ``_transcript_context_migration_backup/`` inside the
conversation directory. Re-running after a successful migration is a no-op.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.segmented_jsonl import SegmentedJsonl  # noqa: E402


DEFAULT_CONVERSATIONS_DIR = ROOT / "data" / "runtime" / "conversations"
_BACKUP_DIR_NAME = "_transcript_context_migration_backup"
_SKIP_DIRS = {
    ".git", "attachments", "shared", "summaries", "transcript",
    "_jsonl_migration_backup", _BACKUP_DIR_NAME,
}
_DROP_TYPES = {"meta", "extra", "status"}


@dataclass
class StreamStats:
    path: Path
    logical_name: str
    rows_before: int = 0
    rows_after: int = 0
    status: str = "pending"
    backup: Path | None = None
    error: str = ""
    unwrapped_t_msg: int = 0
    patches_applied: int = 0
    trace_updates_applied: int = 0
    dropped_rows: int = 0
    thinking_rows: int = 0
    tool_call_rows: int = 0
    tool_results_linked: int = 0
    orphan_tool_results: int = 0
    minted_msg_id: int = 0
    dedup_msg_id: int = 0
    minted_ts: int = 0
    seq_reassigned: int = 0
    parent_rebuilt: int = 0
    minted_conversation_id: int = 0
    minted_user_id: int = 0


@dataclass
class Totals:
    scanned: int = 0
    changed: int = 0
    would_change: int = 0
    unchanged: int = 0
    errors: int = 0
    files: list[StreamStats] = field(default_factory=list)


def _mk_msg_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _iter_conversation_dirs(conversations_dir: Path) -> Iterable[Path]:
    if not conversations_dir.is_dir():
        return []
    dirs: list[Path] = []
    for user_dir in sorted(conversations_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        for conv_dir in sorted(user_dir.iterdir()):
            if conv_dir.is_dir():
                dirs.append(conv_dir)
    return dirs


def _iter_logical_streams(conv_dir: Path) -> Iterable[tuple[Path, str]]:
    yield conv_dir / "transcript.jsonl", "transcript.jsonl"
    yield conv_dir / "shared.jsonl", "shared.jsonl"
    for entry in sorted(conv_dir.iterdir()):
        if not entry.is_dir() or entry.name in _SKIP_DIRS:
            continue
        yield entry / "context.jsonl", f"{entry.name}/context.jsonl"


def _derive_ids(path: Path) -> tuple[str, str]:
    parts = path.resolve().parts
    try:
        idx = parts.index("conversations")
    except ValueError:
        return "", ""
    if idx + 2 >= len(parts):
        return "", ""
    return parts[idx + 2], parts[idx + 1]


def _load_rows(path: Path) -> list[dict[str, Any]]:
    log = SegmentedJsonl(path)
    if not log.exists():
        return []
    rows: list[dict[str, Any]] = []
    for row in log.iter_rows():
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSON row in {_safe_rel(path)}")
        rows.append(row)
    return rows


def _backup_rows(conv_dir: Path, logical_name: str,
                 rows: list[dict[str, Any]]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = conv_dir / _BACKUP_DIR_NAME / stamp / logical_name
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return backup


def _ensure_msg_id(row: dict[str, Any], seen: set[str],
                   stats: StreamStats) -> str:
    msg_id = str(row.get("msg_id") or "")
    if not msg_id:
        msg_id = _mk_msg_id()
        while msg_id in seen:
            msg_id = _mk_msg_id()
        row["msg_id"] = msg_id
        stats.minted_msg_id += 1
    elif msg_id in seen:
        msg_id = _mk_msg_id()
        while msg_id in seen:
            msg_id = _mk_msg_id()
        row["msg_id"] = msg_id
        stats.dedup_msg_id += 1
    seen.add(msg_id)
    return msg_id


def _normalize_ts(row: dict[str, Any], fallback: Any,
                  stats: StreamStats) -> None:
    if not row.get("ts"):
        row["ts"] = row.get("timestamp") or fallback or time.time()
        stats.minted_ts += 1
    row.pop("timestamp", None)


def _base_messages(rows: list[dict[str, Any]], stats: StreamStats) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    patches: dict[str, dict[str, Any]] = {}
    trace_updates: dict[str, list[tuple[Any, Any]]] = {}

    for row in rows:
        kind = row.get("t", "")
        if kind == "msg_patch":
            target = row.get("target_msg_id") or row.get("msg_id") or ""
            if target:
                patches[target] = {
                    k: v for k, v in row.items()
                    if k not in {"t", "msg_id", "target_msg_id", "ts", "seq"}
                }
            stats.dropped_rows += 1
            continue
        if kind == "trace_update":
            trace_id = row.get("trace_id") or ""
            if trace_id:
                trace_updates.setdefault(trace_id, []).append(
                    (row.get("entry") or {}, row.get("content_update") or "")
                )
            stats.dropped_rows += 1
            continue
        if kind in _DROP_TYPES:
            stats.dropped_rows += 1
            continue

        msg = dict(row)
        if kind == "msg":
            msg.pop("t", None)
            msg.pop("private", None)
            stats.unwrapped_t_msg += 1
        elif kind:
            stats.dropped_rows += 1
            continue
        if not msg.get("role"):
            stats.dropped_rows += 1
            continue
        messages.append(msg)

    if patches:
        for msg in messages:
            patch = patches.get(msg.get("msg_id", ""))
            if patch:
                msg.update(patch)
                stats.patches_applied += 1

    if trace_updates:
        for msg in messages:
            if msg.get("role") != "sub_agent_trace":
                continue
            updates = trace_updates.get(msg.get("trace_id") or "")
            if not updates:
                continue
            trace = list(msg.get("trace") or [])
            content = msg.get("content") or ""
            for entry, content_update in updates:
                if entry:
                    trace.append(entry)
                if content_update:
                    content += content_update
            msg["trace"] = trace
            msg["content"] = content
            stats.trace_updates_applied += len(updates)

    return messages


def _tool_call_id(call: Any) -> str:
    return str(call.get("id") or call.get("tool_call_id") or call.get("tc_id") or "") if isinstance(call, dict) else ""


def _tool_call_row(anchor: dict[str, Any], call: dict[str, Any],
                   parent_id: str) -> dict[str, Any]:
    tcid = _tool_call_id(call)
    row = {
        "role": "tool_call",
        "content": call.get("content", ""),
        "parent_message_id": parent_id,
        "tool_call_id": tcid,
    }
    if call.get("msg_id"):
        row["msg_id"] = call["msg_id"]
    if call.get("ts") or call.get("timestamp"):
        row["ts"] = call.get("ts") or call.get("timestamp")
    if call.get("name"):
        row["tool_name"] = call["name"]
        row["name"] = call["name"]
    if "arguments" in call:
        row["arguments"] = call.get("arguments")
    elif "input" in call:
        row["arguments"] = call.get("input")
    for key in ("source", "channel", "conversation_id", "user_id"):
        if anchor.get(key) is not None:
            row[key] = anchor[key]
    return row


def _thinking_row(anchor: dict[str, Any], parent_id: str) -> dict[str, Any] | None:
    thinking = anchor.pop("thinking", "")
    signature = anchor.pop("thinking_signature", "")
    if not thinking and not signature:
        return None
    row = {
        "role": "thinking",
        "content": thinking or "",
        "parent_message_id": parent_id,
    }
    if signature:
        row["thinking_signature"] = signature
    for key in ("source", "channel", "conversation_id", "user_id", "ts", "timestamp"):
        if anchor.get(key) is not None:
            row[key] = anchor[key]
    return row


def _expand_provider_turn_rows(messages: list[dict[str, Any]],
                               stats: StreamStats) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_assistant_id = ""
    tool_call_msg_by_id: dict[str, str] = {}

    for raw in messages:
        msg = dict(raw)
        role = msg.get("role", "")
        if role == "assistant":
            tool_calls = msg.pop("tool_calls", None) or []
            anchor_id = _ensure_msg_id(msg, seen_ids, stats)
            msg.pop("tool_call_id", None)
            out.append(msg)
            last_assistant_id = anchor_id

            trow = _thinking_row(msg, anchor_id)
            if trow is not None:
                _ensure_msg_id(trow, seen_ids, stats)
                out.append(trow)
                stats.thinking_rows += 1

            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                crow = _tool_call_row(msg, call, anchor_id)
                tcid = crow.get("tool_call_id") or ""
                call_id = _ensure_msg_id(crow, seen_ids, stats)
                out.append(crow)
                stats.tool_call_rows += 1
                if tcid:
                    tool_call_msg_by_id[tcid] = call_id
            continue

        if role == "thinking":
            _ensure_msg_id(msg, seen_ids, stats)
            if last_assistant_id and msg.get("parent_message_id") != last_assistant_id:
                msg["parent_message_id"] = last_assistant_id
                stats.parent_rebuilt += 1
            out.append(msg)
            continue

        if role == "tool_call":
            msg_id = _ensure_msg_id(msg, seen_ids, stats)
            tcid = msg.get("tool_call_id") or msg.get("tc_id") or ""
            if tcid and not msg.get("tool_call_id"):
                msg["tool_call_id"] = tcid
            if last_assistant_id and msg.get("parent_message_id") != last_assistant_id:
                msg["parent_message_id"] = last_assistant_id
                stats.parent_rebuilt += 1
            if tcid:
                tool_call_msg_by_id[str(tcid)] = msg_id
            out.append(msg)
            continue

        if role == "tool":
            _ensure_msg_id(msg, seen_ids, stats)
            tcid = str(msg.get("tool_call_id") or msg.get("tc_id") or "")
            if tcid and not msg.get("tool_call_id"):
                msg["tool_call_id"] = tcid
            parent_id = tool_call_msg_by_id.get(tcid)
            if parent_id:
                if msg.get("parent_message_id") != parent_id:
                    msg["parent_message_id"] = parent_id
                    stats.parent_rebuilt += 1
                stats.tool_results_linked += 1
            elif tcid:
                stats.orphan_tool_results += 1
            out.append(msg)
            continue

        _ensure_msg_id(msg, seen_ids, stats)
        out.append(msg)

    return out


def _enforce_stream_invariants(rows: list[dict[str, Any]], path: Path,
                               stats: StreamStats) -> None:
    cid, user_id = _derive_ids(path)
    running_seq = 0
    for row in rows:
        _normalize_ts(row, None, stats)
        seq = row.get("seq")
        if not isinstance(seq, int) or seq <= running_seq:
            running_seq += 1
            row["seq"] = running_seq
            stats.seq_reassigned += 1
        else:
            running_seq = seq
        if cid and not row.get("conversation_id"):
            row["conversation_id"] = cid
            stats.minted_conversation_id += 1
        if user_id and not row.get("user_id"):
            row["user_id"] = user_id
            stats.minted_user_id += 1


def _normalize_stream(path: Path, logical_name: str, conv_dir: Path,
                      apply: bool) -> StreamStats:
    stats = StreamStats(path=path, logical_name=logical_name)
    log = SegmentedJsonl(path)
    if not log.exists():
        stats.status = "missing"
        return stats

    try:
        before = _load_rows(path)
        stats.rows_before = len(before)
        after = _expand_provider_turn_rows(_base_messages(before, stats), stats)
        _enforce_stream_invariants(after, path, stats)
        stats.rows_after = len(after)
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        return stats

    if after == before:
        stats.status = "unchanged"
        return stats
    if not apply:
        stats.status = "would_change"
        return stats

    try:
        stats.backup = _backup_rows(conv_dir, logical_name, before)
        log.replace_dicts(after)
        stats.status = "changed"
        return stats
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        return stats


def migrate(conversations_dir: Path, apply: bool,
            only_conversation: str = "") -> Totals:
    totals = Totals()
    for conv_dir in _iter_conversation_dirs(conversations_dir):
        if only_conversation and conv_dir.name != only_conversation:
            continue
        for path, logical_name in _iter_logical_streams(conv_dir):
            stats = _normalize_stream(path, logical_name, conv_dir, apply)
            if stats.status == "missing":
                continue
            totals.scanned += 1
            totals.files.append(stats)
            if stats.status == "changed":
                totals.changed += 1
            elif stats.status == "would_change":
                totals.would_change += 1
            elif stats.status == "unchanged":
                totals.unchanged += 1
            elif stats.status == "error":
                totals.errors += 1
    return totals


def _print_report(totals: Totals, apply: bool, verbose: bool) -> None:
    mode = "apply" if apply else "dry-run"
    print(f"mode={mode}")
    print(
        "scanned={scanned} would_change={would_change} changed={changed} "
        "unchanged={unchanged} errors={errors}".format(
            scanned=totals.scanned,
            would_change=totals.would_change,
            changed=totals.changed,
            unchanged=totals.unchanged,
            errors=totals.errors,
        )
    )
    for item in totals.files:
        if not verbose and item.status == "unchanged":
            continue
        suffix = ""
        if item.backup:
            suffix += f" backup={_safe_rel(item.backup)}"
        if item.error:
            suffix += f" error={item.error}"
        print(
            f"{item.status:12s} before={item.rows_before:7d} "
            f"after={item.rows_after:7d} path={_safe_rel(item.path)} "
            f"unwrap={item.unwrapped_t_msg} thinking={item.thinking_rows} "
            f"tool_call={item.tool_call_rows} linked={item.tool_results_linked} "
            f"orphan_tool={item.orphan_tool_results} patch={item.patches_applied} "
            f"trace={item.trace_updates_applied} drop={item.dropped_rows} "
            f"mid={item.minted_msg_id}/{item.dedup_msg_id} "
            f"ts={item.minted_ts} seq={item.seq_reassigned} "
            f"parent={item.parent_rebuilt}{suffix}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversations-dir", type=Path,
                        default=DEFAULT_CONVERSATIONS_DIR,
                        help="Conversation root. Default: data/runtime/conversations")
    parser.add_argument("--conversation-id", default="",
                        help="Migrate only one conversation directory name.")
    parser.add_argument("--apply", action="store_true",
                        help="Write migrated streams. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only. This is the default.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print unchanged streams too.")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("choose either --apply or --dry-run")

    conversations_dir = args.conversations_dir
    if not conversations_dir.is_absolute():
        conversations_dir = ROOT / conversations_dir
    if not conversations_dir.exists():
        raise SystemExit(f"conversation root not found: {conversations_dir}")

    totals = migrate(
        conversations_dir=conversations_dir,
        apply=bool(args.apply),
        only_conversation=args.conversation_id,
    )
    _print_report(totals, apply=bool(args.apply), verbose=bool(args.verbose))
    return 1 if totals.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
