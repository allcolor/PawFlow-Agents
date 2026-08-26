"""Exact resolution and conversation binding for bounded agent groups."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.agent_group_contracts import AgentGroupDefinition
from core.resource_identity import ResourceRef

_LLM_SERVICE_TYPES = frozenset({"llmConnection", "llmAggregator", "llmRouter"})
AGENT_GROUP_BINDINGS_KEY = "agent_group_bindings"


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_agent_group_data(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical stored definition or reject malformed group data."""
    if not isinstance(data, dict):
        raise TypeError("agent group data must be an object")
    payload = {
        key: value for key, value in data.items()
        if key not in {"created_at", "updated_at", "_scope"}
    }
    declared_name = str(payload.get("name") or name or "").strip()
    if declared_name != str(name or "").strip():
        raise ValueError("agent group name must match its resource name")
    payload["name"] = declared_name
    return AgentGroupDefinition.from_dict(payload).to_dict()


@dataclass(frozen=True)
class ResolvedAgentResource:
    definition: dict[str, Any]
    ref: ResourceRef


def _resource_ref(
    resource_type: str,
    name: str,
    definition: dict[str, Any],
    user_id: str,
    conversation_id: str,
) -> ResourceRef:
    scope = str(definition.get("_scope") or "")
    if scope not in {"global", "user", "conversation"}:
        raise ValueError(f"{resource_type} resource scope is missing")
    from core.resource_store import _conv_scope_user

    owner_id = None
    if scope == "user":
        owner_id = user_id
    elif scope == "conversation":
        owner_id = _conv_scope_user(conversation_id, user_id)
    installed = definition.get("installed_from") or {}
    stable = {
        key: value for key, value in definition.items()
        if key not in {"created_at", "updated_at", "_scope"}
    }
    return ResourceRef(
        schema_version=1,
        resource_type=resource_type,
        name=name,
        scope=scope,
        owner_id=owner_id,
        package_id=str(installed.get("package") or "") or None,
        package_version=str(installed.get("version") or "") or None,
        version=str(stable.get("version") or "") or None,
        content_digest=_digest(stable),
        source_id=(
            f"repository:{scope}:{conversation_id}:{name}"
            if scope == "conversation" else f"repository:{scope}:{name}"
        ),
    )


def resolve_agent_resource(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    resource_store=None,
) -> ResolvedAgentResource:
    """Resolve one visible agent definition and pin its exact content."""
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    definition = resource_store.get_any(
        "agent", name, user_id, conversation_id=conversation_id)
    if definition is None:
        raise ValueError(f"agent definition is not visible: {name}")
    canonical_name = str(definition.get("name") or name)
    return ResolvedAgentResource(
        definition=dict(definition),
        ref=_resource_ref(
            "agent", canonical_name, definition, user_id, conversation_id),
    )


def resolve_agent_group_resource(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    resource_store=None,
) -> tuple[AgentGroupDefinition, ResourceRef]:
    """Resolve and validate one visible exact group definition."""
    if resource_store is None:
        from core.resource_store import ResourceStore
        resource_store = ResourceStore.instance()
    definition = resource_store.get_any(
        "agent_group", name, user_id, conversation_id=conversation_id)
    if definition is None:
        raise ValueError(f"agent group is not visible: {name}")
    parsed = AgentGroupDefinition.from_dict({
        key: value for key, value in definition.items()
        if key not in {"created_at", "updated_at", "_scope"}
    })
    return parsed, _resource_ref(
        "agent_group", parsed.name, definition, user_id, conversation_id)


