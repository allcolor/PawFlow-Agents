"""Optional conversation bindings for PawFlow automatic service roles.

Bindings are overrides only. When a role has no usable explicit binding, its
caller keeps the historical PawFlow resolution path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_EXTRA_KEY = "linked_service_bindings"
_LLM_TYPES = frozenset({"llmConnection", "llmAggregator", "llmRouter"})
_VALID_SCOPES = frozenset({"conv", "user", "global"})

ROLE_SPECS: Dict[str, Dict[str, Any]] = {
    "summary_compaction": {
        "label": "Summary and compaction",
        "default": "PawFlow summarizer",
        "service_types": frozenset({"summarizer"}),
    },
    "project_wiki": {
        "label": "Project wiki",
        "default": "PawFlow wiki using the effective summarizer LLM",
        "service_types": _LLM_TYPES | {"summarizer"},
        "agent_role": "project_wiki",
    },
    "auto_memory": {
        "label": "Automatic memory",
        "default": "PawFlow memory extraction using the effective summarizer LLM",
        "service_types": _LLM_TYPES | {"summarizer"},
    },
    "memory_embeddings": {
        "label": "Memory embeddings",
        "default": "PawFlow embedding parameter, then local embeddings",
        "service_types": frozenset({"llmConnection"}),
    },
    "attachment_ocr": {
        "label": "Attachment OCR",
        "default": "PawFlow attachment pipeline",
        "service_types": frozenset({"llmConnection"}),
    },
    "skill_learning": {
        "label": "Skill learning",
        "default": "PawFlow skill loop using the effective summarizer LLM",
        "service_types": _LLM_TYPES | {"summarizer"},
    },
    "conversation_title": {
        "label": "Conversation titles",
        "default": "PawFlow title configuration",
        "service_types": _LLM_TYPES,
    },
    "content_review": {
        "label": "Content and package review",
        "default": "PawFlow review using the effective summarizer LLM",
        "service_types": _LLM_TYPES | {"summarizer"},
    },
}


def _store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def _scope_id(scope: str, user_id: str, conversation_id: str) -> str:
    if scope == "conv":
        return conversation_id
    if scope == "user":
        return user_id
    return ""


def _raw_bindings(conversation_id: str) -> Dict[str, Dict[str, str]]:
    if not conversation_id:
        return {}
    try:
        raw = _store().get_extra_cached(
            conversation_id, _EXTRA_KEY, default=None)
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Dict[str, str]] = {}
    for role, value in raw.items():
        if role not in ROLE_SPECS or not isinstance(value, dict):
            continue
        kind = str(value.get("kind") or "")
        if kind == "service":
            scope = str(value.get("scope") or "")
            service_id = str(value.get("service_id") or "")
            if scope in _VALID_SCOPES and service_id:
                result[role] = {
                    "kind": "service", "scope": scope,
                    "service_id": service_id,
                }
        elif kind == "agent":
            instance_name = str(value.get("instance_name") or "")
            if instance_name:
                result[role] = {
                    "kind": "agent", "instance_name": instance_name,
                }
    return result


def get_binding(role: str, conversation_id: str) -> Dict[str, str]:
    """Return one explicit role binding, including the legacy summarizer key."""
    if role not in ROLE_SPECS:
        raise ValueError(f"unknown linked service role: {role}")
    if role == "summary_compaction":
        from core.summarizer_bindings import get_binding as get_summarizer
        legacy = get_summarizer(conversation_id)
        if legacy:
            return {"kind": "service", **legacy}
    return dict(_raw_bindings(conversation_id).get(role) or {})


def set_binding(role: str, conversation_id: str, binding: Dict[str, str],
                user_id: str = "") -> None:
    """Store one validated explicit override."""
    if role not in ROLE_SPECS:
        raise ValueError(f"unknown linked service role: {role}")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    validate_binding(role, binding, user_id, conversation_id)
    if role == "summary_compaction":
        from core.summarizer_bindings import set_binding as set_summarizer
        set_summarizer(
            conversation_id, str(binding["scope"]),
            str(binding["service_id"]))
        return
    values = _raw_bindings(conversation_id)
    values[role] = dict(binding)
    _store().set_extra(conversation_id, _EXTRA_KEY, values)


def clear_binding(role: str, conversation_id: str) -> bool:
    """Delete one override so the caller resumes the PawFlow default."""
    if role not in ROLE_SPECS:
        raise ValueError(f"unknown linked service role: {role}")
    if not conversation_id:
        return False
    if role == "summary_compaction":
        from core.summarizer_bindings import clear_binding as clear_summarizer
        return clear_summarizer(conversation_id)
    values = _raw_bindings(conversation_id)
    existed = role in values
    values.pop(role, None)
    _store().set_extra(conversation_id, _EXTRA_KEY, values)
    return existed


def _service_payload(sdef: Any) -> Dict[str, Any]:
    config = getattr(sdef, "config", {}) or {}
    return {
        "kind": "service",
        "scope": str(getattr(sdef, "scope", "") or ""),
        "service_id": str(getattr(sdef, "service_id", "") or ""),
        "service_type": str(getattr(sdef, "service_type", "") or ""),
        "description": str(getattr(sdef, "description", "") or ""),
        "llm_service": str(config.get("llm_service") or ""),
    }


def _agent_definition(
        instance_name: str, user_id: str, conversation_id: str):
    from core.conv_agent_config import resolve_agent_config_entry
    _source, canonical, config = resolve_agent_config_entry(
        conversation_id, instance_name)
    if not canonical:
        return "", {}, {}
    from core.resource_store import ResourceStore
    definition_name = str(config.get("definition") or "")
    definition = ResourceStore.instance().get_any(
        "agent", definition_name, user_id,
        conversation_id=conversation_id) or {}
    return canonical, config, definition


def _agent_supports(
        role: str, instance_name: str, user_id: str,
        conversation_id: str) -> bool:
    canonical, config, definition = _agent_definition(
        instance_name, user_id, conversation_id)
    if not canonical:
        return False
    declared = definition.get("automation_roles") or []
    if role not in declared:
        return False
    if str(config.get("runtime_kind") or "llm") != "workflow":
        return False
    return bool((config.get("workflow") or {}).get("flow_fqn"))


def list_available(
        role: str, user_id: str = "",
        conversation_id: str = "") -> List[Dict[str, Any]]:
    """List visible targets compatible with one automatic role."""
    spec = ROLE_SPECS.get(role)
    if spec is None:
        raise ValueError(f"unknown linked service role: {role}")
    from core.service_registry import ServiceRegistry
    definitions = ServiceRegistry.get_instance().resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    rows = [
        _service_payload(sdef)
        for sdef in definitions.values()
        if str(getattr(sdef, "service_type", "")) in spec["service_types"]
    ]
    if spec.get("agent_role") and conversation_id:
        from core.conv_agent_config import get_all_agent_configs
        for instance_name in get_all_agent_configs(conversation_id):
            try:
                if not _agent_supports(
                        role, instance_name, user_id, conversation_id):
                    continue
                _canonical, config, definition = _agent_definition(
                    instance_name, user_id, conversation_id)
            except Exception:
                logger.warning(
                    "Linked service agent discovery failed: role=%s agent=%s",
                    role, instance_name, exc_info=True)
                continue
            rows.append({
                "kind": "agent",
                "instance_name": instance_name,
                "definition": str(config.get("definition") or ""),
                "description": str(definition.get("description") or ""),
            })
    return rows


def validate_binding(
        role: str, binding: Dict[str, str], user_id: str,
        conversation_id: str) -> None:
    """Reject targets that are not currently visible and compatible."""
    spec = ROLE_SPECS.get(role)
    if spec is None:
        raise ValueError(f"unknown linked service role: {role}")
    kind = str((binding or {}).get("kind") or "")
    if kind == "service":
        scope = str(binding.get("scope") or "")
        service_id = str(binding.get("service_id") or "")
        if scope not in _VALID_SCOPES or not service_id:
            raise ValueError("service binding requires scope and service_id")
        from core.service_registry import ServiceRegistry
        sdef = ServiceRegistry.get_instance().get_definition(
            scope, _scope_id(scope, user_id, conversation_id), service_id)
        if (sdef is None or not getattr(sdef, "enabled", True)
                or str(getattr(sdef, "service_type", ""))
                not in spec["service_types"]):
            raise ValueError(
                f"service '{service_id}' is not compatible with role '{role}'")
        return
    if kind == "agent":
        instance_name = str(binding.get("instance_name") or "")
        if not spec.get("agent_role") or not _agent_supports(
                role, instance_name, user_id, conversation_id):
            raise ValueError(
                f"agent '{instance_name}' is not compatible with role '{role}'")
        return
    raise ValueError("binding kind must be service or agent")


def _binding_status(
        role: str, binding: Dict[str, str], user_id: str,
        conversation_id: str) -> Dict[str, Any]:
    if not binding:
        return {"explicit": False, "binding": {}, "broken": False}
    try:
        validate_binding(role, binding, user_id, conversation_id)
    except Exception as exc:
        logger.warning(
            "Linked service binding validation failed: role=%s cid=%s",
            role, conversation_id[:8], exc_info=True)
        return {
            "explicit": True, "binding": binding, "broken": True,
            "error": str(exc),
        }
    return {
        "explicit": True, "binding": binding, "broken": False,
        "effective": binding,
    }


def summary(user_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
    """Return all role bindings and their compatible targets for the UI."""
    roles = []
    for role, spec in ROLE_SPECS.items():
        binding = get_binding(role, conversation_id)
        state = _binding_status(role, binding, user_id, conversation_id)
        state.update({
            "role": role,
            "label": spec["label"],
            "default": spec["default"],
            "available": list_available(role, user_id, conversation_id),
        })
        roles.append(state)
    return {"roles": roles}


def resolve_service_override(
        role: str, user_id: str = "",
        conversation_id: str = ""):
    """Return (service, definition, explicit) for a service override."""
    binding = get_binding(role, conversation_id)
    if not binding or binding.get("kind") != "service":
        return None, None, bool(binding)
    try:
        validate_binding(role, binding, user_id, conversation_id)
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
        scope = str(binding["scope"])
        scope_id = _scope_id(scope, user_id, conversation_id)
        sdef = registry.get_definition(
            scope, scope_id, str(binding["service_id"]))
        service = registry.get_live_instance(
            scope, scope_id, str(binding["service_id"]))
        return service, sdef, True
    except Exception:
        logger.warning(
            "Linked service override unavailable: role=%s cid=%s",
            role, conversation_id[:8], exc_info=True)
        return None, None, True


def resolve_llm_override(
        role: str, user_id: str = "",
        conversation_id: str = ""):
    """Return (LLM service, definition, service_id, explicit)."""
    service, sdef, explicit = resolve_service_override(
        role, user_id, conversation_id)
    if service is None:
        return None, sdef, "", explicit
    if str(getattr(sdef, "service_type", "")) == "summarizer":
        try:
            client, _context_size, service_id = service.resolve_llm_service(
                user_id, conversation_id)
            return client, sdef, str(service_id or ""), explicit
        except Exception:
            logger.warning(
                "Linked summarizer override failed: role=%s cid=%s",
                role, conversation_id[:8], exc_info=True)
            return None, sdef, "", explicit
    return service, sdef, str(getattr(sdef, "service_id", "") or ""), explicit


def resolve_agent_override(
        role: str, user_id: str = "",
        conversation_id: str = ""):
    """Return (canonical instance name, config, explicit)."""
    binding = get_binding(role, conversation_id)
    if not binding or binding.get("kind") != "agent":
        return "", {}, bool(binding)
    instance_name = str(binding.get("instance_name") or "")
    try:
        supported = _agent_supports(
            role, instance_name, user_id, conversation_id)
    except Exception:
        logger.warning(
            "Linked agent override unavailable: role=%s cid=%s",
            role, conversation_id[:8], exc_info=True)
        return "", {}, True
    if not supported:
        return "", {}, True
    canonical, config, _definition = _agent_definition(
        instance_name, user_id, conversation_id)
    return canonical, config, True
