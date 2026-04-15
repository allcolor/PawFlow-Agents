"""One-shot migration: guarantee every on-disk message has `seq` and `ts`.

Run once. Walks every .jsonl under data/runtime/conversations/ and
fills in missing `seq` / `ts` on each message entry. After this runs,
the codebase can (and does) assume both fields are always present —
no more `or 0` fallbacks.

Strategy per file:
  - Read all entries, preserve file order (= creation order).
  - `ts`: if an entry is missing it, interpolate between the prev and
    next entries that do have one. If the whole file lacks ts, fall back
    to file mtime (floor) + tiny increments to keep strict ordering.
  - `seq`: assign a fresh strictly-monotonic counter, starting from
    (global_max_seen_before_this_file + 1). Walks every file in a
    deterministic order (sorted path) so the assignments are stable
    if you ever rerun.

Rewrites files atomically (tmp + rename). Safe to interrupt.
"""

import json
import os
import sys
from pathlib import Path

# Allow running as a plain script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.paths as _p  # noqa: E402


def _needs_fix(m: dict) -> bool:
    if not isinstance(m, dict):
        return False
    if m.get("t") and m.get("t") != "msg":
        return False  # transcript control entries (msg_patch, trace_update)
    has_ts = m.get("ts") or m.get("timestamp")
    has_seq = m.get("seq")
    return not (has_ts and has_seq)


def _fill_timestamps(entries: list, file_mtime: float):
    """Fill missing ts via interpolation from neighbours, fallback to mtime."""
    n = len(entries)
    # Forward pass: for each missing, find prev_ts
    prev_ts = None
    for i, e in enumerate(entries):
        ts = e.get("ts") or e.get("timestamp")
        if ts:
            prev_ts = float(ts)
            continue
        # Search forward for next ts
        next_ts = None
        for j in range(i + 1, n):
            t = entries[j].get("ts") or entries[j].get("timestamp")
            if t:
                next_ts = float(t)
                break
        if prev_ts is not None and next_ts is not None:
            # Interpolate
            gap = next_ts - prev_ts
            # Count how many missing between (inclusive of i, exclusive of next)
            missing_k = 1
            for j in range(i + 1, n):
                t = entries[j].get("ts") or entries[j].get("timestamp")
                if t:
                    break
                missing_k += 1
            step = gap / (missing_k + 1) if missing_k > 0 else 1e-6
            e["ts"] = prev_ts + step
        elif prev_ts is not None:
            e["ts"] = prev_ts + 1e-6
        elif next_ts is not None:
            e["ts"] = next_ts - 1e-6
        else:
            # File has no ts at all — use mtime + tiny increment by index
            e["ts"] = float(file_mtime) + i * 1e-6
        prev_ts = float(e["ts"])


def _fill_seqs(entries: list, counter_start: int) -> int:
    """Assign strictly-monotonic seq to entries missing it. Return next counter."""
    next_seq = counter_start
    for e in entries:
        if not e.get("seq"):
            e["seq"] = next_seq
            next_seq += 1
    return next_seq


def _rewrite_file(path: Path, entries: list):
    tmp = path.with_suffix(path.suffix + ".mig.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.replace(path)


def migrate():
    root = _p.CONVERSATIONS_DIR
    if not root.exists():
        print(f"No conversations dir at {root}")
        return 0
    # Global seq counter: start from current max on disk
    print(f"Scanning {root}...")
    current_max = 0
    files = sorted(root.rglob("*.jsonl"))
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                s = m.get("seq") or 0
                if isinstance(s, int) and s > current_max:
                    current_max = s
    print(f"Current max seq on disk: {current_max}")

    next_seq = current_max + 1
    fixed_files = 0
    fixed_msgs = 0
    for path in files:
        entries = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    # Keep malformed lines out of migration (never happens on valid files)
                    print(f"  skip malformed line in {path}")

        missing = [e for e in entries if _needs_fix(e)]
        if not missing:
            continue

        mtime = path.stat().st_mtime
        _fill_timestamps(entries, mtime)
        next_seq = _fill_seqs(entries, next_seq)
        _rewrite_file(path, entries)
        fixed_files += 1
        fixed_msgs += len(missing)
        print(f"  fixed {len(missing)} msgs in {path.relative_to(root)}")

    print(f"\nDone: {fixed_msgs} messages fixed across {fixed_files} files. "
          f"Next seq would be {next_seq}.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate())
