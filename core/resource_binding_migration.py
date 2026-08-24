"""Expand, validate, and activate exact assigned-skill bindings.

The migration is conversation-scoped and dormant until the server enables
resource bindings v2.  Activation is one atomic ConversationStore extra write;
an unresolved assignment or a roster change during preflight leaves the legacy
roster authoritative.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from core.resource_identity import AssignedSkill, ResourceRef

RESOURCE_BINDINGS_V2_KEY = "resource_bindings_v2"
RESOURCE_BINDINGS_V2_SCHEMA = 1
_MIGRATION_LOCK = threading.RLock()


def _serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _MIGRATION_LOCK:
            return function(*args, **kwargs)
    return wrapped


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_assignments(configs: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        str(agent_name): copy.deepcopy(list((config or {}).get("assigned_skills") or []))
        for agent_name, config in sorted(configs.items())
        if isinstance(config, dict)
    }


def _stable_skill(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in definition.items()
        if key not in {
            "_invalid", "_scope", "created_at", "skill_root", "updated_at",
        }
    }


def skill_resource_ref(
    name: str,
    definition: dict[str, Any],
    user_id: str,
    conversation_id: str,
) -> ResourceRef:
    """Pin the exact visible skill selected by current scope precedence."""
    scope = str(definition.get("_scope") or "")
    if scope not in {"global", "user", "conversation"}:
        raise ValueError("skill resource scope is missing")
    from core.resource_store import _conv_scope_user

    owner_id = None
    if scope == "user":
        owner_id = user_id
    elif scope == "conversation":
        owner_id = _conv_scope_user(conversation_id, user_id)
    installed = definition.get("installed_from") or {}
    package_id = str(installed.get("package") or "") or None
    package_version = str(installed.get("version") or "") or None
    if bool(package_id) != bool(package_version):
        raise ValueError("skill package identity is incomplete")
    canonical_name = str(definition.get("name") or name or "").strip()
    if not canonical_name:
        raise ValueError("skill resource name is missing")
    stable = _stable_skill(definition)
    return ResourceRef(
        schema_version=1,
        resource_type="skill",
        name=canonical_name,
        scope=scope,
        owner_id=owner_id,
        package_id=package_id,
        package_version=package_version,
        version=str(stable.get("version") or "") or None,
        content_digest=_digest(stable),
        source_id=(
            f"repository:{scope}:{conversation_id}:{canonical_name}"
            if scope == "conversation"
            else f"repository:{scope}:{canonical_name}"
        ),
    )


def resolve_exact_skill(
    ref: ResourceRef | dict[str, Any],
    conversation_id: str,
    *,
    resource_store=None,
) -> dict[str, Any]:
    """Resolve one pinned skill without falling through to another scope."""
    parsed = ref if isinstance(ref, ResourceRef) else ResourceRef.from_dict(ref)
    if parsed.resource_type != "skill":
        raise ValueError("assigned skill ref must target a skill")
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    from core.resource_store import GLOBAL_USER_ID

    if parsed.scope == "global":
        definition = resource_store.get("skill", parsed.name, GLOBAL_USER_ID)
    elif parsed.scope == "user":
        definition = resource_store.get("skill", parsed.name, parsed.owner_id or "")
    else:
        definition = resource_store.get(
            "skill", parsed.name, parsed.owner_id or "",
            conversation_id=conversation_id)
    if definition is None:
        raise ValueError(f"assigned skill is missing: {parsed.name}")
    actual = skill_resource_ref(
        parsed.name,
        {**definition, "_scope": parsed.scope},
        parsed.owner_id or "",
        conversation_id,
    )
    if actual.to_dict() != parsed.to_dict():
        raise ValueError(f"assigned skill identity changed: {parsed.name}")
    result = dict(definition)
    result["_scope"] = parsed.scope
    return result


def _assignment_payload(assignment: AssignedSkill) -> dict[str, Any]:
    value = assignment.to_dict()
    value.pop("assignment_digest", None)
    return value


def validate_assigned_skill(
    value: dict[str, Any],
    conversation_id: str,
    *,
    resource_store=None,
) -> AssignedSkill:
    parsed = AssignedSkill.from_dict(value)
    if _digest(_assignment_payload(parsed)) != parsed.assignment_digest:
        raise ValueError(f"assigned skill digest changed: {parsed.ref.name}")
    resolve_exact_skill(
        parsed.ref, conversation_id, resource_store=resource_store)
    return parsed


def _legacy_entry_parts(entry: Any) -> tuple[str, dict[str, Any], str | None, str | None]:
    if isinstance(entry, str):
        name = entry.strip()
        params: dict[str, Any] = {}
        condition = None
        policy = None
    elif isinstance(entry, dict):
        if entry.get("schema_version") == 2 and "ref" in entry:
            raise ValueError("v2")
        name = str(entry.get("name") or "").strip()
        params = entry.get("params") or {}
        condition = str(entry.get("condition") or "").strip() or None
        policy = str(entry.get("invocation_policy_override") or "").strip() or None
    else:
        raise TypeError("assigned skill entry must be a name or object")
    if not name:
        raise ValueError("assigned skill name is missing")
    if not isinstance(params, dict):
        raise TypeError(f"assigned skill params must be an object: {name}")
    if policy not in {None, "auto", "explicit_only", "disabled"}:
        raise ValueError(f"assigned skill invocation policy is invalid: {name}")
    return name, dict(params), condition, policy


def _expand_assignment(
    entry: Any,
    user_id: str,
    conversation_id: str,
    assigned_at: str,
    assigned_by: str,
    *,
    resource_store,
) -> AssignedSkill:
    if isinstance(entry, dict) and entry.get("schema_version") == 2 and "ref" in entry:
        return validate_assigned_skill(
            entry, conversation_id, resource_store=resource_store)
    try:
        name, params, condition, policy = _legacy_entry_parts(entry)
    except ValueError as exc:
        if str(exc) == "v2":
            return validate_assigned_skill(
                entry, conversation_id, resource_store=resource_store)
        raise
    definition = resource_store.get_any(
        "skill", name, user_id, conversation_id=conversation_id)
    if definition is None:
        raise ValueError(f"assigned skill is not visible: {name}")
    if definition.get("_invalid"):
        raise ValueError(f"assigned skill is invalid: {name}")
    ref = skill_resource_ref(name, definition, user_id, conversation_id)
    provisional = AssignedSkill(
        schema_version=2,
        ref=ref,
        params=params,
        condition=condition,
        invocation_policy_override=policy,
        assigned_at=assigned_at,
        assigned_by=assigned_by,
        assignment_digest="0" * 64,
    )
    return AssignedSkill(
        **_assignment_payload(provisional),
        assignment_digest=_digest(_assignment_payload(provisional)),
    )


@dataclass(frozen=True)
class SkillBindingMigrationPlan:
    conversation_id: str
    user_id: str
    roster_digest: str
    legacy_assignments: dict[str, list[Any]]
    agents: dict[str, list[dict[str, Any]]]
    activated_at: str
    activated_by: str
    blockers: tuple[dict[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blockers


def preflight_skill_binding_migration(
    conversation_id: str,
    user_id: str,
    *,
    conversation_store=None,
    resource_store=None,
    now: Callable[[], str] = _utc_now,
    activated_by: str = "operator",
) -> SkillBindingMigrationPlan:
    """Expand the current legacy roster without writing migration state."""
    if not conversation_id or not user_id:
        raise ValueError("conversation_id and user_id are required")
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    configs = conversation_store.get_extra(conversation_id, "conv_agents") or {}
    if not isinstance(configs, dict):
        raise TypeError("conversation agent roster must be an object")
    legacy = _legacy_assignments(configs)
    assigned_at = now()
    agents: dict[str, list[dict[str, Any]]] = {}
    blockers: list[dict[str, str]] = []
    for agent_name, entries in legacy.items():
        expanded = []
        seen = set()
        for entry in entries:
            try:
                assignment = _expand_assignment(
                    entry, user_id, conversation_id, assigned_at, activated_by,
                    resource_store=resource_store)
                if assignment.ref.name in seen:
                    raise ValueError(
                        f"duplicate assigned skill: {assignment.ref.name}")
                seen.add(assignment.ref.name)
                expanded.append(assignment.to_dict())
            except (TypeError, ValueError) as exc:
                name = str(entry.get("name") or "") if isinstance(entry, dict) else str(entry)
                blockers.append({
                    "agent": agent_name,
                    "skill": name,
                    "reason": str(exc),
                })
        agents[agent_name] = expanded
    return SkillBindingMigrationPlan(
        conversation_id=conversation_id,
        user_id=user_id,
        roster_digest=_digest(legacy),
        legacy_assignments=legacy,
        agents=agents,
        activated_at=assigned_at,
        activated_by=activated_by,
        blockers=tuple(blockers),
    )


def _active_document(conversation_store, conversation_id: str) -> dict[str, Any] | None:
    value = conversation_store.get_extra(
        conversation_id, RESOURCE_BINDINGS_V2_KEY) or {}
    if isinstance(value, dict) and value.get("state") == "active":
        return value
    return None


@_serialized
def activate_skill_binding_migration(
    plan: SkillBindingMigrationPlan,
    *,
    conversation_store=None,
    resource_store=None,
) -> dict[str, Any]:
    """Activate a valid, unchanged preflight plan in one atomic extra write."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    if not plan.ok:
        return {
            "ok": False, "activated": False,
            "blockers": list(plan.blockers),
        }
    current = _active_document(conversation_store, plan.conversation_id)
    if current is not None:
        return migration_status(
            plan.conversation_id, conversation_store=conversation_store,
            idempotent=True)
    configs = conversation_store.get_extra(plan.conversation_id, "conv_agents") or {}
    if _digest(_legacy_assignments(configs)) != plan.roster_digest:
        return {
            "ok": False, "activated": False,
            "blockers": [{
                "agent": "", "skill": "",
                "reason": "conversation assignments changed after preflight",
            }],
        }
    try:
        for assignments in plan.agents.values():
            for value in assignments:
                validate_assigned_skill(
                    value, plan.conversation_id,
                    resource_store=resource_store)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False, "activated": False,
            "blockers": [{"agent": "", "skill": "", "reason": str(exc)}],
        }
    document = {
        "schema_version": RESOURCE_BINDINGS_V2_SCHEMA,
        "state": "active",
        "conversation_id": plan.conversation_id,
        "owner_id": plan.user_id,
        "activated_at": plan.activated_at,
        "activated_by": plan.activated_by,
        "legacy_roster_digest": plan.roster_digest,
        "agents": copy.deepcopy(plan.agents),
        "rollback_assignments": copy.deepcopy(plan.legacy_assignments),
        "first_write_at": None,
        "mutation_revision": 0,
    }
    if not conversation_store.set_extra(
            plan.conversation_id, RESOURCE_BINDINGS_V2_KEY, document):
        raise RuntimeError("failed to persist resource binding activation")
    return migration_status(
        plan.conversation_id, conversation_store=conversation_store)


