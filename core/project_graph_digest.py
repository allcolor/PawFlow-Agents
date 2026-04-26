"""Project graph digest — compact summary for system prompt injection.

Gives the agent a passive view of the codebase structure (when a
project_graph has been built for the conv) without forcing a
project_graph(action='report') call. Only fires when has_graph() is
true — silent for conversations that haven't built one.
"""

import logging
from collections import Counter
from typing import List

logger = logging.getLogger(__name__)


def build_project_graph_digest(user_id: str, conv_id: str,
                                 max_chars: int = 400,
                                 top_god: int = 5) -> str:
    """Build a compact project-graph summary for system-prompt injection.

    Returns "" when no graph has been built for this conv. The agent
    learns that a graph exists (and what's notable in it) so it can
    decide whether to dive in via project_graph(action='query'/'node')
    instead of re-building from scratch.
    """
    if not user_id or not conv_id:
        return ""
    try:
        from core.project_graph import ProjectGraph
        pg = ProjectGraph.for_conversation(user_id, conv_id)
    except Exception:
        logger.debug("[pg-digest] load failed", exc_info=True)
        return ""

    if not pg.has_graph():
        return ""

    nodes = pg.nodes
    edges = pg.edges
    if not nodes:
        return ""

    # God nodes — same logic as ProjectGraph.get_report but capped at
    # top_god and rendered as a single line.
    degree: dict = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    god_pairs = sorted(degree.items(), key=lambda x: -x[1])[:top_god]
    label_by_id = {n["id"]: n.get("label", n["id"]) for n in nodes}

    # Languages distribution from node metadata when available.
    langs = Counter()
    for n in nodes:
        lang = n.get("language") or n.get("lang") or ""
        if lang:
            langs[lang] += 1
    lang_summary = ", ".join(f"{k} ({v})"
                              for k, v in langs.most_common(5)) or "unknown"

    lines: List[str] = [
        f"Codebase indexed: {len(nodes)} entities, {len(edges)} edges. "
        f"Languages: {lang_summary}."
    ]
    if god_pairs:
        lines.append(
            "God nodes: "
            + ", ".join(f"{label_by_id.get(nid, nid)} ({d})"
                         for nid, d in god_pairs))

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars - 3] + "..."
    return digest
