"""Shared conversation creation contract.

All clients that create a conversation must go through this module so a new
conversation always has validated agents, active resource metadata, title, and
optional relay bindings in the same shape.
"""

from __future__ import annotations

from typing import Any, Dict, List


def create_conversation(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    agents = payload.get("agents", [])
    if not agents or not isinstance(agents, list):
        raise ValueError("'agents' list is required")

    from core.conversation_store import ConversationStore
    from core.conv_agent_config import add_agent_to_conv
    from core.resource_store import ResourceStore

    store = ConversationStore.instance()
    rs = ResourceStore.instance()

    agent_entries: List[Dict[str, Any]] = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        instance_name = item.get("instance_name") or item.get("name", "")
        agent_entries.append({
            "instance_name": instance_name,
            "definition": item.get("definition", instance_name),
            "params": item.get("params") or {},
            "llm_service": item.get("llm_service", ""),
            "model": item.get("model", ""),
            "tools": item.get("tools"),
            "max_depth": int(item.get("max_depth", 1000)),
            "skills": item.get("skills"),
        })

    valid_entries = [
        entry for entry in agent_entries
        if rs.get_any("agent", entry["definition"], user_id)
    ]
    if not valid_entries:
        raise ValueError("None of the specified agent definitions exist in the repository")

    conversation_id = store.generate_id()
    store.save(conversation_id, [], user_id=user_id)
    instance_names = [entry["instance_name"] for entry in valid_entries]
    store.set_extra(conversation_id, "active_resources", {
        "agents": instance_names,
        "agent": instance_names[0],
    })

    for entry in valid_entries:
        if entry.get("skills") is not None:
            agent_def = rs.get_any("agent", entry["definition"], user_id)
            if agent_def is not None:
                scope = agent_def.get("_scope", "user")
                update_uid = user_id if scope == "user" else "__global__"
                rs.update("agent", entry["definition"], update_uid, {
                    "assigned_skills": list(entry.get("skills") or []),
                })
        add_agent_to_conv(
            conversation_id,
            entry["instance_name"],
            llm_service=entry["llm_service"],
            definition=entry["definition"],
            params=entry["params"],
            model=entry["model"],
            tools=entry["tools"],
            max_depth=entry["max_depth"],
            skills=entry["skills"],
        )

    title = payload.get("title", "")
    if title:
        store.set_extra(conversation_id, "title", title)

    relay_ids = payload.get("relays", [])
    default_relay = payload.get("default_relay", "")
    if relay_ids:
        from core.relay_bindings import link_relay, set_default_relay
        for relay_id in relay_ids:
            link_relay(conversation_id, relay_id, user_id=user_id)
        if default_relay and default_relay in relay_ids:
            set_default_relay(conversation_id, default_relay)

    return {"conversation_id": conversation_id, "agents": instance_names}
