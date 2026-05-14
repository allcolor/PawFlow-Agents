"""Conversation and per-agent tool/MCP availability filters."""

from typing import Any, Dict, Iterable, Set


FILTERS_KEY = "tool_mcp_filters"


def _parent_conversation_id(conversation_id: str) -> str:
    conversation_id = str(conversation_id or "")
    for marker in ("::task::", "::task_verify::", "::delegate::"):
        if marker in conversation_id:
            return conversation_id.split(marker, 1)[0]
    return ""


def _clean_names(values: Iterable[Any]) -> list[str]:
    seen = set()
    out = []
    for value in values or []:
        name = str(value or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _default_filters() -> Dict[str, Any]:
    return {
        "disabled_tools": [],
        "enabled_dynamic_tools": [],
        "enabled_mcps": [],
        "agent_overrides": {},
    }


def get_filters(conversation_id: str) -> Dict[str, Any]:
    if not conversation_id:
        return _default_filters()
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    raw = store.get_extra(conversation_id, FILTERS_KEY, default=None)
    if not isinstance(raw, dict):
        parent_id = _parent_conversation_id(conversation_id)
        if parent_id:
            raw = store.get_extra(parent_id, FILTERS_KEY, default=None)
    data = _default_filters()
    if isinstance(raw, dict):
        data.update(raw)
    data["disabled_tools"] = _clean_names(data.get("disabled_tools") or [])
    data["enabled_dynamic_tools"] = _clean_names(data.get("enabled_dynamic_tools") or [])
    data["enabled_mcps"] = _clean_names(data.get("enabled_mcps") or [])
    data.pop("disabled_mcps", None)
    if not isinstance(data.get("agent_overrides"), dict):
        data["agent_overrides"] = {}
    return data


def set_filters(conversation_id: str, filters: Dict[str, Any]) -> Dict[str, Any]:
    if not conversation_id:
        raise ValueError("conversation_id is required")
    data = _default_filters()
    data.update(filters or {})
    data["disabled_tools"] = _clean_names(data.get("disabled_tools") or [])
    data["enabled_dynamic_tools"] = _clean_names(data.get("enabled_dynamic_tools") or [])
    data["enabled_mcps"] = _clean_names(data.get("enabled_mcps") or [])
    data.pop("disabled_mcps", None)
    overrides = {}
    for agent, cfg in (data.get("agent_overrides") or {}).items():
        if not isinstance(cfg, dict):
            continue
        entry = {}
        for kind in ("tools", "mcps"):
            mode = cfg.get(kind, {}).get("mode", "inherit") if isinstance(cfg.get(kind), dict) else "inherit"
            names_key = "enabled" if kind == "mcps" else "selected"
            names = cfg.get(kind, {}).get(names_key, []) if isinstance(cfg.get(kind), dict) else []
            entry[kind] = {
                "mode": "custom" if mode == "custom" else "inherit",
                names_key: _clean_names(names),
            }
        overrides[str(agent)] = entry
    data["agent_overrides"] = overrides
    from core.conversation_store import ConversationStore
    ConversationStore.instance().set_extra(conversation_id, FILTERS_KEY, data)
    return data


def _agent_cfg(filters: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    overrides = filters.get("agent_overrides") or {}
    cfg = overrides.get(agent_name or "")
    if cfg is None and agent_name:
        needle = agent_name.lower()
        for key, value in overrides.items():
            if str(key).lower() == needle:
                cfg = value
                break
    return cfg if isinstance(cfg, dict) else {}


def disabled_names(conversation_id: str, agent_name: str = "",
                   kind: str = "tools") -> Set[str]:
    if kind == "mcps":
        return set()
    filters = get_filters(conversation_id)
    cfg = _agent_cfg(filters, agent_name)
    scoped = cfg.get("tools")
    if isinstance(scoped, dict) and scoped.get("mode") == "custom":
        return set()
    return set(filters.get("disabled_tools") or [])


def is_tool_enabled(conversation_id: str, name: str, agent_name: str = "",
                    origin: str = "builtin", origin_scope: str = "") -> bool:
    if not name:
        return False
    filters = get_filters(conversation_id)
    if agent_name:
        scoped = _agent_cfg(filters, agent_name).get("tools")
        if isinstance(scoped, dict) and scoped.get("mode") == "custom":
            return name in set(scoped.get("selected") or [])
    if origin == "dynamic" and origin_scope != "conversation":
        return name in set(filters.get("enabled_dynamic_tools") or [])
    return name not in set(filters.get("disabled_tools") or [])


def enabled_mcp_names(conversation_id: str, agent_name: str = "") -> Set[str]:
    filters = get_filters(conversation_id)
    base = set(filters.get("enabled_mcps") or [])
    if not agent_name:
        return base
    cfg = _agent_cfg(filters, agent_name)
    scoped = cfg.get("mcps")
    if not isinstance(scoped, dict) or scoped.get("mode") != "custom":
        return base
    return set(scoped.get("enabled") or [])


def enabled_dynamic_tool_names(conversation_id: str, agent_name: str = "") -> Set[str]:
    filters = get_filters(conversation_id)
    base = set(filters.get("enabled_dynamic_tools") or [])
    if not agent_name:
        return base
    cfg = _agent_cfg(filters, agent_name)
    scoped = cfg.get("tools")
    if not isinstance(scoped, dict) or scoped.get("mode") != "custom":
        return base
    return set(scoped.get("selected") or [])


def is_enabled(conversation_id: str, name: str, agent_name: str = "",
               kind: str = "tools") -> bool:
    if kind == "mcps":
        return bool(name) and name in enabled_mcp_names(conversation_id, agent_name)
    return is_tool_enabled(conversation_id, name, agent_name)
