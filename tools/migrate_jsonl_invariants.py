#!/usr/bin/env python3
"""One-shot migration: enforce the five-field invariant
(msg_id, ts, seq, conversation_id, user_id) + strict-monotonic-seq on
every conversation jsonl file on disk.

Run from the PawFlow root:
    python tools/migrate_jsonl_invariants.py

For each conversation:
  - transcript.jsonl          — full authoritative history
  - shared.jsonl              — shared context
  - <agent>/context.jsonl     — per-agent LLM context
  - <agent>/pending.jsonl     — queued inputs (rare but same rules)

For every line:
  1. Ensure an OWN msg_id (uuid4[:12]) — mint if missing.
     For legacy msg_patch that squatted msg_id on the target, rename
     the field to target_msg_id and mint a fresh msg_id for the patch
     line itself (unless target_msg_id is already present, in which
     case the file was already migrated).
  2. Ensure ts — mint time.time() if missing.
  3. Ensure a strictly-increasing seq. seq is reassigned in a single
     sweep per-file: walk lines in physical order, track the running
     max, assign max+1 wherever seq is missing OR violates monotony.
     Lines that already have a seq respecting monotony are left
     untouched (no churn on healthy data).

The script is idempotent — re-running after migration is a no-op.
Backups are made to <file>.bak before any rewrite (only when the file
actually changes).
"""
import json
import shutil
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONV_ROOT = ROOT / "data" / "runtime" / "conversations"


def _mk_msg_id() -> str:
    return uuid.uuid4().hex[:12]


def _derive_cid_user(path: Path) -> tuple:
    """Infer (conversation_id, user_id) from the jsonl file's location.

    The store layout is data/runtime/conversations/<user>/<conv>/...
    Both fields are embedded in the path; we reflect them into every
    record during migration so the invariant holds from the line
    itself (no need to join against the folder structure).
    """
    parts = path.resolve().parts
    # Find index of "conversations" dir
    try:
        idx = parts.index("conversations")
    except ValueError:
        return "", ""
    if idx + 2 >= len(parts):
        return "", ""
    user = parts[idx + 1]
    conv = parts[idx + 2]
    return conv, user


def _migrate_file(path: Path) -> dict:
    """Return a dict with per-file stats; rewrite file if anything changed."""
    cid, user_id = _derive_cid_user(path)
    stats = {
        "file": str(path.relative_to(ROOT)),
        "lines": 0, "touched": 0,
        "minted_msg_id": 0, "dedup_msg_id": 0, "minted_ts": 0,
        "seq_reassigned": 0, "patch_renamed": 0,
        "minted_cid": 0, "minted_user": 0,
    }
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        stats["error"] = f"read: {e}"
        return stats

    out_lines = []
    running_max_seq = 0
    seen_mids = set()
    changed = False

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            out_lines.append("")
            continue
        stats["lines"] += 1
        try:
            obj = json.loads(raw)
        except Exception:
            out_lines.append(raw)
            continue
        if not isinstance(obj, dict):
            out_lines.append(raw)
            continue
        before = dict(obj)

        # msg_patch legacy rename: old format squatted msg_id on the
        # target. If we see a patch without target_msg_id, treat the
        # current msg_id as the target and mint a fresh patch msg_id.
        if obj.get("t") == "msg_patch" and not obj.get("target_msg_id"):
            old_target = obj.get("msg_id", "")
            if old_target:
                obj["target_msg_id"] = old_target
                obj["msg_id"] = _mk_msg_id()
                stats["patch_renamed"] += 1

        # Own msg_id — mint one on every duplicate or missing value.
        mid = obj.get("msg_id")
        if not mid:
            mid = _mk_msg_id()
            while mid in seen_mids:
                mid = _mk_msg_id()
            obj["msg_id"] = mid
            stats["minted_msg_id"] += 1
        elif mid in seen_mids:
            # Legacy duplicate (assistant msg written once per tool_call
            # in an early version of the writer). Every persisted line
            # must own a unique identity — mint a fresh one.
            new_mid = _mk_msg_id()
            while new_mid in seen_mids:
                new_mid = _mk_msg_id()
            obj["msg_id"] = new_mid
            mid = new_mid
            stats["dedup_msg_id"] = stats.get("dedup_msg_id", 0) + 1
        seen_mids.add(mid)

        # Own ts
        if "ts" not in obj and "timestamp" not in obj:
            obj["ts"] = time.time()
            stats["minted_ts"] += 1

        # Strictly-monotonic seq. Keep the existing value when it
        # already respects the running max (no churn on healthy lines).
        s = obj.get("seq")
        if not isinstance(s, int) or s <= running_max_seq:
            running_max_seq += 1
            obj["seq"] = running_max_seq
            stats["seq_reassigned"] += 1
        else:
            running_max_seq = s

        # conversation_id + user_id embedded in the record itself
        # (derived from the folder path).
        if cid and not obj.get("conversation_id"):
            obj["conversation_id"] = cid
            stats["minted_cid"] += 1
        if user_id and not obj.get("user_id"):
            obj["user_id"] = user_id
            stats["minted_user"] += 1

        if obj != before:
            stats["touched"] += 1
            changed = True

        out_lines.append(json.dumps(obj, ensure_ascii=False))

    if changed:
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        path.write_text("\n".join(out_lines) + ("\n" if raw_lines and raw_lines[-1] == "" else "\n"),
                        encoding="utf-8")
    return stats


def main():
    if not CONV_ROOT.exists():
        print(f"No conversations dir at {CONV_ROOT}")
        return 0
    totals = {"files": 0, "touched_files": 0, "lines": 0, "touched": 0,
              "minted_msg_id": 0, "dedup_msg_id": 0, "minted_ts": 0,
              "seq_reassigned": 0, "patch_renamed": 0,
              "minted_cid": 0, "minted_user": 0}
    per_file = []
    for p in sorted(CONV_ROOT.rglob("*.jsonl")):
        s = _migrate_file(p)
        totals["files"] += 1
        totals["lines"] += s["lines"]
        totals["touched"] += s["touched"]
        totals["minted_msg_id"] += s["minted_msg_id"]
        totals["dedup_msg_id"] += s["dedup_msg_id"]
        totals["minted_ts"] += s["minted_ts"]
        totals["seq_reassigned"] += s["seq_reassigned"]
        totals["patch_renamed"] += s["patch_renamed"]
        totals["minted_cid"] += s["minted_cid"]
        totals["minted_user"] += s["minted_user"]
        if s["touched"] > 0:
            totals["touched_files"] += 1
            per_file.append(s)

    print(f"Scanned {totals['files']} jsonl files, {totals['lines']} lines")
    print(f"Rewrote {totals['touched_files']} files, {totals['touched']} lines touched")
    print(f"  minted msg_id:          {totals['minted_msg_id']}")
    print(f"  de-duped msg_id:        {totals['dedup_msg_id']}")
    print(f"  minted ts:              {totals['minted_ts']}")
    print(f"  seq reassigned:         {totals['seq_reassigned']}")
    print(f"  msg_patch renamed:      {totals['patch_renamed']}")
    print(f"  minted conversation_id: {totals['minted_cid']}")
    print(f"  minted user_id:         {totals['minted_user']}")
    if per_file:
        print("\nTop 10 most-touched files:")
        per_file.sort(key=lambda x: -x["touched"])
        for s in per_file[:10]:
            print(f"  {s['file']}: {s['touched']}/{s['lines']} lines "
                  f"(mid={s['minted_msg_id']} ts={s['minted_ts']} "
                  f"seq={s['seq_reassigned']} patch={s['patch_renamed']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
