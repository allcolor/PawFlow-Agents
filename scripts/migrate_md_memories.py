#!/usr/bin/env python3
"""One-shot: migrate file-based MEMORY.md system into MemoryStore.

Reads /workspace/projects/-workspace/memory/*.md (excluding the index),
parses YAML frontmatter (name/description/type), and calls
MemoryStore.remember() for each entry. Then deletes the source dir.

Dedup: MemoryStore.remember() merges entries with identical text, so re-runs
are safe.
"""

import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory_store import MemoryStore

USER_ID = "quentin.anciaux"
AGENT = "claude"
SRC_DIR = ROOT / "projects" / "-workspace" / "memory"

# Map .md `type:` field → MemoryStore category
TYPE_TO_CATEGORY = {
    "feedback": "preferences",
    "project": "facts",
    "reference": "facts",
    "user": "facts",
}


def parse_frontmatter(text: str):
    """Return (meta_dict, body_str). Supports `---\nkey: value\n---\nbody`."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, parts[2].lstrip("\n")


def main():
    if not SRC_DIR.exists():
        print(f"Nothing to migrate: {SRC_DIR} does not exist")
        return 0

    ms = MemoryStore.instance()
    migrated = 0
    skipped = []

    for md in sorted(SRC_DIR.glob("*.md")):
        if md.name == "MEMORY.md":
            skipped.append(md.name + " (index)")
            continue
        text = md.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if not body.strip():
            skipped.append(md.name + " (empty)")
            continue

        mtype = (meta.get("type") or "").lower()
        category = TYPE_TO_CATEGORY.get(mtype, "")
        name = meta.get("name") or md.stem
        description = meta.get("description") or ""

        # Build memory text: keep title + description as header, then body
        full_text = f"{name}\n{description}\n\n{body.strip()}".strip()

        tags = ["migrated-from-md", mtype] if mtype else ["migrated-from-md"]
        # Add file-stem keywords as tags for retrieval
        for token in md.stem.replace("_", " ").split():
            t = token.lower().strip()
            if t and t not in tags and len(t) >= 3:
                tags.append(t)

        entry = ms.remember(
            user_id=USER_ID,
            text=full_text,
            tags=tags,
            source=f"migrated:{md.name}",
            agent=AGENT,
            category=category,
        )
        print(f"  ✓ {md.name} → id={entry.id} category={category!r} tags={tags}")
        migrated += 1

    print(f"\nMigrated {migrated} memory file(s).")
    if skipped:
        print(f"Skipped: {', '.join(skipped)}")

    # Wipe the source dir — file-based memory system is deprecated.
    shutil.rmtree(SRC_DIR)
    print(f"Removed {SRC_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
