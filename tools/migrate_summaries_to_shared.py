#!/usr/bin/env python3
"""One-shot migration: wipe per-agent summaries/<agent>/ dirs.

Old layout: every agent had its own pyramid under
  data/runtime/conversations/<user>/<conv>/summaries/<agent>/
  data/runtime/conversations/<user>/<conv>/summaries/_shared/  (if any)

New layout: one pyramid per conv, shared by all agents.
  data/runtime/conversations/<user>/<conv>/summaries/_shared/

Strategy:
  - Delete every `summaries/<agent>/` directory (where agent != "_shared").
  - Leave `summaries/_shared/` alone if it exists.
  - Conversations will rebuild the shared pyramid lazily on the next
    shared append (via BgBucketBuilder.maybe_trigger) or at the next
    /compact (via build_now_sync).

Dry-run by default. Pass --apply to actually delete.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def iter_agent_summary_dirs(root: Path):
    """Yield every summaries/<agent>/ path (excluding _shared)."""
    # data/runtime/conversations/<user>/<conv>/summaries/<agent>/
    if not root.is_dir():
        return
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for conv_dir in user_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            summaries = conv_dir / "summaries"
            if not summaries.is_dir():
                continue
            for agent_dir in summaries.iterdir():
                if not agent_dir.is_dir():
                    continue
                if agent_dir.name == "_shared":
                    continue
                yield agent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default="data/runtime/conversations",
        help="Root conversations directory (default: data/runtime/conversations)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete (default: dry run)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[migrate-summaries] root not found: {root}")
        return 1

    targets = list(iter_agent_summary_dirs(root))
    if not targets:
        print(f"[migrate-summaries] nothing to do under {root}")
        return 0

    total_files = 0
    total_bytes = 0
    for d in targets:
        n_files = sum(1 for _ in d.rglob("*") if _.is_file())
        try:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            size = 0
        total_files += n_files
        total_bytes += size

    print(f"[migrate-summaries] root: {root}")
    print(f"[migrate-summaries] {len(targets)} per-agent summary dirs")
    print(f"[migrate-summaries]   {total_files} files, "
          f"{total_bytes / 1024:.0f} KiB total")
    print(f"[migrate-summaries] mode: {'APPLY (deleting)' if args.apply else 'DRY RUN'}")
    print()

    for d in targets:
        print(f"  {'[DEL]' if args.apply else '[would DEL]'} {d}")

    if not args.apply:
        print()
        print("[migrate-summaries] dry run complete. Re-run with --apply to delete.")
        return 0

    print()
    errors = 0
    for d in targets:
        try:
            shutil.rmtree(d)
        except OSError as e:
            print(f"  [ERR] {d}: {e}")
            errors += 1
    print()
    print(f"[migrate-summaries] deleted {len(targets) - errors}/{len(targets)} dirs "
          f"(errors: {errors})")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
