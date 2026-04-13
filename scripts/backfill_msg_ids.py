"""One-shot migration: ensure every persisted message has a msg_id.

For each conversation, walk transcript.jsonl + shared.jsonl + every
agent's context.jsonl. Assign UUIDs where missing — and propagate the
SAME UUID across files for the SAME message (matched by ts, with a
content-fingerprint tiebreaker for ts-collisions).

Run: python scripts/backfill_msg_ids.py [--dry-run]

The transcript is the source of truth: any (ts, fp) seen there gets a
single UUID; the same key in shared/agent contexts inherits it.
Context-only rows (no transcript match) get fresh independent UUIDs.

Skips system messages — they're ephemeral and don't carry msg_id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "runtime" / "conversations"


def _content_fp(content) -> str:
    """Stable fingerprint for content (string or list-of-parts)."""
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                else:
                    parts.append(p.get("type", "?"))
            else:
                parts.append(str(p))
        s = "\n".join(parts)
    elif content is None:
        s = ""
    else:
        s = str(content)
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()[:12]


def _line_key(line: dict) -> Optional[Tuple]:
    """Identity key for cross-file matching: (role, ts_rounded, content_fp).

    Returns None when the row has no usable timestamp — rows without ts
    are not safe to merge across files (content alone collides on empty
    or near-empty messages); each such row gets its own fresh UUID.
    """
    if line.get("t") and line.get("t") != "msg":
        return None
    role = line.get("role", "")
    if role == "system":
        return None
    ts = line.get("ts") or line.get("timestamp") or 0
    if not ts:
        return None
    ts_key = round(float(ts), 3)
    return (role, ts_key, _content_fp(line.get("content", "")))


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return out


def _write_jsonl(path: Path, lines: list, dry_run: bool):
    if dry_run:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _process_file(path: Path, key_to_id: Dict[Tuple, str],
                  is_transcript: bool, dry_run: bool) -> Tuple[int, int, int]:
    """Returns (added_msgid, propagated, total_msgs)."""
    lines = _read_jsonl(path)
    if not lines:
        return 0, 0, 0
    added = 0
    propagated = 0
    total = 0
    for ln in lines:
        if ln.get("t") and ln.get("t") != "msg":
            continue
        if ln.get("role") == "system":
            continue
        total += 1
        key = _line_key(ln)
        existing_mid = ln.get("msg_id", "")

        if key is None:
            # No identity key (no role/ts) — keep existing or generate
            if not existing_mid:
                ln["msg_id"] = uuid.uuid4().hex
                added += 1
            continue

        canonical = key_to_id.get(key)
        if canonical:
            # Propagate the canonical id
            if existing_mid != canonical:
                ln["msg_id"] = canonical
                propagated += 1
        elif existing_mid:
            key_to_id[key] = existing_mid
        else:
            # Mint a new id; transcript pass establishes the canonical
            ln["msg_id"] = uuid.uuid4().hex
            key_to_id[key] = ln["msg_id"]
            added += 1
    _write_jsonl(path, lines, dry_run)
    return added, propagated, total


def _process_conv(conv_dir: Path, dry_run: bool) -> dict:
    transcript = conv_dir / "transcript.jsonl"
    shared = conv_dir / "shared.jsonl"

    key_to_id: Dict[Tuple, str] = {}

    stats = {
        "added": 0,
        "propagated": 0,
        "total": 0,
        "files": 0,
    }

    # PASS 1 — transcript first to seed canonical ids.
    if transcript.exists():
        a, p, t = _process_file(transcript, key_to_id, True, dry_run)
        stats["added"] += a
        stats["propagated"] += p
        stats["total"] += t
        stats["files"] += 1

    # PASS 2 — agent contexts + shared inherit when keys match.
    aux_files = []
    if shared.exists():
        aux_files.append(shared)
    for entry in conv_dir.iterdir():
        if entry.is_dir():
            ctx = entry / "context.jsonl"
            if ctx.exists():
                aux_files.append(ctx)
    for path in aux_files:
        a, p, t = _process_file(path, key_to_id, False, dry_run)
        stats["added"] += a
        stats["propagated"] += p
        stats["total"] += t
        stats["files"] += 1

    return stats


def _verify(root: Path) -> int:
    """Walk every conv and check the cross-file UUID rule.

    Rule: a (role, ts, content_fp) key must map to AT MOST one msg_id
    across transcript + shared + every agent context of the same conv.
    Returns the number of violations found.
    """
    violations = 0
    rows_seen = 0
    rows_with_id = 0
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir():
            continue
        for conv_dir in sorted(user_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            files = []
            tr = conv_dir / "transcript.jsonl"
            if tr.exists():
                files.append(tr)
            sh = conv_dir / "shared.jsonl"
            if sh.exists():
                files.append(sh)
            for entry in conv_dir.iterdir():
                if entry.is_dir() and (entry / "context.jsonl").exists():
                    files.append(entry / "context.jsonl")
            # key → set of msg_ids
            key_ids: Dict[Tuple, set] = {}
            missing_ids = []
            for path in files:
                for ln in _read_jsonl(path):
                    if ln.get("t") and ln.get("t") != "msg":
                        continue
                    if ln.get("role") == "system":
                        continue
                    rows_seen += 1
                    mid = ln.get("msg_id", "")
                    if mid:
                        rows_with_id += 1
                    else:
                        missing_ids.append((path.name, ln.get("role"), ln.get("ts")))
                    key = _line_key(ln)
                    if key is None or not mid:
                        continue
                    key_ids.setdefault(key, set()).add(mid)
            for key, ids in key_ids.items():
                if len(ids) > 1:
                    violations += 1
                    print(f"  VIOLATION {user_dir.name}/{conv_dir.name}: "
                          f"key={key} has {len(ids)} distinct ids: {sorted(ids)}")
            if missing_ids:
                violations += len(missing_ids)
                for name, role, ts in missing_ids[:5]:
                    print(f"  MISSING {user_dir.name}/{conv_dir.name}/{name}: "
                          f"role={role} ts={ts}")
                if len(missing_ids) > 5:
                    print(f"  ... and {len(missing_ids) - 5} more missing in {conv_dir.name}")
    print(f"\nVerify: {rows_seen} rows ({rows_with_id} with msg_id), "
          f"{violations} violation(s).")
    return violations


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report only; don't rewrite files.")
    ap.add_argument("--verify", action="store_true",
                    help="Verify the cross-file UUID rule and exit.")
    ap.add_argument("--root", type=Path, default=DATA_ROOT,
                    help="Conversations root (defaults to data/runtime/conversations).")
    args = ap.parse_args()
    if args.verify:
        return 1 if _verify(args.root) else 0

    if not args.root.is_dir():
        print(f"No conversations dir: {args.root}", file=sys.stderr)
        return 1

    grand = {"added": 0, "propagated": 0, "total": 0, "files": 0, "convs": 0}
    for user_dir in sorted(args.root.iterdir()):
        if not user_dir.is_dir():
            continue
        for conv_dir in sorted(user_dir.iterdir()):
            if not conv_dir.is_dir():
                continue
            if not (conv_dir / "transcript.jsonl").exists():
                continue
            s = _process_conv(conv_dir, args.dry_run)
            print(f"{user_dir.name}/{conv_dir.name}: "
                  f"+{s['added']} new ids, ={s['propagated']} propagated, "
                  f"{s['total']} msgs across {s['files']} files")
            grand["added"] += s["added"]
            grand["propagated"] += s["propagated"]
            grand["total"] += s["total"]
            grand["files"] += s["files"]
            grand["convs"] += 1

    suffix = " (dry-run, no files written)" if args.dry_run else ""
    print(f"\nDone{suffix}: {grand['convs']} convs, {grand['files']} files, "
          f"{grand['total']} msgs — added {grand['added']} new ids, "
          f"propagated {grand['propagated']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