def bind_agent_group(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    resource_store=None,
    conversation_store=None,
    service_registry=None,
) -> dict[str, Any]:
    """Bind an exact group and concrete LLM members to one conversation."""
    if not conversation_id:
        raise ValueError("agent group binding requires conversation_id")
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    if service_registry is None:
        from core.service_registry import ServiceRegistry
        service_registry = ServiceRegistry.get_instance()

    group, group_ref = resolve_agent_group_resource(
        name, user_id, conversation_id, resource_store=resource_store)
    if group.tool_policy.mode not in {"none", "read_only"}:
        raise ValueError("agent groups support only none or read_only tool mode")

    from core.conv_agent_config import resolve_agent_config_entry
    from core.identifier import resolve_identifier
    from core.service_definition_revision import compute_service_definition_revision

    service_defs = service_registry.resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    snapshots: list[dict[str, Any]] = []
    for member in group.members:
        roster_conversation, instance_name, config = resolve_agent_config_entry(
            conversation_id, member.instance_name)
        if roster_conversation != conversation_id or not instance_name:
            raise ValueError(
                f"group member is not bound to this conversation: {member.instance_name}")
        if str(config.get("runtime_kind") or "llm") != "llm":
            raise ValueError(
                f"group member must use runtime_kind llm: {instance_name}")
        definition_name = str(config.get("definition") or "")
        resolved = resolve_agent_resource(
            definition_name, user_id, conversation_id,
            resource_store=resource_store)
        if resolved.ref != member.agent_ref:
            raise ValueError(
                f"group member agent ref changed or mismatched: {member.member_id}")
        service_id = str(config.get("llm_service") or "").strip()
        canonical_service = resolve_identifier(service_defs, service_id)
        if not canonical_service:
            raise ValueError(
                f"group member LLM service is unavailable: {member.member_id}")
        service = service_defs[canonical_service]
        if str(service.service_type) not in _LLM_SERVICE_TYPES:
            raise ValueError(
                f"group member service is not API LLM-compatible: {member.member_id}")
        snapshot = {
            "member_id": member.member_id,
            "instance_name": instance_name,
            "agent_ref": resolved.ref.to_dict(),
            "agent_definition": {
                key: value for key, value in resolved.definition.items()
                if key not in {"created_at", "updated_at", "_scope"}
            },
            "params": dict(config.get("params") or {}),
            "model": str(config.get("model") or ""),
            "tools": list(config.get("tools") or ()),
            "service": {
                "service_id": str(service.service_id),
                "service_type": str(service.service_type),
                "scope": str(service.scope),
                "scope_id": str(service.scope_id),
                "definition_revision": compute_service_definition_revision(service),
            },
        }
        snapshot["snapshot_digest"] = _digest(snapshot)
        snapshots.append(snapshot)

    binding = {
        "schema_version": 1,
        "group_ref": group_ref.to_dict(),
        "definition": group.to_dict(),
        "member_snapshots": snapshots,
        "binding_digest": _digest({
            "group_ref": group_ref.to_dict(),
            "member_snapshots": snapshots,
        }),
    }
    bindings = dict(conversation_store.get_extra(
        conversation_id, AGENT_GROUP_BINDINGS_KEY) or {})
    bindings[group.name] = binding
    conversation_store.set_extra(
        conversation_id, AGENT_GROUP_BINDINGS_KEY, bindings)
    return binding


