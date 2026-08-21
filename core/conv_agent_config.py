"""Conversation-level agent configuration.

An agent *instance* lives in a conversation. It has:
    - instance_name: the key in conv_agents (unique per conv, chosen by user)
    - definition: the name of the .md template in the repository
    - params: dict of values resolved into the definition prompt via ${agent.key}
    - llm_service, model, tools, max_depth, timeout: runtime config
    - assigned_skills: skills available only to this conversation instance

The same definition can be instantiated multiple times with different
names and params (e.g. two "researcher" agents: Alice and Bob).

Stored in ConversationStore extras under "conv_agents".

Usage:
    from core.conv_agent_config import get_agent_config, set_agent_config

    cfg = get_agent_config(conv_id, "Alice")
    # → {"definition": "researcher", "params": {"name": "Alice", ...}, ...}
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONV_AGENTS_KEY = "conv_agents"

# Defaults for runtime agent config
AGENT_CONFIG_DEFAULTS = {
    "definition": "",
    "params": {},
    # External runtimes must never start a PawFlow LLM turn.
    "runtime_kind": "llm",
    "agui_url": "",
    "agui_service": "",
    "agui_timeout": 300,
    "agui_max_tool_rounds": 8,
    # Optional agent-level policy gate ({"scope", "service_id"} or a bare
    # service id); it can only tighten the conversation gate (core/gating_bindings).
    "gating_service": "",
    "llm_service": "",
    # Optional LLM-capable service used only by flash_delegate. This lets
    # externally-operated agents keep their non-LLM runtime service while
    # delegating flash work through a regular LLM service.
    "flash_delegate_llm_service": "",
    "model": "",
    "tools": [],
    "assigned_skills": [],
    "max_depth": 1000,
    # Optional realtimeVoiceConnection service id. When set the agent is
    # "voice-native": the webchat auto-selects it for voice mode and
    # Telegram voice notes go through a realtime speech-to-speech turn.
    "realtime_voice_service": "",
}


def _agent_config_source(
        conv_id: str) -> tuple[str, Dict[str, Dict[str, Any]]]:
    """Return the conversation that owns the effective agent roster."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    configs = store.get_extra(conv_id, CONV_AGENTS_KEY) or {}
    source_id = conv_id
    if not configs:
        from core.service_registry import _parent_conversation_id
        parent_id = _parent_conversation_id(conv_id)
        if parent_id:
            configs = store.get_extra(parent_id, CONV_AGENTS_KEY) or {}
            if configs:
                source_id = parent_id
    return source_id, configs


def get_all_agent_configs(conv_id: str) -> Dict[str, Dict[str, Any]]:
    """Get all agent configs for a conversation."""
    return _agent_config_source(conv_id)[1]


def resolve_agent_config_entry(
        conv_id: str,
        agent_name: str) -> tuple[str, str, Dict[str, Any]]:
    """Resolve an instance to its roster owner, canonical name, and config."""
    source_id, configs = _agent_config_source(conv_id)
    resolved_name = agent_name if agent_name in configs else ""
    if not resolved_name and agent_name:
        needle = agent_name.lower()
        resolved_name = next((
            key for key in configs
            if isinstance(key, str) and key.lower() == needle
        ), "")
    if not resolved_name:
        return source_id, "", {}
    result = dict(AGENT_CONFIG_DEFAULTS)
    result.update(configs[resolved_name] or {})
    return source_id, resolved_name, result


def get_agent_config(conv_id: str, agent_name: str) -> Dict[str, Any]:
    """Get runtime config for an agent instance in a conversation.

    Returns config dict with all fields guaranteed present (defaults applied).
    Returns defaults if agent not found (graceful fallback).
    Agent-name lookup is case-insensitive.
    """
    configs = get_all_agent_configs(conv_id)
    resolved_name = agent_name if agent_name in configs else ""
    if not resolved_name and agent_name:
        needle = agent_name.lower()
        resolved_name = next((
            key for key in configs
            if isinstance(key, str) and key.lower() == needle
        ), "")
    result = dict(AGENT_CONFIG_DEFAULTS)
    if resolved_name:
        result.update(configs[resolved_name] or {})
    return result


