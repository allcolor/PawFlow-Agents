#!/usr/bin/env python3
"""Migrate existing conversation JSONL files to segmented JSONL streams.

The runtime can read both legacy flat files (`transcript.jsonl`,
`shared.jsonl`, `{agent}/context.jsonl`) and segmented directories
(`transcript/`, `shared/`, `{agent}/context/`). This script converts existing
flat conversation streams to segmented storage in a controlled offline pass.

Run from the PawFlow root:

    python scripts/migrate_segmented_jsonl.py --dry-run
    python scripts/migrate_segmented_jsonl.py --apply

The default is dry-run. `--apply` writes segments and removes the legacy flat
file only after a backup has been copied to `_jsonl_migration_backup/` inside
the conversation directory. Re-running after a successful migration is a no-op.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.segmented_jsonl import DEFAULT_MAX_ROWS, SegmentedJsonl  # noqa: E402


DEFAULT_CONVERSATIONS_DIR = ROOT / "data" / "runtime" / "conversations"
_BACKUP_DIR_NAME = "_jsonl_migration_backup"
_SKIP_DIRS = {".git", "summaries", "transcript", "shared", _BACKUP_DIR_NAME}


@dataclass
class FileStats:
    path: Path
    logical_name: str
    rows: int = 0
    bytes: int = 0
    segments: int = 0
    status: str = "pending"
    backup: Path | None = None
    error: str = ""


@dataclass
class Totals:
    scanned: int = 0
    migrated: int = 0
    would_migrate: int = 0
    skipped: int = 0
    errors: int = 0
    rows: int = 0
    bytes: int = 0
    files: list[FileStats] = field(default_factory=list)


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


def _iter_logical_files(conv_dir: Path) -> Iterable[tuple[Path, str]]:
    for name in ("transcript.jsonl", "shared.jsonl"):
        yield conv_dir / name, name
    for entry in sorted(conv_dir.iterdir()):
        if not entry.is_dir() or entry.name in _SKIP_DIRS:
            continue
        yield entry / "context.jsonl", f"{entry.name}/context.jsonl"


def _load_flat_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {lineno}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSON row at line {lineno}")
            rows.append(row)
    return rows


def _backup_flat_file(path: Path, conv_dir: Path, logical_name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = conv_dir / _BACKUP_DIR_NAME / stamp
    backup_path = backup_root / logical_name
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def _migrate_file(path: Path, logical_name: str, conv_dir: Path,
                  apply: bool, max_rows: int) -> FileStats:
    stats = FileStats(path=path, logical_name=logical_name)
    log = SegmentedJsonl(path, max_rows=max_rows)

    if log.is_segmented() and not path.exists():
        stats.status = "already_segmented"
        stats.rows = log.total_rows()
        stats.segments = len(log.iter_paths())
        return stats

    if log.is_segmented() and path.exists():
        stats.status = "skipped_mixed_layout"
        stats.error = "both flat file and segment directory exist"
        return stats

    if not path.exists():
        stats.status = "missing"
        return stats

    try:
        stats.bytes = path.stat().st_size
        rows = _load_flat_rows(path)
        stats.rows = len(rows)
        stats.segments = max(1, (stats.rows + max_rows - 1) // max_rows) if rows else 1
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        return stats

    if not apply:
        stats.status = "would_migrate"
        return stats

    try:
        stats.backup = _backup_flat_file(path, conv_dir, logical_name)
        log.replace_dicts(rows)
        stats.status = "migrated"
        return stats
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        return stats


def migrate(conversations_dir: Path, apply: bool, max_rows: int,
            only_conversation: str = "") -> Totals:
    totals = Totals()
    for conv_dir in _iter_conversation_dirs(conversations_dir):
        if only_conversation and conv_dir.name != only_conversation:
            continue
        for path, logical_name in _iter_logical_files(conv_dir):
            stats = _migrate_file(path, logical_name, conv_dir, apply, max_rows)
            if stats.status == "missing":
                continue
            totals.scanned += 1
            totals.rows += stats.rows
            totals.bytes += stats.bytes
            totals.files.append(stats)
            if stats.status == "migrated":
                totals.migrated += 1
            elif stats.status == "would_migrate":
                totals.would_migrate += 1
            elif stats.status == "already_segmented":
                totals.skipped += 1
            elif stats.status in ("error", "skipped_mixed_layout"):
                totals.errors += 1
    return totals


def _print_report(totals: Totals, apply: bool, verbose: bool) -> None:
    mode = "apply" if apply else "dry-run"
    print(f"mode={mode}")
    print(
        "scanned={scanned} would_migrate={would_migrate} "
        "migrated={migrated} skipped={skipped} errors={errors} "
        "rows={rows} bytes={bytes}".format(
            scanned=totals.scanned,
            would_migrate=totals.would_migrate,
            migrated=totals.migrated,
            skipped=totals.skipped,
            errors=totals.errors,
            rows=totals.rows,
            bytes=totals.bytes,
        )
    )

    interesting = [
        f for f in totals.files
        if verbose or f.status in ("would_migrate", "migrated", "error", "skipped_mixed_layout")
    ]
    for item in interesting:
        suffix = ""
        if item.backup:
            suffix += f" backup={_safe_rel(item.backup)}"
        if item.error:
            suffix += f" error={item.error}"
        print(
            f"{item.status:20s} rows={item.rows:7d} "
            f"segments={item.segments:4d} path={_safe_rel(item.path)}{suffix}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversations-dir", type=Path,
                        default=DEFAULT_CONVERSATIONS_DIR,
                        help="Conversation root. Default: data/runtime/conversations")
    parser.add_argument("--conversation-id", default="",
                        help="Migrate only one conversation directory name.")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                        help=f"Rows per segment. Default: {DEFAULT_MAX_ROWS}")
    parser.add_argument("--apply", action="store_true",
                        help="Write migrated segment files. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only. This is the default.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print already-segmented streams too.")
    args = parser.parse_args()

    if args.max_rows <= 0:
        parser.error("--max-rows must be > 0")
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
        max_rows=int(args.max_rows),
        only_conversation=args.conversation_id,
    )
    _print_report(totals, apply=bool(args.apply), verbose=bool(args.verbose))
    return 1 if totals.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
