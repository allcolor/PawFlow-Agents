#!/usr/bin/env python3
"""Run MemoryStore garbage collection for one user.

Default is dry-run. Use --apply to mark stale auto-extracted compaction
memories as ended. A JSON backup is written before apply.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from core.memory_gc import apply_memory_gc
from core.memory_store import MemoryStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean stale auto-extracted memories")
    parser.add_argument("user_id", help="Memory user id, e.g. quentin.anciaux")
    parser.add_argument("--apply", action="store_true", help="Apply the plan; default is dry-run")
    parser.add_argument("--store-dir", default="", help="Override memory store directory")
    args = parser.parse_args()

    if args.store_dir:
        MemoryStore.reset()
        store = MemoryStore(store_dir=args.store_dir)
        MemoryStore._instance = store
    else:
        store = MemoryStore.instance()

    backup = ""
    if args.apply:
        path = store._user_path(args.user_id)
        if path.exists():
            backup_path = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
            shutil.copy2(path, backup_path)
            backup = str(backup_path)

    plan = apply_memory_gc(args.user_id, dry_run=not args.apply)
    out = {
        "user_id": args.user_id,
        "dry_run": not args.apply,
        "backup": backup,
        "applied": plan.get("applied", 0),
        "stats": plan.get("stats", {}),
        "reason_counts": {
            k: v for k, v in plan.get("stats", {}).items()
            if k not in {"total", "active", "auto_compaction", "to_end"}
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
