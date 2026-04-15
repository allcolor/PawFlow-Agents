#!/usr/bin/env python3
"""Migration: add 'definition' and 'params' to conv_agents entries.

Every conv_agents entry must have:
  - definition: str (the repo .md template name)
  - params: dict (instance parameters, at minimum {name: instance_name})

For existing entries where instance_name matches a definition file,
we set definition = instance_name and params = {name: instance_name}.

Run once, then delete this script.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.paths as paths


def migrate():
    conv_root = paths.RUNTIME_DIR / "conversations"
    if not conv_root.exists():
        print("No conversations directory found.")
        return

    migrated = 0
    skipped = 0

    for extras_file in sorted(conv_root.rglob("extras.json")):
        with open(extras_file) as f:
            data = json.load(f)

        conv_agents = data.get("conv_agents")
        if not conv_agents:
            continue

        changed = False
        for instance_name, cfg in conv_agents.items():
            if "definition" not in cfg:
                cfg["definition"] = instance_name
                changed = True
            if "params" not in cfg:
                cfg["params"] = {"name": instance_name}
                changed = True
            elif "name" not in cfg["params"]:
                cfg["params"]["name"] = instance_name
                changed = True

        if changed:
            with open(extras_file, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            rel = extras_file.relative_to(conv_root)
            print(f"  migrated: {rel}")
            migrated += 1
        else:
            skipped += 1

    print(f"\nDone: {migrated} migrated, {skipped} already up-to-date.")


if __name__ == "__main__":
    print("Migrating conv_agents: adding definition + params...\n")
    migrate()