@_serialized
def migrate_skill_bindings(
    conversation_id: str,
    user_id: str,
    *,
    conversation_store=None,
    resource_store=None,
    now: Callable[[], str] = _utc_now,
    activated_by: str = "operator",
) -> dict[str, Any]:
    """Idempotent one-shot preflight and activation convenience command."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    current = _active_document(conversation_store, conversation_id)
    if current is not None:
        return migration_status(
            conversation_id, conversation_store=conversation_store,
            idempotent=True)
    plan = preflight_skill_binding_migration(
        conversation_id, user_id,
        conversation_store=conversation_store,
        resource_store=resource_store,
        now=now,
        activated_by=activated_by,
    )
    return activate_skill_binding_migration(
        plan,
        conversation_store=conversation_store,
        resource_store=resource_store,
    )


def migration_status(
    conversation_id: str,
    *,
    conversation_store=None,
    idempotent: bool = False,
) -> dict[str, Any]:
    """Return a redacted operator view of one conversation migration."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    value = conversation_store.get_extra(
        conversation_id, RESOURCE_BINDINGS_V2_KEY) or {}
    state = str(value.get("state") or "legacy") if isinstance(value, dict) else "legacy"
    agents = value.get("agents") or {} if isinstance(value, dict) else {}
    return {
        "ok": True,
        "state": state,
        "activated": state == "active",
        "idempotent": idempotent,
        "agent_count": len(agents),
        "assignment_count": sum(len(rows or []) for rows in agents.values()),
        "activated_at": value.get("activated_at") if isinstance(value, dict) else None,
        "first_write_at": value.get("first_write_at") if isinstance(value, dict) else None,
        "rollback_available": bool(
            isinstance(value, dict)
            and state == "active"
            and value.get("first_write_at") is None
            and value.get("rollback_assignments") is not None
        ),
    }


