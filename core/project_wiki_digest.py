"""Compact relay-project wiki state for prompt injection."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def build_project_wiki_digest(user_id: str, relay_id: str,
                              max_chars: int = 700) -> str:
    """Return state plus actionable usage hints for the active relay wiki."""
    if not user_id or not relay_id:
        return ""
    try:
        from core.project_wiki import ProjectWiki
        status = ProjectWiki.for_relay(user_id, relay_id).status()
    except Exception:
        logger.debug("[project-wiki-digest] load failed", exc_info=True)
        return ""
    if not status["initialized"]:
        return (
            f"Project wiki for relay `{relay_id}` is not initialized yet; "
            "automatic background construction has been scheduled."
        )[:max_chars]
    stale = len(status["stale_pages"])
    lines = [
        f"Project wiki for relay `{relay_id}`: {status['pages']} pages, "
        f"{status['sources']} indexed sources, "
        f"{status['dirty_sources']} pending, {stale} stale pages.",
        "Use `project_wiki(action='query')` before broad architectural "
        "exploration; validate stale claims against live source files.",
    ]
    if status.get("scan", {}).get("truncated"):
        lines.append("Warning: the last source scan hit its file limit.")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 3] + "..."
