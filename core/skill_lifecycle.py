"""Shared lifecycle helpers for Agent Skill resources."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

# Serializes read-modify-write of instance assigned_skills across concurrent
# assign/unassign/delete/update paths so a concurrent update cannot drop an entry.
ASSIGNED_SKILLS_LOCK = threading.Lock()


def _agent_instance(agent_name: str, conversation_id: str):
    from core.conv_agent_config import resolve_agent_config_entry
    return resolve_agent_config_entry(conversation_id, agent_name)


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
    if not conversation_id:
        return {"ok": False, "error": "Missing conversation_id"}
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None and conversation_id and notify:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import available_skill_context_message, normalize_skill_entry

    config_conv_id, resolved_name, agent_config = _agent_instance(
        agent_name, conversation_id)
    if not resolved_name:
        return {"ok": False, "error": f"Agent '{agent_name}' not found in conversation"}
    def_name = agent_config.get("definition") or ""
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
        from core.conv_agent_config import get_agent_config, set_agent_config
        fresh = get_agent_config(config_conv_id, resolved_name)
        assigned = list(fresh.get("assigned_skills", []) or [])
        changed = not any(
            normalize_skill_entry(entry)[0] == skill_name for entry in assigned)
        if changed:
            assigned.append(skill_name)
        set_agent_config(
            config_conv_id, resolved_name, {"assigned_skills": assigned})
    if changed and notify and conversation_id and conversation_store is not None:
        _append_agent_context(
            conversation_store, conversation_id, user_id, agent_name,
            available_skill_context_message(skill_name, skill_def), source)
    return {
        "ok": True,
        "assigned": True,
        "changed": changed,
        "agent": resolved_name,
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
    if not conversation_id:
        return {"ok": False, "error": "Missing conversation_id"}
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if conversation_store is None and conversation_id and notify:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    from core.skill_resolver import normalize_skill_entry, removed_skill_context_message

    config_conv_id, resolved_name, agent_config = _agent_instance(
        agent_name, conversation_id)
    if not resolved_name:
        return {"ok": False, "error": f"Agent '{agent_name}' not found in conversation"}
    def_name = agent_config.get("definition") or ""
    agent_def = resource_store.get_any(
        "agent", def_name, user_id, conversation_id=conversation_id)
    if not agent_def:
        return {"ok": False, "error": f"Agent '{agent_name}' not found"}
    with ASSIGNED_SKILLS_LOCK:
        from core.conv_agent_config import get_agent_config, set_agent_config
        fresh = get_agent_config(config_conv_id, resolved_name)
        assigned = list(fresh.get("assigned_skills", []) or [])
        kept = []
        changed = False
        for entry in assigned:
            if normalize_skill_entry(entry)[0] == skill_name:
                changed = True
                continue
            kept.append(entry)
        set_agent_config(
            config_conv_id, resolved_name, {"assigned_skills": kept})
    if changed and notify and conversation_id and conversation_store is not None:
        _append_agent_context(
            conversation_store, conversation_id, user_id, agent_name,
            removed_skill_context_message(skill_name), source)
    return {
        "ok": True,
        "unassigned": True,
        "changed": changed,
        "agent": resolved_name,
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

    if not conversation_id:
        cleaned_agents: List[str] = []
        for agent_def in resource_store.list_all("agent", user_id):
            agent_name = agent_def.get("name", "")
            if not agent_name:
                continue
            with ASSIGNED_SKILLS_LOCK:
                fresh = resource_store.get_any(
                    "agent", agent_name, user_id) or agent_def
                kept = []
                changed = False
                for entry in list(fresh.get("assigned_skills", []) or []):
                    name, _params, _condition = normalize_skill_entry(entry)
                    if name == skill_name:
                        changed = True
                        continue
                    kept.append(entry)
                if not changed:
                    continue
                scope = fresh.get("_scope", "user")
                update_uid = (
                    user_id if scope == "user" else "__global__")
                resource_store.update(
                    "agent", agent_name, update_uid,
                    {"assigned_skills": kept})
            cleaned_agents.append(agent_name)
        return cleaned_agents
    from core.conv_agent_config import (
        get_all_agent_configs, resolve_agent_config_entry, set_agent_config,
    )
    cleaned_agents: List[str] = []
    for agent_name in get_all_agent_configs(conversation_id):
        if not agent_name:
            continue
        changed = False
        with ASSIGNED_SKILLS_LOCK:
            config_conv_id, resolved_name, fresh = resolve_agent_config_entry(
                conversation_id, agent_name)
            kept = []
            for entry in list(fresh.get("assigned_skills", []) or []):
                name, _params, _condition = normalize_skill_entry(entry)
                if name == skill_name:
                    changed = True
                    continue
                kept.append(entry)
            if changed:
                set_agent_config(
                    config_conv_id, resolved_name,
                    {"assigned_skills": kept})
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

    from core.conv_agent_config import get_all_agent_configs
    notified: List[str] = []
    for agent_name, agent_config in get_all_agent_configs(
            conversation_id).items():
        assigned = False
        for entry in list(agent_config.get("assigned_skills", []) or []):
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
