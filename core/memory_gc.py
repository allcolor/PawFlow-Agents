"""Memory garbage collection for auto-extracted compaction memories.

The GC is conservative: it marks stale entries as ended instead of hard
removing them. Ended memories are ignored by recall/digests, while the raw
JSON still keeps an audit trail.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from core.memory_store import MemoryEntry

_VOLATILE_RE = re.compile(
    r"(?i)\b(current|latest|recent|in[- ]flight|actionable|validation passed|"
    r"tests? passed|after compact|post-compact|line \d+|around line|"
    r"commit [0-9a-f]{7,}|work centers on|was traced to|right now|today)\b"
)

_DURABLE_TAGS = {
    "decision", "preference", "preferences", "project_rules", "architecture",
    "security", "auth", "relay", "compact", "context", "testing",
}

_MAX_GLOBAL_COMPACTION = 300
_MAX_FAMILY_KEEP = 3


def memory_gc_plan(entries: Iterable[MemoryEntry], *, now: float = 0) -> Dict[str, Any]:
    """Return a deterministic cleanup plan for memory entries.

    Plan fields:
    - end_ids: memories to mark ended
    - reasons: memory id -> reason
    - stats: counters useful for dry-run output
    """
    now = now or time.time()
    all_entries = list(entries)
    active = [e for e in all_entries if not e.ended]
    end_ids: set[str] = set()
    reasons: Dict[str, str] = {}

    def mark(e: MemoryEntry, reason: str) -> None:
        if e.id in end_ids:
            return
        end_ids.add(e.id)
        reasons[e.id] = reason
    mark.end_ids = end_ids

    compaction = [_e for _e in active if _is_auto_compaction(_e)]

    for e in compaction:
        if _VOLATILE_RE.search(e.text or ""):
            mark(e, "volatile-compaction")

    _mark_duplicate_groups(compaction, mark)
    _mark_family_overflow(compaction, mark)
    _mark_global_overflow(compaction, mark)

    stats = Counter(reasons.values())
    stats.update({
        "total": len(all_entries),
        "active": len(active),
        "auto_compaction": len(compaction),
        "to_end": len(end_ids),
    })
    return {
        "end_ids": sorted(end_ids),
        "reasons": reasons,
        "stats": dict(stats),
    }


def apply_memory_gc(user_id: str, *, dry_run: bool = True, now: float = 0) -> Dict[str, Any]:
    """Plan or apply GC for one user in MemoryStore."""
    from core.memory_store import MemoryStore

    store = MemoryStore.instance()
    with store._store_lock:
        store._ensure_loaded(user_id)
        entries = list(store._memories.get(user_id, []))
        plan = memory_gc_plan(entries, now=now)
        if not dry_run:
            end_ids = set(plan["end_ids"])
            ended_at = now or time.time()
            changed = 0
            for e in store._memories.get(user_id, []):
                if e.id in end_ids and not e.ended:
                    e.ended = ended_at
                    e.updated_at = ended_at
                    changed += 1
            if changed:
                store._save_user(user_id)
            plan["applied"] = changed
        else:
            plan["applied"] = 0
        return plan


def _is_auto_compaction(e: MemoryEntry) -> bool:
    return e.source == "compaction" or (
        "auto-extracted" in e.tags and "compaction" in e.tags)


def _norm(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"\b\d+\s*(minutes?|hours?|days?|msgs?|messages?)\b", "<num>", text)
    text = re.sub(r"[^a-z0-9_:/@.-]+", " ", text)
    return " ".join(text.split())


def _family_key(text: str) -> str:
    n = _norm(text)
    if n.startswith("user communicates"):
        return "user:communication-style"
    if n.startswith("user expects"):
        return "user:expectations"
    if n.startswith("user prefers") or n.startswith("user strongly prefers"):
        return "user:preferences"
    if "commit" in n and "push" in n:
        return "workflow:commit-push"
    if "compact" in n and ("context" in n or "session" in n):
        return "pawflow:compact-context"
    if "toolcall" in n or "tool call" in n:
        return "pawflow:toolcalls"
    if "thinking" in n and "display" in n:
        return "pawflow:thinking-display"
    return n[:96]


def _rank_entry(e: MemoryEntry) -> Tuple[int, float]:
    durable = 1 if set(e.tags) & _DURABLE_TAGS else 0
    user_pref = 1 if e.category in {"preferences", "advice"} else 0
    return durable + user_pref, e.updated_at or e.created_at or 0


def _mark_duplicate_groups(entries: List[MemoryEntry], mark) -> None:
    groups: Dict[Tuple[str, str, str, str], List[MemoryEntry]] = defaultdict(list)
    for e in entries:
        key = (_norm(e.text), e.agent or "", e.conversation_id or "", e.category or "")
        groups[key].append(e)
    for group in groups.values():
        if len(group) <= 1:
            continue
        keep = max(group, key=_rank_entry)
        for e in group:
            if e.id != keep.id:
                mark(e, "duplicate")


def _mark_family_overflow(entries: List[MemoryEntry], mark) -> None:
    groups: Dict[str, List[MemoryEntry]] = defaultdict(list)
    for e in entries:
        groups[_family_key(e.text)].append(e)
    for group in groups.values():
        if len(group) <= _MAX_FAMILY_KEEP:
            continue
        ranked = sorted(group, key=_rank_entry, reverse=True)
        for e in ranked[_MAX_FAMILY_KEEP:]:
            mark(e, "family-overflow")


def _mark_global_overflow(entries: List[MemoryEntry], mark) -> None:
    global_entries = [e for e in entries if not e.agent and not e.conversation_id]
    survivors = [e for e in global_entries if e.id not in getattr(mark, "end_ids", set())]
    if len(survivors) <= _MAX_GLOBAL_COMPACTION:
        return
    ranked = sorted(survivors, key=_rank_entry, reverse=True)
    keep_ids = {e.id for e in ranked[:_MAX_GLOBAL_COMPACTION]}
    for e in survivors:
        if e.id not in keep_ids:
            mark(e, "global-auto-quota")