def get_bound_agent_group(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    conversation_store=None,
    resource_store=None,
) -> dict[str, Any]:
    """Return a binding only while its exact group ref remains current."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    bindings = conversation_store.get_extra(
        conversation_id, AGENT_GROUP_BINDINGS_KEY) or {}
    binding = dict(bindings.get(name) or {})
    if not binding:
        raise ValueError(f"agent group is not bound to this conversation: {name}")
    _group, current_ref = resolve_agent_group_resource(
        name, user_id, conversation_id, resource_store=resource_store)
    if ResourceRef.from_dict(binding.get("group_ref") or {}) != current_ref:
        raise ValueError("agent group binding is stale")
    return binding


def snapshot_bound_agent_group(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    conversation_store=None,
    resource_store=None,
    service_registry=None,
) -> dict[str, Any]:
    """Revalidate a binding and freeze current service revisions for one run."""
    binding = get_bound_agent_group(
        name,
        user_id,
        conversation_id,
        conversation_store=conversation_store,
        resource_store=resource_store,
    )
    group = AgentGroupDefinition.from_dict(binding["definition"])
    if service_registry is None:
        from core.service_registry import ServiceRegistry
        service_registry = ServiceRegistry.get_instance()
    definitions = service_registry.resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    from core.conv_agent_config import resolve_agent_config_entry
    from core.identifier import resolve_identifier
    from core.service_definition_revision import compute_service_definition_revision

    by_member = {
        str(item.get("member_id") or ""): dict(item)
        for item in binding.get("member_snapshots") or ()
    }
    run_members: list[dict[str, Any]] = []
    services: dict[str, dict[str, Any]] = {}
    for member in group.members:
        bound = by_member.get(member.member_id)
        if not bound:
            raise ValueError(f"group binding has no member snapshot: {member.member_id}")
        roster_conversation, instance_name, config = resolve_agent_config_entry(
            conversation_id, member.instance_name)
        if roster_conversation != conversation_id or not instance_name:
            raise ValueError(f"group member left the conversation: {member.member_id}")
        if str(config.get("runtime_kind") or "llm") != "llm":
            raise ValueError(f"group member runtime changed: {member.member_id}")
        current_agent = resolve_agent_resource(
            str(config.get("definition") or ""), user_id, conversation_id,
            resource_store=resource_store)
        if current_agent.ref != member.agent_ref:
            raise ValueError(f"group member agent ref is stale: {member.member_id}")
        service_id = str(config.get("llm_service") or "")
        canonical = resolve_identifier(definitions, service_id)
        service = definitions.get(canonical) if canonical else None
        if service is None or str(service.service_type) not in _LLM_SERVICE_TYPES:
            raise ValueError(f"group member LLM service is unavailable: {member.member_id}")
        service_snapshot = {
            "service_id": str(service.service_id),
            "service_type": str(service.service_type),
            "scope": str(service.scope),
            "scope_id": str(service.scope_id),
            "definition_revision": compute_service_definition_revision(service),
        }
        services[service_snapshot["service_id"]] = service_snapshot
        snapshot = {
            **bound,
            "instance_name": instance_name,
            "params": dict(config.get("params") or {}),
            "model": str(config.get("model") or ""),
            "tools": [],
            "service": service_snapshot,
        }
        snapshot["snapshot_digest"] = _digest({
            key: value for key, value in snapshot.items()
            if key != "snapshot_digest"
        })
        run_members.append(snapshot)

    group_services: dict[str, str] = {}
    role_ids = {
        "classifier": group.selection.classifier_service_role,
        "synthesis": group.synthesis.llm_service_role,
    }
    for role, requested in role_ids.items():
        if not requested:
            continue
        canonical = resolve_identifier(definitions, requested)
        service = definitions.get(canonical) if canonical else None
        if service is None or str(service.service_type) not in _LLM_SERVICE_TYPES:
            raise ValueError(f"group {role} LLM service is unavailable: {requested}")
        snapshot = {
            "service_id": str(service.service_id),
            "service_type": str(service.service_type),
            "scope": str(service.scope),
            "scope_id": str(service.scope_id),
            "definition_revision": compute_service_definition_revision(service),
        }
        services[snapshot["service_id"]] = snapshot
        group_services[role] = snapshot["service_id"]

    return {
        "bindings": {},
        "services": services,
        "agent_group": {
            "schema_version": 1,
            "group_ref": binding["group_ref"],
            "definition": group.to_dict(),
            "member_snapshots": run_members,
            "group_services": group_services,
            "run_snapshot_digest": _digest({
                "group_ref": binding["group_ref"],
                "members": run_members,
                "group_services": group_services,
            }),
        },
    }


def bind_agent_group_instance(
    group_name: str,
    instance_name: str,
    user_id: str,
    conversation_id: str,
    *,
    preempt_policy: str = "queue",
) -> dict[str, Any]:
    """Bind a group and expose it as a normal selectable workflow agent."""
    binding = bind_agent_group(group_name, user_id, conversation_id)
    group = AgentGroupDefinition.from_dict(binding["definition"])
    from core.conv_agent_config import add_agent_to_conv

    config = add_agent_to_conv(
        conversation_id,
        instance_name,
        llm_service="",
        definition="group-deliberation",
        runtime_kind="workflow",
        tools=[],
        skills=[],
        user_id=user_id,
        workflow={
            "flow_fqn": "pawflow.agents.group-deliberation:1.0.0",
            "flow_scope": "global",
            "input_port": "group_request",
            "terminal_port": "group_terminal",
            "preempt_policy": preempt_policy,
            "allowed_effects": ["resource.read"],
            "parameters": {"group_name": group.name},
            "limits": {
                "max_duration_seconds": group.budgets.timeout_seconds,
                "max_llm_calls": (
                    group.deliberation.max_total_participant_calls + 2),
                "max_flowfiles": 32,
                "max_fanout": group.deliberation.max_parallelism,
                "max_cost_usd": group.budgets.max_cost,
            },
        },
    )
    return {
        "group_name": group.name,
        "instance_name": instance_name,
        "binding": binding,
        "agent_config": config,
    }


__all__ = [
    "AGENT_GROUP_BINDINGS_KEY",
    "ResolvedAgentResource",
    "bind_agent_group",
    "bind_agent_group_instance",
    "get_bound_agent_group",
    "resolve_agent_group_resource",
    "resolve_agent_resource",
    "snapshot_bound_agent_group",
    "validate_agent_group_data",
]
