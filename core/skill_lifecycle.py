"""Shared lifecycle helpers for Agent Skill resources."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

# Serializes read-modify-write of agent.assigned_skills across concurrent
# assign/unassign/delete/update paths so a concurrent update cannot drop an entry.
ASSIGNED_SKILLS_LOCK = threading.Lock()


def _agent_update_target(agent_def: Dict[str, Any], user_id: str,
                         conversation_id: str) -> tuple[str, Dict[str, str]]:
    scope = agent_def.get("_scope", "user")
    update_uid = user_id if scope in ("conversation", "user") else "__global__"
    update_kwargs = {"conversation_id": conversation_id} if scope == "conversation" and conversation_id else {}
    return update_uid, update_kwargs


def _append_agent_context(conversation_store, conversation_id: str, user_id: str,
                          agent_name: str, content: str, source: str) -> None:
    if not conversation_id:
        return
    try:
        from core.llm_client import stamp_message
        from core.pending_queue import PendingQueue
        msg = stamp_message({
            "role": "system",
            "content": content,
            "source": {"type": "context", "name": "pawflow"},
        }, conversation_id)
        conversation_store.append_message(
            conversation_id, msg, agent_name=agent_name, user_id=user_id)
        PendingQueue.for_agent(conversation_id, agent_name).enqueue(
            dict(msg), source=source)
    except Exception:
        logger.debug("skill context injection failed", exc_info=True)


def remove_skill_assignments(skill_name: str, user_id: str,
                             conversation_id: str = "", *,
                             resource_store=None,
                             conversation_store=None,
                             notify: bool = True,
                             source: str = "skill_delete") -> List[str]:
    """Remove a skill from every visible agent and optionally notify them."""
    if not skill_name:
        return []
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None and conversation_id and notify:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import normalize_skill_entry, removed_skill_context_message

    cleaned_agents: List[str] = []
    for agent_def in resource_store.list_all(
            "agent", user_id, conversation_id=conversation_id):
        agent_name = agent_def.get("name", "")
        if not agent_name:
            continue
        changed = False
        with ASSIGNED_SKILLS_LOCK:
            fresh = resource_store.get_any(
                "agent", agent_name, user_id,
                conversation_id=conversation_id) or agent_def
            kept = []
            for entry in list(fresh.get("assigned_skills", []) or []):
                name, _params, _condition = normalize_skill_entry(entry)
                if name == skill_name:
                    changed = True
                    continue
                kept.append(entry)
            if changed:
                update_uid, update_kwargs = _agent_update_target(
                    fresh, user_id, conversation_id)
                resource_store.update(
                    "agent", agent_name, update_uid,
                    {"assigned_skills": kept}, **update_kwargs)
        if not changed:
            continue
        cleaned_agents.append(agent_name)
        if notify and conversation_id and conversation_store is not None:
            _append_agent_context(
                conversation_store, conversation_id, user_id, agent_name,
                removed_skill_context_message(skill_name), source)
    return cleaned_agents


def notify_skill_updated(skill_name: str, skill_def: Optional[Dict[str, Any]],
                         user_id: str, conversation_id: str = "", *,
                         resource_store=None,
                         conversation_store=None,
                         source: str = "skill_update") -> List[str]:
    """Notify agents currently assigned to a skill that its content changed."""
    if not skill_name or not conversation_id:
        return []
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import normalize_skill_entry, updated_skill_context_message

    notified: List[str] = []
    for agent_def in resource_store.list_all(
            "agent", user_id, conversation_id=conversation_id):
        agent_name = agent_def.get("name", "")
        if not agent_name:
            continue
        assigned = False
        for entry in list(agent_def.get("assigned_skills", []) or []):
            name, _params, _condition = normalize_skill_entry(entry)
            if name == skill_name:
                assigned = True
                break
        if not assigned:
            continue
        _append_agent_context(
            conversation_store, conversation_id, user_id, agent_name,
            updated_skill_context_message(skill_name, skill_def or {}), source)
        notified.append(agent_name)
    return notified

