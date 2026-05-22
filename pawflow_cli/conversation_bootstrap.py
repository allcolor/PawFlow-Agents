"""Helpers for creating or resuming PawCode conversations."""


def guess_llm_service(agent_name: str, services: list) -> str:
    """Pick the same preferred LLM service naming convention as webchat."""
    enabled = [s for s in services if s.get("enabled", True)]
    names = [s.get("service_id", "") for s in enabled]
    for suffix in ("_llm_service", "_llm"):
        candidate = f"{agent_name}{suffix}"
        if candidate in names:
            return candidate
    return names[0] if names else ""


def active_agent_for(api, conversation_id: str) -> str:
    """Load the server-selected active agent for a conversation."""
    if not conversation_id:
        return ""
    data = api.send_action("load_history", conversation_id=conversation_id,
                           limit=1, offset=0)
    if data.get("error"):
        return ""
    return data.get("active_agent", "") or ""


def create_conversation(api, requested_agent: str = "", llm_service: str = "",
                        relays: list | None = None, title: str = ""):
    """Create a conversation with one validated agent and LLM service."""
    relays = relays or []
    repo_data = api.send_action("list_repo_agents", conversation_id="")
    repo_agents = repo_data.get("agents", [])
    if not repo_agents:
        raise ValueError("No agent definitions available. Create an agent in webchat first.")

    requested = (requested_agent or "").lstrip("@")
    agent_def = None
    if requested:
        for item in repo_agents:
            if item.get("name", "").lower() == requested.lower():
                agent_def = item
                break
        if not agent_def:
            raise ValueError(f"Agent definition not found: {requested}")
    else:
        agent_def = repo_agents[0]

    agent_name = agent_def.get("name", "")
    svc_data = api.send_action("list_services", service_type="llmConnection",
                               conversation_id="")
    enabled_services = [s for s in svc_data.get("services", []) if s.get("enabled", True)]
    service_ids = {s.get("service_id", "") for s in enabled_services}
    resolved_llm = llm_service or guess_llm_service(agent_name, enabled_services)
    if not resolved_llm:
        raise ValueError("No enabled LLM service available. Configure one in webchat first.")
    if resolved_llm not in service_ids:
        raise ValueError(f"LLM service not found or disabled: {resolved_llm}")

    valid_relays = []
    if relays:
        relay_data = api.send_action("relay_list_available")
        available = {
            r.get("relay_id", "") for r in relay_data.get("relays", [])
            if r.get("connected", True)
        }
        invalid = [rid for rid in relays if rid not in available]
        if invalid:
            raise ValueError(f"Relay not found or disconnected: {', '.join(invalid)}")
        valid_relays = list(relays)

    payload = {
        "agents": [{
            "instance_name": agent_name,
            "definition": agent_name,
            "llm_service": resolved_llm,
            "params": {"name": agent_name},
        }],
    }
    if title:
        payload["title"] = title
    if valid_relays:
        payload["relays"] = valid_relays
        payload["default_relay"] = valid_relays[0]

    data = api.send_action("create_conversation", **payload)
    if data.get("error"):
        raise ValueError(data["error"])
    cid = data.get("conversation_id", "")
    if not cid:
        raise ValueError("Conversation creation returned no conversation_id")
    return cid, agent_name, resolved_llm, payload


def ensure_conversation_and_agent(api, conversation_id: str = "",
                                  requested_agent: str = ""):
    """Return a usable (conversation_id, target_agent), creating one if needed."""
    if conversation_id:
        agent = active_agent_for(api, conversation_id)
        if agent:
            return conversation_id, agent
        if requested_agent:
            return conversation_id, requested_agent.lstrip("@")
        raise ValueError(f"No active agent found for conversation {conversation_id[:8]}")
    cid, agent, _llm, _payload = create_conversation(api, requested_agent=requested_agent)
    return cid, agent
