"""Resolve and schedule the relay project attached to an agent turn."""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple


logger = logging.getLogger(__name__)

PROJECT_GRAPH_USAGE_HINT = (
    "\n\n**Reach for `project_graph` BEFORE read/grep when:**"
    "\n- User mentions a function/class/module by name"
    " → `project_graph(action='node', question='X')` for location + neighbours."
    "\n- 'where is X used', 'what calls Y', 'what depends on Z'"
    " → `project_graph(action='query', question='X')` returns AST call sites"
    " (no false matches in comments/strings)."
    "\n- Refactor/rename touching a public API → query first to scope blast radius."
    "\n- Onboarding to an unfamiliar area → `action='report'` for god nodes + stats."
    "\n**Skip it for:** single-file edit you already have open, text/comment search,"
    " non-code files (md/json/yaml), scopes <5 files, or when the graph is stale."
)


def resolve_active_project(user_id: str, conversation_id: str,
                           agent_name: str = "") -> Tuple[str, Any, bool]:
    """Return `(relay_id, live_service, local)` or empty values."""
    if not user_id or not conversation_id:
        return "", None, False
    try:
        from core.relay_bindings import get_default, get_default_local, get_linked
        relay_id = get_default(conversation_id, agent=agent_name) or ""
        if not relay_id:
            linked = get_linked(conversation_id, agent=agent_name)
            if len(linked) == 1:
                relay_id = linked[0]
        if not relay_id:
            return "", None, False
        from core.service_registry import ServiceRegistry
        service = ServiceRegistry.get_instance().resolve(
            relay_id, user_id=user_id, conv_id=conversation_id)
        if service is None:
            return relay_id, None, False
        local = get_default_local(
            conversation_id, relay_id=relay_id, agent=agent_name)
        return relay_id, service, bool(local)
    except Exception:
        logger.debug("Failed to resolve active relay project", exc_info=True)
        return "", None, False


def schedule_active_project(user_id: str, conversation_id: str,
                            agent_name: str = "") -> str:
    """Schedule automatic maintenance and return the active relay ID."""
    relay_id, service, local = resolve_active_project(
        user_id, conversation_id, agent_name)
    if relay_id and service is not None:
        from core.project_maintenance import schedule_project_maintenance
        schedule_project_maintenance(
            user_id=user_id, relay_id=relay_id, service=service,
            conversation_id=conversation_id, agent_name=agent_name,
            local=local)
    return relay_id


def prepare_active_project_context(user_id: str, conversation_id: str,
                                   agent_name: str = ""
                                   ) -> Tuple[str, str, str]:
    """Schedule maintenance and return relay, graph and wiki prompt digests."""
    relay_id = schedule_active_project(user_id, conversation_id, agent_name)
    graph_digest = ""
    wiki_digest = ""
    if relay_id:
        try:
            from core.project_graph_digest import build_project_graph_digest
            graph_digest = build_project_graph_digest(user_id, relay_id)
        except Exception:
            logger.debug("Failed to build project graph context", exc_info=True)
        try:
            from core.project_wiki_digest import build_project_wiki_digest
            wiki_digest = build_project_wiki_digest(user_id, relay_id)
        except Exception:
            logger.debug("Failed to build project wiki context", exc_info=True)
    return relay_id, graph_digest, wiki_digest
