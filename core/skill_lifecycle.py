"""Shared lifecycle helpers for Agent Skill resources."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

# Serializes read-modify-write of agent.assigned_skills across concurrent
# assign/unassign/delete/update paths so a concurrent update cannot drop an entry.
ASSIGNED_SKILLS_LOCK = threading.Lock()


def _agent_definition_name(agent_name: str, conversation_id: str) -> str:
    if not conversation_id:
        return agent_name
    try:
        from core.conv_agent_config import get_agent_config
        return get_agent_config(conversation_id, agent_name).get("definition") or agent_name
    except Exception:
        return agent_name


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


def assign_skill_to_agent(agent_name: str, skill_name: str, user_id: str,
                          conversation_id: str = "", *,
                          resource_store=None,
                          conversation_store=None,
                          notify: bool = True,
                          source: str = "skill_assign") -> Dict[str, Any]:
    """Assign a visible skill to an agent and optionally notify the agent."""
    agent_name = str(agent_name or "").strip()
    skill_name = str(skill_name or "").strip()
    if not agent_name or not skill_name:
        return {"ok": False, "error": "Missing agent_name or skill_name"}
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None and conversation_id and notify:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import available_skill_context_message, normalize_skill_entry

    def_name = _agent_definition_name(agent_name, conversation_id)
    agent_def = resource_store.get_any(
        "agent", def_name, user_id, conversation_id=conversation_id)
    if not agent_def:
        return {"ok": False, "error": f"Agent '{agent_name}' not found"}
    skill_def = resource_store.get_any(
        "skill", skill_name, user_id, conversation_id=conversation_id)
    if not skill_def:
        return {"ok": False, "error": f"Skill '{skill_name}' not found"}
    if skill_def.get("_invalid"):
        return {"ok": False, "error": f"Skill '{skill_name}' is invalid: {skill_def.get('_invalid')}"}

    with ASSIGNED_SKILLS_LOCK:
        fresh = resource_store.get_any(
            "agent", def_name, user_id, conversation_id=conversation_id) or agent_def
        assigned = list(fresh.get("assigned_skills", []) or [])
        changed = not any(
            normalize_skill_entry(entry)[0] == skill_name for entry in assigned)
        if changed:
            assigned.append(skill_name)
        update_uid, update_kwargs = _agent_update_target(
            fresh, user_id, conversation_id)
        resource_store.update(
            "agent", def_name, update_uid,
            {"assigned_skills": assigned}, **update_kwargs)
    if changed and notify and conversation_id and conversation_store is not None:
        _append_agent_context(
            conversation_store, conversation_id, user_id, agent_name,
            available_skill_context_message(skill_name, skill_def), source)
    return {
        "ok": True,
        "assigned": True,
        "changed": changed,
        "agent": agent_name,
        "skill": skill_name,
    }


def unassign_skill_from_agent(agent_name: str, skill_name: str, user_id: str,
                              conversation_id: str = "", *,
                              resource_store=None,
                              conversation_store=None,
                              notify: bool = True,
                              source: str = "skill_unassign") -> Dict[str, Any]:
    """Remove a skill assignment from an agent and optionally notify it."""
    agent_name = str(agent_name or "").strip()
    skill_name = str(skill_name or "").strip()
    if not agent_name or not skill_name:
        return {"ok": False, "error": "Missing agent_name or skill_name"}
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None and conversation_id and notify:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import normalize_skill_entry, removed_skill_context_message

    def_name = _agent_definition_name(agent_name, conversation_id)
    agent_def = resource_store.get_any(
        "agent", def_name, user_id, conversation_id=conversation_id)
    if not agent_def:
        return {"ok": False, "error": f"Agent '{agent_name}' not found"}
    with ASSIGNED_SKILLS_LOCK:
        fresh = resource_store.get_any(
            "agent", def_name, user_id, conversation_id=conversation_id) or agent_def
        assigned = list(fresh.get("assigned_skills", []) or [])
        kept = []
        changed = False
        for entry in assigned:
            if normalize_skill_entry(entry)[0] == skill_name:
                changed = True
                continue
            kept.append(entry)
        update_uid, update_kwargs = _agent_update_target(
            fresh, user_id, conversation_id)
        resource_store.update(
            "agent", def_name, update_uid,
            {"assigned_skills": kept}, **update_kwargs)
    if changed and notify and conversation_id and conversation_store is not None:
        _append_agent_context(
            conversation_store, conversation_id, user_id, agent_name,
            removed_skill_context_message(skill_name), source)
    return {
        "ok": True,
        "unassigned": True,
        "changed": changed,
        "agent": agent_name,
        "skill": skill_name,
    }


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