def set_agent_config(conv_id: str, agent_name: str,
                     config: Dict[str, Any]) -> None:
    """Set or update runtime config for an agent in a conversation."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    configs = store.get_extra(conv_id, CONV_AGENTS_KEY) or {}
    resolved_name = agent_name
    if agent_name not in configs and agent_name:
        needle = agent_name.lower()
        resolved_name = next((
            key for key in configs
            if isinstance(key, str) and key.lower() == needle
        ), agent_name)
    existing = configs.get(resolved_name, {})
    existing.update(config)
    configs[resolved_name] = existing
    store.set_extra(conv_id, CONV_AGENTS_KEY, configs)


def remove_agent_config(conv_id: str, agent_name: str) -> None:
    """Remove an agent's runtime config from a conversation."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    configs = store.get_extra(conv_id, CONV_AGENTS_KEY) or {}
    configs.pop(agent_name, None)
    store.set_extra(conv_id, CONV_AGENTS_KEY, configs)


def add_agent_to_conv(conv_id: str, instance_name: str,
                      llm_service: str,
                      definition: str,
                      params: Optional[Dict[str, Any]] = None,
                      model: str = "",
                      tools: Optional[List[str]] = None,
                      max_depth: int = 1000,
                      skills: Optional[List[str]] = None,
                      flash_delegate_llm_service: str = "",
                      runtime_kind: str = "llm",
                      agui_url: str = "", agui_service: str = "",
                      agui_timeout: int = 300,
                      agui_max_tool_rounds: int = 8,
                      gating_service: Any = "") -> Dict[str, Any]:
    """Add an agent instance to a conversation.

    instance_name: the key in conv_agents (chosen by user, unique per conv).
    definition: the repository .md template name (required).
    params: dict of values injected into the definition prompt as ${agent.key}.
    llm_service is required for llm agents. External agents do not require one.
    A direct agui_url is public and unauthenticated; a Bearer secret or a
    private endpoint must come from an aguiConnection service (agui_service).
    flash_delegate_llm_service optionally overrides the service used by
    flash_delegate; when empty, flash agents inherit llm_service.
    """
    runtime_kind = str(runtime_kind or "llm").strip()
    if runtime_kind not in {"llm", "external_mcp", "external_agui"}:
        raise ValueError(
            "runtime_kind must be 'llm', 'external_mcp', or 'external_agui'")
    if runtime_kind == "llm" and not llm_service:
        raise ValueError(
            f"llm_service is required when adding agent '{instance_name}' to conversation")
    if not definition:
        raise ValueError(
            f"definition is required when adding agent '{instance_name}' to conversation")
    if (runtime_kind == "external_agui" and not str(agui_url or "").strip()
            and not str(agui_service or "").strip()):
        raise ValueError(
            "agui_url or agui_service is required for an external_agui agent")
    config = {
        "definition": definition,
        "params": params or {},
        "runtime_kind": runtime_kind,
        "agui_url": str(agui_url or "").strip(),
        "agui_service": str(agui_service or "").strip(),
        "agui_timeout": max(1, int(agui_timeout or 300)),
        "agui_max_tool_rounds": max(
            0, min(32, int(8 if agui_max_tool_rounds in (None, "")
                           else agui_max_tool_rounds))),
        "llm_service": llm_service,
        "flash_delegate_llm_service": flash_delegate_llm_service,
        "model": model,
        "tools": tools or [],
        "assigned_skills": list(skills or []),
        "max_depth": max_depth,
        "gating_service": gating_service if isinstance(gating_service, dict)
        else str(gating_service or "").strip(),
    }
    set_agent_config(conv_id, instance_name, config)
    try:
        from core.conversation_store import ConversationStore
        ConversationStore.instance().prewarm_agent_context(conv_id, instance_name)
    except Exception:
        logger.debug(
            "agent context prewarm failed for %s/%s",
            conv_id[:8], instance_name, exc_info=True)
    return config


def get_definition_name(conv_id: str, instance_name: str) -> str:
    """Get the repository definition name for an agent instance."""
    cfg = get_agent_config(conv_id, instance_name)
    return cfg["definition"]


def flatten_agent_params(instance_name: str,
                        params: Dict[str, Any]) -> Dict[str, str]:
    """Flatten instance params to expression-language keys.

    {"name": "Alice", "specialty": "biology"}
    → {"agent.name": "Alice", "agent.specialty": "biology"}

    The instance name is always available as ${agent.instance_name}.
    """
    flat = {"agent.instance_name": instance_name}
    for k, v in (params or {}).items():
        flat[f"agent.{k}"] = str(v) if v is not None else ""
    return flat


