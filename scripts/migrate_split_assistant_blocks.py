"""One-shot migration: split combined assistant(content + tool_calls)
messages into two separate lines.

After this runs, every persisted assistant message matches exactly one
of these shapes:
  - role=assistant, content=<text>, tool_calls=[]  (text-only block)
  - role=assistant, content="",     tool_calls=[...] (tool_calls-only block)

This is the uniform block-per-message layout the providers expect
(anthropic/openai/claude-code regroup split pairs back together via
core.llm_message_regroup.regroup_split_assistant_messages).

Strategy per file (transcript.jsonl / shared.jsonl / <agent>/context.jsonl):
  - Read all entries preserving order.
  - For each combined assistant msg:
      tc_msg = deep copy with content="", thinking moved here, new msg_id,
               ts = original_ts + 1e-6, seq = original_seq + 0.5 placeholder
      original.tool_calls = []; original.thinking = ""
  - Second pass: renumber seq as strict monotone integers starting from
    the first (preserved) seq.
  - Atomic write (tmp + rename).

Idempotent: a file with no combined messages is left untouched.

Usage:
    python3 scripts/migrate_split_assistant_blocks.py          # dry-run
    python3 scripts/migrate_split_assistant_blocks.py --apply  # write
"""
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.paths as _p  # noqa: E402


def _is_combined(obj: dict) -> bool:
    if obj.get("t") != "msg":
        return False
    if obj.get("role") != "assistant":
        return False
    content = obj.get("content")
    has_text = bool(
        (content or "").strip() if isinstance(content, str) else content)
    has_tc = bool(obj.get("tool_calls"))
    return has_text and has_tc


def _split(obj: dict) -> list:
    """Return [text_block, tc_block] for a combined assistant message."""
    # Text block = original minus tool_calls / thinking
    text_block = dict(obj)
    text_block["tool_calls"] = []
    text_block["thinking"] = ""

    # Tool-calls block = fresh msg_id, empty content, carry thinking
    tc_block = dict(obj)
    tc_block["content"] = ""
    tc_block["tool_calls"] = list(obj.get("tool_calls") or [])
    tc_block["thinking"] = obj.get("thinking", "") or ""
    tc_block["msg_id"] = uuid.uuid4().hex[:12]
    # ts ordering: slightly after the text block (seq is renumbered below)
    orig_ts = float(obj.get("ts") or obj.get("timestamp") or 0.0)
    tc_block["ts"] = orig_ts + 1e-6
    tc_block["timestamp"] = tc_block["ts"]
    # Seq placeholder — will be renumbered in the monotone pass.
    tc_block["seq"] = None
    return [text_block, tc_block]


def _renumber_seq(entries: list) -> None:
    """Assign strict monotone integer seq in list order.

    Keeps the seq span tight to the original file range: starts from
    the first entry's existing seq (if any), otherwise 1.
    """
    # Determine starting seq from the first msg entry that already has one.
    start = 1
    for e in entries:
        if e.get("t") == "msg" and isinstance(e.get("seq"), int):
            start = int(e["seq"])
            break
    cur = start
    last = None
    for e in entries:
        if e.get("t") != "msg":
            continue
        # Honour existing seq if already monotone; bump otherwise.
        existing = e.get("seq")
        if isinstance(existing, int) and (last is None or existing > last):
            cur = existing
        else:
            cur = (last or start - 1) + 1
        e["seq"] = cur
        last = cur


def _migrate_file(path: Path, apply: bool) -> tuple:
    """Return (combined_count, total_msgs). apply=False means dry-run."""
    entries = []
    combined = 0
    total = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Preserve as-is (corruption would be obvious elsewhere)
                entries.append({"_raw": line})
                continue
            if obj.get("t") == "msg":
                total += 1
            if _is_combined(obj):
                combined += 1
                entries.extend(_split(obj))
            else:
                entries.append(obj)
    if combined == 0:
        return (0, total)
    if apply:
        _renumber_seq(entries)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in entries:
                if "_raw" in e:
                    f.write(e["_raw"] + "\n")
                else:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return (combined, total)


def main():
    apply = "--apply" in sys.argv
    root = Path("data/runtime/conversations")
    if not root.exists():
        print(f"No conversations dir at {root}")
        return
    files = sorted(
        list(root.glob("*/*/transcript.jsonl"))
        + list(root.glob("*/*/shared.jsonl"))
        + list(root.glob("*/*/*/context.jsonl")))
    print(f"Scanning {len(files)} jsonl files (apply={apply})...")
    grand_combined = 0
    grand_total = 0
    touched = 0
    for p in files:
        c, t = _migrate_file(p, apply)
        grand_combined += c
        grand_total += t
        if c:
            touched += 1
            print(f"  {'split' if apply else 'would split'} {c:5d}/{t:6d} in {p}")
    print(f"\nTotal: {grand_combined} combined / {grand_total} msgs "
          f"across {touched}/{len(files)} files.")
    if not apply and grand_combined:
        print("Re-run with --apply to rewrite files.")


if __name__ == "__main__":
    main()
