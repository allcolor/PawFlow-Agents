"""Memory digest — compact summary of critical memories for system prompt injection.

Builds a short text block from L0 (identity/profile) and L1 (facts/preferences)
memories, suitable for prepending to the system prompt so the LLM has persistent
context without needing to call recall() first.
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


def build_memory_digest(user_id: str, agent_name: str = "",
                        max_chars: int = 1200) -> str:
    """Build compact digest of critical memories for system prompt injection.

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

    if not lines:
        return ""

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars - 3] + "..."
    return digest
