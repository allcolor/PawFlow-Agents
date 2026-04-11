"""Conversation-level agent configuration.

When agents are added to a conversation, each gets runtime parameters:
    llm_service, model, tools, max_depth, timeout, skills

These are stored in ConversationStore extras under "conv_agents".
The agent definition (repository .md file) provides only the system prompt
and description. Everything else is runtime.

Usage:
    from core.conv_agent_config import get_agent_config, set_agent_config

    cfg = get_agent_config(conv_id, "claude")
    # → {"llm_service": "claude_code_llm_service", "model": "", ...}
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONV_AGENTS_KEY = "conv_agents"

# Defaults for runtime agent config
AGENT_CONFIG_DEFAULTS = {
    "llm_service": "",
    "model": "",
    "tools": [],
    "max_depth": 5,
    "timeout": 180,
    "skills": [],
}


def get_all_agent_configs(conv_id: str) -> Dict[str, Dict[str, Any]]:
    """Get all agent configs for a conversation."""
    from core.conversation_store import ConversationStore
    return ConversationStore.instance().get_extra(
        conv_id, CONV_AGENTS_KEY) or {}


def get_agent_config(conv_id: str, agent_name: str) -> Dict[str, Any]:
    """Get runtime config for an agent in a conversation.

    Returns config dict with all fields guaranteed present (defaults applied).
    Returns defaults if agent not found (graceful fallback).
    """
    configs = get_all_agent_configs(conv_id)
    raw = configs.get(agent_name, {})
    result = dict(AGENT_CONFIG_DEFAULTS)
    result.update(raw)
    return result


def set_agent_config(conv_id: str, agent_name: str,
                     config: Dict[str, Any]) -> None:
    """Set or update runtime config for an agent in a conversation."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    configs = store.get_extra(conv_id, CONV_AGENTS_KEY) or {}
    existing = configs.get(agent_name, {})
    existing.update(config)
    configs[agent_name] = existing
    store.set_extra(conv_id, CONV_AGENTS_KEY, configs)


def remove_agent_config(conv_id: str, agent_name: str) -> None:
    """Remove an agent's runtime config from a conversation."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    configs = store.get_extra(conv_id, CONV_AGENTS_KEY) or {}
    configs.pop(agent_name, None)
    store.set_extra(conv_id, CONV_AGENTS_KEY, configs)


def add_agent_to_conv(conv_id: str, agent_name: str,
                      llm_service: str = "",
                      model: str = "",
                      tools: Optional[List[str]] = None,
                      max_depth: int = 5,
                      timeout: int = 180,
                      skills: Optional[List[str]] = None) -> Dict[str, Any]:
    """Add an agent to a conversation with runtime config.

    If llm_service is empty, tries to find {agent_name}_llm_service
    in the service registry.
    """
    if not llm_service:
        llm_service = _guess_llm_service(agent_name, conv_id)

    config = {
        "llm_service": llm_service,
        "model": model,
        "tools": tools or [],
        "max_depth": max_depth,
        "timeout": timeout,
        "skills": skills or [],
    }
    set_agent_config(conv_id, agent_name, config)
    return config


def _guess_llm_service(agent_name: str, conv_id: str = "") -> str:
    """Try to find a matching LLM service for an agent name.

    Looks for services named:
      {agent_name}_llm_service → exact match
      Otherwise → first enabled llmConnection service
    """
    try:
        from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
        reg = ServiceRegistry.get_instance()
        # Try {agent_name}_llm_service
        candidate = f"{agent_name}_llm_service"
        sdef = reg.get_definition(SCOPE_GLOBAL, "", candidate)
        if sdef and sdef.enabled:
            return candidate
        # Try {agent_name}_llm
        candidate = f"{agent_name}_llm"
        sdef = reg.get_definition(SCOPE_GLOBAL, "", candidate)
        if sdef and sdef.enabled:
            return candidate
        # Fallback: first llmConnection service
        all_llm = reg.resolve_by_type("llmConnection")
        if all_llm:
            return all_llm[0].service_id
    except Exception:
        pass
    return ""