@_serialized
def rollback_skill_binding_migration(
    conversation_id: str,
    *,
    conversation_store=None,
) -> dict[str, Any]:
    """Return to legacy reads only before the first post-activation write."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    current = _active_document(conversation_store, conversation_id)
    if current is None:
        return migration_status(
            conversation_id, conversation_store=conversation_store,
            idempotent=True)
    if current.get("first_write_at") is not None:
        raise ValueError(
            "resource binding rollback is unavailable after the first v2 write")
    rolled_back = dict(current)
    rolled_back["state"] = "rolled_back"
    rolled_back["rolled_back_at"] = _utc_now()
    if not conversation_store.set_extra(
            conversation_id, RESOURCE_BINDINGS_V2_KEY, rolled_back):
        raise RuntimeError("failed to persist resource binding rollback")
    return migration_status(
        conversation_id, conversation_store=conversation_store)


def runtime_skill_assignments(
    conversation_id: str,
    agent_name: str,
    legacy: list[Any],
    *,
    conversation_store=None,
) -> list[Any]:
    """Select v2 only when both the server flag and marker are active."""
    from core.agent_feature_flags import resource_bindings_v2_enabled

    if not resource_bindings_v2_enabled():
        return list(legacy)
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    current = _active_document(conversation_store, conversation_id)
    if current is None:
        return list(legacy)
    if current.get("first_write_at") is None:
        configs = conversation_store.get_extra(conversation_id, "conv_agents") or {}
        if _digest(_legacy_assignments(configs)) != current.get("legacy_roster_digest"):
            raise ValueError("resource binding activation is stale; rerun migration")
    return copy.deepcopy(list((current.get("agents") or {}).get(agent_name) or []))


@_serialized
def replace_active_skill_assignments(
    conversation_id: str,
    user_id: str,
    agent_name: str,
    entries: list[Any],
    *,
    conversation_store=None,
    resource_store=None,
    now: Callable[[], str] = _utc_now,
    assigned_by: str = "operator",
) -> bool:
    """Replace one active v2 assignment list and fence off rollback."""
    from core.agent_feature_flags import resource_bindings_v2_enabled

    if not resource_bindings_v2_enabled():
        return False
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    current = _active_document(conversation_store, conversation_id)
    if current is None:
        return False
    timestamp = now()
    expanded = []
    seen = set()
    for entry in entries:
        assignment = _expand_assignment(
            entry, user_id, conversation_id, timestamp, assigned_by,
            resource_store=resource_store)
        if assignment.ref.name in seen:
            raise ValueError(f"duplicate assigned skill: {assignment.ref.name}")
        seen.add(assignment.ref.name)
        expanded.append(assignment.to_dict())
    updated = copy.deepcopy(current)
    updated.setdefault("agents", {})[agent_name] = expanded
    updated["first_write_at"] = current.get("first_write_at") or timestamp
    updated["rollback_assignments"] = None
    updated["mutation_revision"] = int(current.get("mutation_revision") or 0) + 1
    if not conversation_store.set_extra(
            conversation_id, RESOURCE_BINDINGS_V2_KEY, updated):
        raise RuntimeError("failed to persist v2 skill assignments")
    return True
