"""Policy gate bindings and resolution (plan section 9).

A gate acts only through an explicit binding: one per conversation
(conversation extra ``gating_binding``) and optionally one per agent
(``gating_service`` in the conversation agent config). There is no implicit
"first available gate" fallback; a broken binding fails closed and is
reported, never replaced by another service.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_EXTRA_KEY = "gating_binding"
GATING_TYPE = "gating"
_SCOPES = ("conv", "user", "global")


def _store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def _registry():
    from core.service_registry import ServiceRegistry
    return ServiceRegistry.get_instance()


def _scope_id(scope: str, user_id: str, conversation_id: str) -> str:
    if scope == "conv":
        return conversation_id
    if scope == "user":
        return user_id
    return ""


def _normalize_ref(raw: Any) -> Dict[str, str]:
    """``{"scope", "service_id"}`` from a dict or a bare service id string."""
    if isinstance(raw, dict):
        scope = str(raw.get("scope") or "")
        service_id = str(raw.get("service_id") or "")
    else:
        scope, service_id = "", str(raw or "").strip()
    if not service_id:
        return {}
    return {"scope": scope, "service_id": service_id}


# ── conversation binding ─────────────────────────────────────────────

def get_binding(conversation_id: str) -> Dict[str, str]:
    if not conversation_id:
        return {}
    try:
        raw = _store().get_extra_cached(conversation_id, _EXTRA_KEY, default=None)
    except ValueError:
        return {}
    ref = _normalize_ref(raw)
    return ref if ref.get("scope") in _SCOPES else {}


def set_binding(conversation_id: str, scope: str, service_id: str) -> None:
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if scope not in _SCOPES:
        raise ValueError("scope must be one of: conv, user, global")
    if not service_id:
        raise ValueError("service_id is required")
    _store().set_extra(conversation_id, _EXTRA_KEY, {"scope": scope, "service_id": service_id})


def clear_binding(conversation_id: str) -> bool:
    if not conversation_id:
        return False
    existed = bool(get_binding(conversation_id))
    _store().set_extra(conversation_id, _EXTRA_KEY, {})
    return existed


# ── definitions ──────────────────────────────────────────────────────

def _def_payload(sdef: Any, *, explicit: bool = False) -> Dict[str, Any]:
    cfg = getattr(sdef, "config", {}) or {}
    return {
        "service_id": getattr(sdef, "service_id", ""),
        "scope": getattr(sdef, "scope", ""),
        "service_type": getattr(sdef, "service_type", ""),
        "enabled": getattr(sdef, "enabled", True),
        "description": getattr(sdef, "description", ""),
        "llm_service": cfg.get("llm_service", ""),
        "scripts": list(cfg.get("scripts") or []),
        "llm_scope": cfg.get("llm_scope", "mutating"),
        "failure_decision": cfg.get("failure_decision", "ask"),
        "explicit": explicit,
    }


def list_available(user_id: str = "", conversation_id: str = "") -> List[Dict[str, Any]]:
    return [_def_payload(sdef) for sdef in _registry().resolve_by_type(
        GATING_TYPE, user_id=user_id, conv_id=conversation_id, enabled_only=True)]


def _definition(ref: Dict[str, str], user_id: str, conversation_id: str):
    reg = _registry()
    scope = ref.get("scope") or ""
    if scope in _SCOPES:
        sdef = reg.get_definition(scope, _scope_id(scope, user_id, conversation_id),
                                  ref["service_id"])
    else:
        sdef = reg.resolve_definition(ref["service_id"], user_id=user_id,
                                      conv_id=conversation_id)
    if not sdef or not getattr(sdef, "enabled", True) \
            or getattr(sdef, "service_type", "") != GATING_TYPE:
        return None
    return sdef


def validate_binding(scope: str, service_id: str, user_id: str,
                     conversation_id: str) -> Any:
    """Raise ValueError unless the reference is a usable gate for this conversation.

    A7: a gate with scripts needs a relay linked to the conversation because
    scripts run in the relay sandbox; binding it without one is refused.
    """
    ref = {"scope": scope, "service_id": service_id}
    sdef = _definition(ref, user_id, conversation_id)
    if sdef is None:
        raise ValueError(f"Gating service '{service_id}' is not available in scope '{scope}'")
    cfg = getattr(sdef, "config", {}) or {}
    if [s for s in (cfg.get("scripts") or []) if str(s).strip()]:
        from core.relay_bindings import get_linked_all
        if not get_linked_all(conversation_id):
            raise ValueError(
                f"Gating service '{service_id}' uses policy scripts, which run in the "
                "relay sandbox: link a relay to this conversation first")
    return sdef


def _live(sdef: Any):
    svc = _registry().get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
    return svc if svc is not None and hasattr(svc, "evaluate") else None


# ── resolution ───────────────────────────────────────────────────────

def _resolve_ref(ref: Dict[str, str], user_id: str, conversation_id: str,
                 *, origin: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"origin": origin, "ref": dict(ref), "service": None,
                           "definition": None, "broken": False, "error": ""}
    if not ref:
        return out
    sdef = _definition(ref, user_id, conversation_id)
    if sdef is None:
        out["broken"] = True
        out["error"] = f"gating service '{ref.get('service_id')}' is unavailable"
        return out
    svc = _live(sdef)
    if svc is None:
        out["broken"] = True
        out["error"] = f"gating service '{ref.get('service_id')}' could not be started"
        out["definition"] = _def_payload(sdef, explicit=True)
        return out
    cfg = getattr(sdef, "config", {}) or {}
    if [s for s in (cfg.get("scripts") or []) if str(s).strip()]:
        from core.relay_bindings import get_linked_all
        if not get_linked_all(conversation_id):
            out["broken"] = True
            out["error"] = (f"gating service '{ref.get('service_id')}' needs a linked "
                            "relay for its policy scripts")
            out["definition"] = _def_payload(sdef, explicit=True)
            return out
    out["service"] = svc
    out["definition"] = _def_payload(sdef, explicit=True)
    return out


def agent_ref(conversation_id: str, agent_name: str) -> Dict[str, str]:
    if not conversation_id or not agent_name:
        return {}
    from core.conv_agent_config import get_agent_config
    try:
        return _normalize_ref(get_agent_config(conversation_id, agent_name).get("gating_service"))
    except Exception:
        logger.debug("agent gating ref lookup failed", exc_info=True)
        return {}


def resolve_gates(user_id: str, conversation_id: str,
                  agent_name: str = "") -> Dict[str, Any]:
    """Ordered gates for a call: conversation first, then a distinct agent gate.

    ``bound`` is True as soon as any binding exists, even a broken one — the
    engine then fails closed instead of running ungated.
    """
    conversation = _resolve_ref(get_binding(conversation_id), user_id, conversation_id,
                                origin="conversation")
    agent_binding = agent_ref(conversation_id, agent_name)
    if agent_binding and conversation["ref"] and \
            agent_binding["service_id"] == conversation["ref"].get("service_id") and \
            (not agent_binding.get("scope") or agent_binding["scope"] == conversation["ref"].get("scope")):
        agent_binding = {}  # same service twice evaluates once
    agent = _resolve_ref(agent_binding, user_id, conversation_id, origin="agent")
    return {
        "conversation": conversation,
        "agent": agent,
        "bound": bool(conversation["ref"] or agent["ref"]),
        "broken": bool(conversation["broken"] or agent["broken"]),
        "gates": [g for g in (conversation, agent) if g["service"] is not None],
    }


def summary(user_id: str = "", conversation_id: str = "",
            agent_name: str = "") -> Dict[str, Any]:
    """UI-friendly binding/effective state (no live service objects)."""
    resolved = resolve_gates(user_id, conversation_id, agent_name) if conversation_id \
        else {"conversation": {"ref": {}, "broken": False, "error": "", "definition": None},
              "agent": {"ref": {}, "broken": False, "error": "", "definition": None},
              "bound": False, "broken": False}

    def _view(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {"ref": entry.get("ref") or {}, "effective": entry.get("definition"),
                "broken": bool(entry.get("broken")), "error": entry.get("error", "")}

    return {
        "binding": get_binding(conversation_id),
        "available": list_available(user_id, conversation_id),
        "conversation": _view(resolved["conversation"]),
        "agent": _view(resolved["agent"]),
        "bound": bool(resolved["bound"]),
        "broken": bool(resolved["broken"]),
    }


__all__ = ["GATING_TYPE", "agent_ref", "clear_binding", "get_binding", "list_available",
           "resolve_gates", "set_binding", "summary", "validate_binding"]
