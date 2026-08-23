#!/usr/bin/env python3
"""Remove leaked ``_secret_env`` mappings from conversation content streams.

The command is idempotent and prints counts only. It deliberately creates no
backup because a backup would retain the secret material being removed.
Encrypted conversations are skipped: run the scrub from the live application
with their key unlocked instead of rewriting ciphertext without its codec.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.segmented_jsonl import SegmentedJsonl  # noqa: E402


DEFAULT_CONVERSATIONS_DIR = ROOT / "data" / "runtime" / "conversations"


@dataclass
class ScrubTotals:
    conversations: int = 0
    streams: int = 0
    changed_streams: int = 0
    changed_rows: int = 0
    removed_keys: int = 0
    encrypted_skipped: int = 0
    errors: int = 0


def _conversation_dirs(root: Path):
    if not root.is_dir():
        return
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith("_"):
            continue
        for conv_dir in sorted(user_dir.iterdir()):
            if conv_dir.is_dir():
                yield conv_dir


def _is_encrypted(conv_dir: Path) -> bool:
    path = conv_dir / "extras.json"
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return bool((data.get("encryption") or {}).get("enabled"))


def _stream_paths(conv_dir: Path):
    yield conv_dir / "transcript.jsonl"
    yield conv_dir / "shared.jsonl"
    for entry in sorted(conv_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith("_"):
            yield entry / "context.jsonl"


def scrub(root: Path, conversation_id: str = "", user_id: str = "",
          exclude_conversation_id: str = "") -> ScrubTotals:
    totals = ScrubTotals()
    for conv_dir in _conversation_dirs(root):
        if user_id and conv_dir.parent.name != user_id:
            continue
        if conversation_id and conv_dir.name != conversation_id:
            continue
        if exclude_conversation_id and conv_dir.name == exclude_conversation_id:
            continue
        totals.conversations += 1
        try:
            if _is_encrypted(conv_dir):
                totals.encrypted_skipped += 1
                continue
            for path in _stream_paths(conv_dir):
                log = SegmentedJsonl(path)
                if not log.exists():
                    continue
                totals.streams += 1
                changed_rows, removed_keys = log.scrub_secret_runtime_values()
                if changed_rows:
                    totals.changed_streams += 1
                    totals.changed_rows += changed_rows
                    totals.removed_keys += removed_keys
        except Exception:
            totals.errors += 1
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversations-dir", type=Path,
                        default=DEFAULT_CONVERSATIONS_DIR)
    parser.add_argument("--conversation-id", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--exclude-conversation-id", default="")
    args = parser.parse_args()
    root = args.conversations_dir
    if not root.is_absolute():
        root = ROOT / root
    totals = scrub(
        root,
        conversation_id=args.conversation_id,
        user_id=args.user_id,
        exclude_conversation_id=args.exclude_conversation_id,
    )
    print(
        "conversations={conversations} streams={streams} "
        "changed_streams={changed_streams} changed_rows={changed_rows} "
        "removed_keys={removed_keys} encrypted_skipped={encrypted_skipped} "
        "errors={errors}".format(**vars(totals)))
    return 1 if totals.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
