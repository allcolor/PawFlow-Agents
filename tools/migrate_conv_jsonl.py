#!/usr/bin/env python3
"""One-shot migration: JSON conversation files -> JSONL format.

Converts each data/conversations/*.json to *.jsonl.
Dry-run by default. Use --apply to write changes.
"""

import json
import sys
import time
from pathlib import Path


def migrate_file(json_path: Path, dry_run: bool = True) -> dict:
    """Convert a single .json conversation file to .jsonl."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "error", "error": str(e)}

    lines = []

    # Meta line
    lines.append({
        "t": "meta",
        "user_id": data.get("user_id", ""),
        "status": data.get("status", "idle"),
        "created_at": data.get("created_at", 0),
        "expires_at": data.get("expires_at", 0),
    })

    # Messages -> msg lines
    for m in data.get("messages", []):
        line = {"t": "msg"}
        for k, v in m.items():
            if k == "timestamp":
                line["ts"] = v
            else:
                line[k] = v
        if "ts" not in line:
            line["ts"] = data.get("updated_at", time.time())
        lines.append(line)

    # Shared context -> ctx replace
    if data.get("context") is not None:
        lines.append({
            "t": "ctx", "agent": "", "op": "replace",
            "data": data["context"],
        })

    # Agent contexts -> ctx replace per agent
    for agent_name, ctx_msgs in (data.get("agent_contexts") or {}).items():
        if ctx_msgs is not None:
            lines.append({
                "t": "ctx", "agent": agent_name, "op": "replace",
                "data": ctx_msgs,
            })

    # Extras -> extra lines
    for key, value in (data.get("extra") or {}).items():
        lines.append({"t": "extra", "key": key, "value": value})

    # Status
    status = data.get("status", "idle")
    if status != "idle":
        lines.append({"t": "status", "status": status})

    jsonl_path = json_path.with_suffix(".jsonl")

    if dry_run:
        print(f"  WOULD migrate {json_path.name} -> {jsonl_path.name}: "
              f"{len(data.get('messages', []))} msgs, "
              f"{len(lines)} lines")
        return {"status": "dry_run", "lines": len(lines)}

    # Write JSONL
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # Remove old JSON
    json_path.unlink()
    print(f"  MIGRATED {json_path.name} -> {jsonl_path.name}: {len(lines)} lines")
    return {"status": "migrated", "lines": len(lines)}


def main():
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print("DRY RUN — use --apply to write changes\n")
    else:
        print("APPLYING CHANGES\n")

    store_dir = Path("data/conversations")
    if not store_dir.exists():
        print("No data/conversations directory found.")
        return

    total_files = 0
    total_lines = 0

    for json_path in sorted(store_dir.glob("*.json")):
        result = migrate_file(json_path, dry_run)
        if result.get("status") in ("migrated", "dry_run"):
            total_files += 1
            total_lines += result.get("lines", 0)

    print(f"\n{'Would migrate' if dry_run else 'Migrated'}: "
          f"{total_files} file(s), {total_lines} total lines")
    if dry_run and total_files:
        print("Run with --apply to execute migration.")


if __name__ == "__main__":
    main()
