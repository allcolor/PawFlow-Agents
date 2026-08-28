"""Validated execution authority for durable one-shot FlowRuns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import field_validator

from core import TaskFactory
from core.agent_contracts import (
    AuthorizationRefContract,
    CapabilityEffect,
    ContractModel,
    require_text,
)
from core.service_definition_revision import compute_service_definition_revision
from core.workflow_task_safety import workflow_task_metadata


class FlowExecutionAuthority(ContractModel):
    """Frozen capability and service ceiling accepted with one FlowRun."""

    agent_name: str
    permission_mode: str
    allowed_effects: tuple[CapabilityEffect, ...]
    service_snapshot: dict[str, Any]

    _agent_name = field_validator("agent_name")(
        lambda value: require_text(value, "agent_name"))
    _permission_mode = field_validator("permission_mode")(
        lambda value: require_text(value, "permission_mode"))

    @field_validator("allowed_effects")
    @classmethod
    def _effects_are_non_empty_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("allowed_effects must be non-empty and unique")
        return value

    @field_validator("service_snapshot")
    @classmethod
    def _snapshot_has_closed_core_shape(cls, value):
        if not isinstance(value, dict):
            raise TypeError("service_snapshot must be an object")
        if not isinstance(value.get("bindings"), dict):
            raise ValueError("service_snapshot.bindings must be an object")
        if not isinstance(value.get("services"), dict):
            raise ValueError("service_snapshot.services must be an object")
        return value


class FlowRunTaskAuthorizationContext(ContractModel):
    """Workflow-task authorization view reconstructed from one stored FlowRun."""

    run_id: str
    user_id: str
    conversation_id: str
    agent_name: str
    permission_mode: str
    authorization_ref: AuthorizationRefContract
    service_snapshot: dict[str, Any]

    @field_validator(
        "run_id", "user_id", "conversation_id", "agent_name", "permission_mode",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @property
    def root_turn_id(self) -> str:
        return self.authorization_ref.root_turn_id

    @classmethod
    def from_run(cls, run: dict[str, Any]):
        if not isinstance(run, dict):
            raise TypeError("flow run must be an object")
        authority = FlowExecutionAuthority.from_dict(
            run.get("execution_authority"))
        return cls(
            run_id=run.get("run_id"),
            user_id=run.get("user_id"),
            conversation_id=run.get("conversation_id"),
            agent_name=authority.agent_name,
            permission_mode=authority.permission_mode,
            authorization_ref=AuthorizationRefContract.from_dict(
                run.get("authorization_ref")),
            service_snapshot=authority.service_snapshot,
        )


def _iter_groups(value: Any):
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = ()
    for group in rows:
        if isinstance(group, dict):
            yield group


def _load_referenced_flow(flow_ref: dict[str, Any], seen_paths: set[Path]):
    path = Path(require_text(flow_ref.get("path"), "flow_ref.path")).resolve()
    if path in seen_paths:
        return None
    seen_paths.add(path)
    definition = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(definition, dict):
        raise ValueError("referenced flow must be an object")
    expected_version = str(flow_ref.get("version") or "")
    if expected_version and str(definition.get("version") or "") != expected_version:
        raise ValueError(
            f"referenced flow version mismatch for {path}: expected "
            f"{expected_version}, got {definition.get('version') or ''}")
    return definition


def _collect_effects(
    definition: dict[str, Any], effects: set[CapabilityEffect],
    seen_paths: set[Path],
) -> None:
    if not isinstance(definition, dict):
        raise TypeError("flow definition must be an object")

    for task_id, task in (definition.get("tasks") or {}).items():
        if not isinstance(task, dict):
            raise ValueError(f"flow task '{task_id}' must be an object")
        task_type = require_text(task.get("type"), f"task {task_id}.type")
        metadata = workflow_task_metadata(TaskFactory.get(task_type))
        effects.update(metadata.effects)
        if task_type == "executeFlow":
            parameters = task.get("parameters") or task.get("config") or {}
            if not isinstance(parameters, dict):
                raise ValueError(f"executeFlow task '{task_id}' parameters must be an object")
            child = _load_referenced_flow(
                {"path": parameters.get("flow_path")}, seen_paths)
            if child is not None:
                _collect_effects(child, effects, seen_paths)

    for group in _iter_groups(definition.get("groups")):
        flow_ref = group.get("flow_ref")
        if flow_ref:
            if not isinstance(flow_ref, dict):
                raise ValueError("group flow_ref must be an object")
            effects.update(workflow_task_metadata(
                TaskFactory.get("executeFlow")).effects)
            child = _load_referenced_flow(flow_ref, seen_paths)
            if child is not None:
                _collect_effects(child, effects, seen_paths)
            continue
        _collect_effects({
            "tasks": group.get("tasks") or {},
            "groups": group.get("child_groups") or group.get("groups") or {},
        }, effects, seen_paths)


def _snapshot_services(registry, user_id: str, conversation_id: str):
    definitions = registry.resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    services = {}
    for service_id in sorted(definitions):
        definition = definitions[service_id]
        canonical = str(getattr(definition, "service_id", "") or service_id)
        services[canonical] = {
            "service_id": canonical,
            "service_type": str(getattr(definition, "service_type", "") or ""),
            "scope": str(getattr(definition, "scope", "") or ""),
            "scope_id": str(getattr(definition, "scope_id", "") or ""),
            "definition_revision": compute_service_definition_revision(definition),
        }
    return {"bindings": {}, "services": services}


def build_flow_execution_authority(
    definition: dict[str, Any], *, user_id: str, conversation_id: str,
    agent_name: str, registry=None, relay_ids: tuple[str, ...] | None = None,
    permission_mode: str = "default",
) -> FlowExecutionAuthority:
    """Validate every executable task recursively and freeze its authority."""

    require_text(user_id, "user_id")
    require_text(conversation_id, "conversation_id")
    if registry is None:
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
    effects: set[CapabilityEffect] = set()
    _collect_effects(definition, effects, set())
    snapshot = _snapshot_services(registry, user_id, conversation_id)
    if relay_ids is None:
        from core.relay_bindings import get_linked
        relay_ids = tuple(get_linked(conversation_id, agent_name))
    candidates = tuple(dict.fromkeys(
        require_text(value, "relay_id") for value in relay_ids))
    if candidates:
        snapshot["relay"] = {"candidates": list(candidates)}
    return FlowExecutionAuthority(
        agent_name=agent_name,
        permission_mode=permission_mode,
        allowed_effects=tuple(sorted(effects, key=lambda item: item.value)),
        service_snapshot=snapshot,
    )


__all__ = [
    "FlowExecutionAuthority", "FlowRunTaskAuthorizationContext",
    "build_flow_execution_authority",
]
