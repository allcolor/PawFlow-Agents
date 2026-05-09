"""Skill review service binding and write-time helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EXTRA_KEY = "skill_review_binding"
_SERVICE_TYPE = "skillReview"


def _store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def get_binding(conversation_id: str) -> Dict[str, str]:
    if not conversation_id:
        return {}
    try:
        raw = _store().get_extra_cached(conversation_id, _EXTRA_KEY, default=None)
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    scope = str(raw.get("scope", "") or "")
    service_id = str(raw.get("service_id", "") or "")
    if not scope or not service_id:
        return {}
    return {"scope": scope, "service_id": service_id}


def set_binding(conversation_id: str, scope: str, service_id: str) -> None:
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
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    return [
        _def_payload(sdef)
        for sdef in reg.resolve_by_type(
            _SERVICE_TYPE,
            user_id=user_id,
            conv_id=conversation_id,
            enabled_only=True,
        )
    ]


def resolve_definition(user_id: str = "", conversation_id: str = ""):
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    binding = get_binding(conversation_id)
    if binding:
        sdef = reg.get_definition(
            binding["scope"],
            _scope_id(binding["scope"], user_id, conversation_id),
            binding["service_id"],
        )
        if sdef and getattr(sdef, "enabled", True) and sdef.service_type == _SERVICE_TYPE:
            return sdef, True
        logger.warning(
            "Explicit skill review binding is unavailable: cid=%s scope=%s service=%s",
            conversation_id[:8], binding.get("scope"), binding.get("service_id"))
        return None, True

    defs = reg.resolve_by_type(
        _SERVICE_TYPE,
        user_id=user_id,
        conv_id=conversation_id,
        enabled_only=True,
    )
    return (defs[0], False) if defs else (None, False)


def resolve_service(user_id: str = "", conversation_id: str = "") -> Tuple[Any, Any, bool]:
    sdef, explicit = resolve_definition(user_id, conversation_id)
    if not sdef:
        return None, None, explicit
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
    return svc, sdef, explicit


def summary(user_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
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


def skill_review_hash(skill: Dict[str, Any], package_files: Optional[Dict[str, str]] = None) -> str:
    relevant = {
        key: skill.get(key)
        for key in (
            "prompt", "description", "parameters", "extends",
            "template_engine", "dynamic_context", "tools", "capabilities",
        )
        if key in skill
    }
    payload = {
        "skill": relevant,
        "package_files": package_files or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def review_for_write(skill: Dict[str, Any], *, operation: str,
                     user_id: str = "", conversation_id: str = "",
                     package_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Review skill content before create/update/import and return metadata.

    Raises ValueError when policy blocks the write.
    """
    svc, sdef, explicit = resolve_service(user_id, conversation_id)
    using_configured_service = bool(svc is not None or explicit)
    if explicit and svc is None:
        review = {
            "risk": "block",
            "allowed": False,
            "requires_human_review": True,
            "findings": [{
                "severity": "block",
                "category": "review_service_unavailable",
                "evidence": "skill_review_binding",
                "reason": "Explicit skill review service binding is unavailable.",
            }],
            "reviewer": "missing-explicit-skillReview",
            "reviewed_at": time.time(),
        }
    elif svc is not None and getattr(svc, "should_review", lambda _op: True)(operation):
        review = svc.review_skill(
            skill,
            user_id=user_id,
            conversation_id=conversation_id,
            package_files=package_files or {},
        )
    else:
        from core.skill_review import static_review_skill
        review = static_review_skill(skill, package_files=package_files or {})

    review.setdefault("reviewed_at", time.time())
    review_hash = skill_review_hash(skill, package_files=package_files)
    findings = list(review.get("findings") or [])
    metadata = {
        "hash": review_hash,
        "risk": str(review.get("risk", "medium") or "medium"),
        "allowed": bool(review.get("allowed", False)),
        "requires_human_review": bool(review.get("requires_human_review", False)),
        "reviewer": review.get("reviewer", "static"),
        "reviewed_at": review.get("reviewed_at"),
        "findings_count": len(findings),
    }
    if sdef:
        metadata["service_id"] = getattr(sdef, "service_id", "")
        metadata["llm_service"] = (getattr(sdef, "config", {}) or {}).get("llm_service", "")

    if not metadata["allowed"] or metadata["risk"] == "block":
        reasons = "; ".join(
            f.get("reason", "") for f in findings[:3] if isinstance(f, dict) and f.get("reason")
        )
        raise ValueError(f"Skill review blocked this write: {reasons or metadata['risk']}")
    if not using_configured_service and metadata["risk"] == "low" and not findings:
        return {}
    return metadata


def review_now(skill: Dict[str, Any], *, user_id: str = "",
               conversation_id: str = "",
               package_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Return a full review report using the configured skillReview service."""
    svc, _sdef, explicit = resolve_service(user_id, conversation_id)
    if explicit and svc is None:
        return {
            "risk": "block",
            "allowed": False,
            "requires_human_review": True,
            "findings": [{
                "severity": "block",
                "category": "review_service_unavailable",
                "evidence": "skill_review_binding",
                "reason": "Explicit skill review service binding is unavailable.",
            }],
            "reviewer": "missing-explicit-skillReview",
            "reviewed_at": time.time(),
        }
    if svc is not None:
        return svc.review_skill(
            skill,
            user_id=user_id,
            conversation_id=conversation_id,
            package_files=package_files or {},
        )
    from core.skill_review import static_review_skill
    return static_review_skill(skill, package_files=package_files or {})


def attach_review_metadata(skill: Dict[str, Any], review_metadata: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(skill)
    data["review"] = review_metadata
    return data
