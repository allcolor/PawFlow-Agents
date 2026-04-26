"""Knowledge graph digest — compact summary for system prompt injection.

Gives the agent a passive view of the KG without forcing a kg_query
call. Mirrors the role of memory_digest for MemoryStore: top god
nodes (most connected entities) plus the most recent currently-valid
facts. Omits expired triples and confidence-AMBIGUOUS entries to
keep the digest signal/noise high.
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


def build_kg_digest(user_id: str, max_chars: int = 600,
                     top_god: int = 5, recent_limit: int = 6) -> str:
    """Build a compact KG summary for system-prompt injection.

    Returns "" when the graph is empty or when top god nodes have
    fewer than 2 connections (no signal yet). A short digest is
    better than no digest — we want the agent to know the KG exists
    and what's in it without spending tokens.
    """
    if not user_id:
        return ""
    try:
        from core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph.for_user(user_id)
    except Exception:
        logger.debug("[kg-digest] load failed", exc_info=True)
        return ""

    try:
        s = kg.stats()
    except Exception:
        return ""
    if not s or s.get("current_facts", 0) == 0:
        return ""

    lines: List[str] = []

    # Top connected entities. Filter <2 connections — a node with one
    # link is just a leaf, not a signal.
    try:
        gods = [g for g in kg.god_nodes(limit=top_god)
                if g.get("connections", 0) >= 2]
    except Exception:
        gods = []
    if gods:
        lines.append("Most connected: "
                     + ", ".join(f"{g['entity']} ({g['connections']})"
                                  for g in gods))

    # Recent currently-valid facts — timeline filtered to AMBIGUOUS-
    # excluded current entries.
    try:
        tl = kg.timeline(limit=recent_limit * 3)
    except Exception:
        tl = []
    recent = [t for t in tl
              if t.get("current")
              and t.get("confidence", "") != "AMBIGUOUS"][:recent_limit]
    if recent:
        lines.append("Recent facts: "
                     + "; ".join(f"{t['subject']} {t['predicate']} {t['object']}"
                                  for t in recent))

    if not lines:
        return ""
    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars - 3] + "..."
    return digest
