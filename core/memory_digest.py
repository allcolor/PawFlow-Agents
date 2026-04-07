"""Memory digest — compact summary of critical memories for system prompt injection.

Builds a multi-tier text block (L0-L4) from the memory palace structure:
  L0: Identity/profile
  L1: Key facts + preferences
  L2: Recent events
  L3: Active decisions
  L4: Discoveries/learnings
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


def build_memory_digest(user_id: str, agent_name: str = "",
                        max_chars: int = 1200) -> str:
    """Build compact multi-tier digest of critical memories.

    Returns "" if no relevant memories exist.
    """
    from core.memory_store import MemoryStore
    ms = MemoryStore.instance()

    lines: List[str] = []

    # L0: identity/profile
    identity = ms.recall(user_id, tags=["identity", "profile"], limit=3,
                         agent_name=agent_name)
    if identity:
        lines.append("Identity: " + "; ".join(e.text[:150] for e in identity))

    # L1: critical facts
    facts = ms.recall(user_id, hall="facts", limit=5, agent_name=agent_name)
    if facts:
        lines.append("Key facts: " + "; ".join(e.text[:150] for e in facts))

    # L1: preferences
    prefs = ms.recall(user_id, hall="preferences", limit=3, agent_name=agent_name)
    if prefs:
        lines.append("Preferences: " + "; ".join(e.text[:150] for e in prefs))

    # L2: recent events (sorted by date, most recent first)
    events = ms.recall(user_id, hall="events", limit=3, agent_name=agent_name)
    if events:
        events.sort(key=lambda e: e.created_at, reverse=True)
        lines.append("Recent events: " + "; ".join(e.text[:120] for e in events))

    # L3: active decisions (facts tagged with "decision")
    decisions = ms.recall(user_id, tags=["decision"], hall="facts", limit=3,
                          agent_name=agent_name)
    if decisions:
        lines.append("Active decisions: " + "; ".join(e.text[:120] for e in decisions))

    # L4: discoveries/learnings
    discoveries = ms.recall(user_id, hall="discoveries", limit=3,
                            agent_name=agent_name)
    if discoveries:
        lines.append("Discoveries: " + "; ".join(e.text[:120] for e in discoveries))

    # L4: advice
    advice = ms.recall(user_id, hall="advice", limit=2, agent_name=agent_name)
    if advice:
        lines.append("Advice: " + "; ".join(e.text[:120] for e in advice))

    if not lines:
        return ""

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars - 3] + "..."
    return digest
