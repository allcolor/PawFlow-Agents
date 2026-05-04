#!/usr/bin/env python3
"""Repair PawFlow shared and per-agent context files from the transcript.

The transcript is the canonical conversation record. Shared and agent context
files are derived views used for LLM prompting and context inspection. This
script rebuilds those derived files when a routing/rendering bug has corrupted
or diverged them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.conversation_store import ConversationStore  # noqa: E402


def _counts(messages: Iterable[dict]) -> dict:
    role_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    user_targets: dict[str, int] = {}
    total = 0
    for msg in messages:
        total += 1
        role = str(msg.get("role") or "")
        role_counts[role] = role_counts.get(role, 0) + 1
        source = msg.get("source") or {}
        stype = str(source.get("type") or "") if isinstance(source, dict) else ""
        source_counts[stype] = source_counts.get(stype, 0) + 1
        if role == "user":
            target = ""
            if isinstance(source, dict):
                target = str(source.get("target_agent") or "")
            user_targets[target] = user_targets.get(target, 0) + 1
    return {
        "messages": total,
        "roles": role_counts,
        "sources": source_counts,
        "user_targets": user_targets,
    }


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _rebuild_shared(store: ConversationStore, cid: str) -> list[dict]:
    transcript = store.load(cid) or []
    candidates = store.filter_for_shared(transcript)
    return [store._transform_for_shared(msg) for msg in candidates]


def _agents_from_args(store: ConversationStore, cid: str, args) -> list[str]:
    if args.all_agents:
        contexts = store.list_agent_contexts(cid)
        agents = [name for name in contexts if name and name != "shared"]
    else:
        agents = list(args.agent or [])
    return sorted(dict.fromkeys(ConversationStore._canon_agent(a) for a in agents if a))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("conversation_id")
    parser.add_argument("--agent", action="append", default=[],
                        help="Agent context to rebuild. Repeatable.")
    parser.add_argument("--all-agents", action="store_true",
                        help="Rebuild every existing agent context.")
    parser.add_argument("--shared", action="store_true",
                        help="Rebuild shared.jsonl from transcript.")
    parser.add_argument("--apply", action="store_true",
                        help="Write repaired files. Default is dry-run.")
    args = parser.parse_args()

    if not args.shared and not args.agent and not args.all_agents:
        parser.error("choose --shared, --agent NAME, or --all-agents")

    store = ConversationStore.instance()
    cid = args.conversation_id
    if not store.exists(cid):
        raise SystemExit(f"conversation not found: {cid}")

    print(f"conversation={cid} mode={'apply' if args.apply else 'dry-run'}")

    if args.shared:
        before = store.load_agent_context(cid, "") or []
        rebuilt = _rebuild_shared(store, cid)
        print("\n[shared]")
        print("before", _counts(before))
        print("after ", _counts(rebuilt))
        if args.apply:
            backup = _backup(store._shared_ctx_path(cid))
            if backup:
                print(f"backup {backup}")
            store.save_agent_context(cid, "", rebuilt)
            print("wrote shared.jsonl")

    for agent in _agents_from_args(store, cid, args):
        before = store.load_agent_context(cid, agent) or []
        rebuilt = store.load_transcript_for_agent(cid, agent) or []
        print(f"\n[agent:{agent}]")
        print("before", _counts(before))
        print("after ", _counts(rebuilt))
        if args.apply:
            backup = _backup(store._agent_ctx_path(cid, agent))
            if backup:
                print(f"backup {backup}")
            store.save_agent_context(cid, agent, rebuilt)
            print(f"wrote {agent}/context.jsonl")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