def guess_llm_service(agent_name: str, conv_id: str = "",
                      user_id: str = "") -> str:
    """Suggest a matching LLM service for an agent name (for UI prefill only).

    Looks for services named:
      {agent_name}_llm_service → exact match
      {agent_name}_llm → fallback match
      Otherwise → first enabled LLM-capable service
    Resolved across the canonical scope chain (conv > user > global).
    Returns "" if nothing found.
    """
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        all_defs = reg.resolve_all(user_id=user_id, conv_id=conv_id,
                                   enabled_only=True)
        for candidate in (f"{agent_name}_llm_service", f"{agent_name}_llm"):
            sdef = all_defs.get(candidate)
            if sdef and sdef.service_type in {
                "llmConnection", "llmAggregator", "llmRouter"}:
                return candidate
        all_llm = [
            sdef for sdef in all_defs.values()
            if sdef.service_type in {
                "llmConnection", "llmAggregator", "llmRouter"}
        ]
        if all_llm:
            return all_llm[0].service_id
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return ""


# Sentinel values for agent_name that mean "not a specific agent" — per-
# conv operations (compact, rebuild, view_context) accept these as
# "whole conversation" scope and bypass membership checks.
_AGENT_SCOPE_SENTINELS = frozenset({"", "ALL", "shared"})


def require_agent_member(conv_id: str, agent_name: str,
                         user_id: str = "",
                         auto_register: bool = True) -> Optional[str]:
    """Enforce that `agent_name` is a member of this conversation before
    running a per-agent operation (compact, rebuild, delegate, etc.).

    Returns None when the operation is allowed:
      - agent_name is a scope sentinel ("", "ALL", "shared") — means
        the whole conversation, no specific member required
      - agent_name matches a conv_agents entry (case-insensitive)
      - `auto_register=True` AND a global/user agent definition with a
        derivable llm_service exists → the agent is added into
        conv_agents on the fly and the caller can proceed

    Returns a human-readable error string when the operation is NOT
    allowed. Callers should surface it to the user rather than silently
    propagate — the previous behaviour (accepting any string, creating
    phantom per-agent dirs, failing late in _resolve_agent_client)
    leaked state and produced confusing "No llm_service resolved"
    errors for an agent the user never added to the conv.
    """
    if (agent_name or "") in _AGENT_SCOPE_SENTINELS:
        return None
    if not conv_id:
        # Defensive: without a conv we can't check membership; allow.
        # Caller paths that reach here with empty conv_id have their
        # own invariants to enforce.
        return None
    try:
        members = get_all_agent_configs(conv_id) or {}
    except Exception as _err:
        logger.warning(
            "[agent-member] get_all_agent_configs(%s) failed: %s — "
            "allowing without guard", (conv_id or "")[:8], _err)
        return None
    _needle = agent_name.lower()
    for _k in members.keys():
        if isinstance(_k, str) and _k.lower() == _needle:
            return None  # already a member
    if auto_register:
        # Look up a global/user agent definition (resource). If one
        # exists with an explicitly-declared llm_service (or one we can
        # derive by naming heuristic), register it into conv_agents so
        # the operation proceeds. Matches the user's expectation: "I
        # have qwen defined globally with a service, it should work
        # everywhere without per-conv re-configuration".
        try:
            from core.resource_store import ResourceStore
            _adef = ResourceStore.instance().get_any(
                "agent", agent_name, user_id or "",
                conversation_id=conv_id)
            if _adef is not None:
                _svc = (_adef.get("llm_service", "")
                        or guess_llm_service(agent_name, conv_id,
                                             user_id=user_id or ""))
                if _svc:
                    add_agent_to_conv(conv_id, agent_name,
                                      llm_service=_svc,
                                      definition=agent_name,
                                      skills=_adef.get("assigned_skills") or [])
                    logger.info(
                        "[agent-member] auto-registered agent '%s' "
                        "into conv %s with llm_service='%s' (from "
                        "global definition)",
                        agent_name, conv_id[:8], _svc)
                    return None
        except Exception as _auto_err:
            logger.warning(
                "[agent-member] auto-register of '%s' failed: %s",
                agent_name, _auto_err, exc_info=True)
    return (
        f"Agent '{agent_name}' is not a member of this conversation "
        f"and has no resolvable llm_service. Known agents: "
        f"{sorted(members.keys())}. Create '{agent_name}' as an agent "
        f"resource (with an llm_service) or add it to this conversation "
        f"via the agents panel.")
