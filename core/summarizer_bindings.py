"""Conversation summarizer service binding and resolution helpers."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EXTRA_KEY = "summarizer_binding"
_SUMMARIZER_TYPE = "summarizer"


def _store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def get_binding(conversation_id: str) -> Dict[str, str]:
    """Return the explicit summarizer binding for a conversation, if any."""
    if not conversation_id:
        return {}
    raw = _store().get_extra_cached(conversation_id, _EXTRA_KEY, default=None)
    if not isinstance(raw, dict):
        return {}
    scope = str(raw.get("scope", "") or "")
    service_id = str(raw.get("service_id", "") or "")
    if not scope or not service_id:
        return {}
    return {"scope": scope, "service_id": service_id}


def set_binding(conversation_id: str, scope: str, service_id: str) -> None:
    """Bind exactly one summarizer service to a conversation."""
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if scope not in ("conv", "user", "global"):
        raise ValueError("scope must be one of: conv, user, global")
    if not service_id:
        raise ValueError("service_id is required")
    _store().set_extra(conversation_id, _EXTRA_KEY, {
        "scope": scope,
        "service_id": service_id,
    })


def clear_binding(conversation_id: str) -> bool:
    """Remove the explicit summarizer binding, returning True if one existed."""
    if not conversation_id:
        return False
    existed = bool(get_binding(conversation_id))
    _store().set_extra(conversation_id, _EXTRA_KEY, {})
    return existed


def _scope_id(scope: str, user_id: str, conversation_id: str) -> str:
    if scope == "conv":
        return conversation_id
    if scope == "user":
        return user_id
    return ""


def _def_payload(sdef: Any, *, explicit: bool = False) -> Dict[str, Any]:
    cfg = getattr(sdef, "config", {}) or {}
    return {
        "service_id": getattr(sdef, "service_id", ""),
        "scope": getattr(sdef, "scope", ""),
        "service_type": getattr(sdef, "service_type", ""),
        "enabled": getattr(sdef, "enabled", True),
        "description": getattr(sdef, "description", ""),
        "llm_service": cfg.get("llm_service", ""),
        "explicit": explicit,
    }


def list_available(user_id: str = "", conversation_id: str = "") -> List[Dict[str, Any]]:
    """List enabled summarizer services in runtime resolution order."""
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    return [
        _def_payload(sdef)
        for sdef in reg.resolve_by_type(
            _SUMMARIZER_TYPE,
            user_id=user_id,
            conv_id=conversation_id,
            enabled_only=True,
        )
    ]


def resolve_definition(user_id: str = "", conversation_id: str = ""):
    """Resolve the effective summarizer ServiceDef.

    Explicit conversation binding wins. Without it, the first enabled
    summarizer in ServiceRegistry scope order wins: conv -> user -> global.
    """
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    binding = get_binding(conversation_id)
    if binding:
        sdef = reg.get_definition(
            binding["scope"],
            _scope_id(binding["scope"], user_id, conversation_id),
            binding["service_id"],
        )
        if sdef and getattr(sdef, "enabled", True) and sdef.service_type == _SUMMARIZER_TYPE:
            return sdef, True
        logger.warning(
            "Explicit summarizer binding is unavailable: cid=%s scope=%s service=%s",
            conversation_id[:8], binding.get("scope"), binding.get("service_id"))
        return None, True

    defs = reg.resolve_by_type(
        _SUMMARIZER_TYPE,
        user_id=user_id,
        conv_id=conversation_id,
        enabled_only=True,
    )
    return (defs[0], False) if defs else (None, False)


def resolve_service(user_id: str = "", conversation_id: str = "") -> Tuple[Any, Any, bool]:
    """Resolve the effective live summarizer service.

    Returns ``(service, service_def, explicit)``. ``service`` is None when the
    explicit binding is broken or no summarizer exists.
    """
    sdef, explicit = resolve_definition(user_id, conversation_id)
    if not sdef:
        return None, None, explicit
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
    return svc, sdef, explicit


def summary(user_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
    """Return UI-friendly current/effective summarizer state."""
    binding = get_binding(conversation_id)
    available = list_available(user_id, conversation_id)
    sdef, explicit = resolve_definition(user_id, conversation_id)
    effective = _def_payload(sdef, explicit=explicit) if sdef else None
    return {
        "binding": binding,
        "available": available,
        "effective": effective,
        "explicit": explicit,
    }
