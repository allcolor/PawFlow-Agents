"""Read-only declarative projection of canonical tasks and relations."""

from __future__ import annotations

import copy
from typing import Any

from core.declarative_flow.registry import DeclarativeBlockRegistry
from core.flow_definition_validator import normalize_relation
from core.flow_layout_contracts import resolve_executor_profile_ref

_SENSITIVE_NAMES = (
    "password", "secret", "credential", "api_key", "access_token",
    "refresh_token",
)


def _redacted(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        return {}
    return {
        str(key): (
            "[REDACTED]" if any(
                token in str(key).lower() for token in _SENSITIVE_NAMES)
            else copy.deepcopy(value)
        )
        for key, value in parameters.items()
    }


def _project_execution(
    execution: dict[str, Any], definition: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(execution)
    profiles = definition.get("executor_profiles") or {}
    defaults = definition.get("executor_defaults") or {}
    effective_roles = {}
    for role, binding in (result.get("roles") or {}).items():
        binding = binding if isinstance(binding, dict) else {}
        reference = binding.get("executor_profile")
        resolved = resolve_executor_profile_ref(reference, profiles, defaults)
        if resolved:
            profile_id, source = resolved
            profile = profiles.get(profile_id) or {}
            effective_roles[role] = {
                "profile_id": profile_id,
                "source": source,
                "kind": profile.get("kind"),
                "model": profile.get("model", ""),
                "service_ref": profile.get("service_ref", ""),
                "agent_ref": copy.deepcopy(profile.get("agent_ref")),
            }
        elif binding.get("kind"):
            effective_roles[role] = copy.deepcopy(binding)
        else:
            effective_roles[role] = {
                "profile_id": "", "source": "", "missing": True,
            }
    result["effective_roles"] = effective_roles
    return result


def project_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """Project root canonical tasks; never compile or mutate the definition."""
    if not isinstance(definition, dict):
        raise ValueError("definition must be an object")
    blocks = []
    endpoint_blocks: dict[str, tuple[str, str]] = {}
    for group_id, group in sorted((definition.get("groups") or {}).items()):
        if not isinstance(group, dict):
            continue
        declarative = group.get("declarative")
        if not isinstance(declarative, dict):
            continue
        ports = declarative.get("ports") or {}
        for name, task_id in (ports.get("inputs") or {}).items():
            endpoint_blocks[str(task_id)] = (str(group_id), str(name))
        for name, task_id in (ports.get("outputs") or {}).items():
            endpoint_blocks[str(task_id)] = (str(group_id), str(name))
        control_type = str(declarative.get("type") or "")
        blocks.append({
            "block_id": str(group_id),
            "descriptor": {
                "type": control_type,
                "version": int(declarative.get("version", 1)),
                "label": str(group.get("name") or control_type.title()),
                "category": (
                    "Agents and LLM"
                    if control_type == "workflow_agent" else "Control Flow"),
                "shape": "composite",
                "task_type": "", "config_schema": {},
                "inputs": list((ports.get("inputs") or {}).keys()),
                "outputs": list((ports.get("outputs") or {}).keys()),
                "lowering_version": int(declarative.get("lowering_version", 1)),
                "recognizer_version": 1,
                "requires_explicit_executor": False, "generic": False,
            },
            "config": _redacted(declarative.get("config")),
            "execution": _project_execution(
                group.get("execution") or {
                    "strategy": "single",
                    "roles": {"primary": {"kind": "pawflow"}},
                }, definition),
            "recognizable": True,
            "canonical_task_ids": sorted(map(str, (group.get("tasks") or {}))),
        })
    for task_id, task in sorted((definition.get("tasks") or {}).items()):
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("type") or "")
        try:
            descriptor = DeclarativeBlockRegistry.descriptor_for_task(
                task_type, task.get("parameters") or {})
        except (KeyError, ValueError):
            descriptor = {
                "type": f"processor:{task_type}",
                "version": 1,
                "label": task_type or "Unknown processor",
                "category": "Advanced Processors",
                "shape": "atomic",
                "task_type": task_type,
                "config_schema": {},
                "inputs": ["input"],
                "outputs": [],
                "lowering_version": 1,
                "recognizer_version": 1,
                "requires_explicit_executor": False,
                "generic": True,
            }
        execution = DeclarativeBlockRegistry.effective_executor(task)
        execution = _project_execution(execution, definition)
        blocks.append({
            "block_id": str(task_id),
            "descriptor": descriptor,
            "config": _redacted(task.get("parameters")),
            "execution": execution,
            "recognizable": bool(task_type),
            "canonical_task_ids": [str(task_id)],
        })
    relations = []
    for relation in definition.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        normalized = normalize_relation(relation)
        source_block, source_port = endpoint_blocks.get(
            normalized["from"], (normalized["from"], normalized["type"]))
        target_block, target_port = endpoint_blocks.get(
            normalized["to"], (normalized["to"], "input"))
        relations.append({
            "relation_id": str(relation.get("relation_id") or ""),
            "from": source_block,
            "to": target_block,
            "output": source_port,
            "input": target_port,
        })
    return {
        "schema_version": 1,
        "blocks": blocks,
        "relations": relations,
        "executor_profiles": copy.deepcopy(
            definition.get("executor_profiles") or {}),
        "executor_defaults": copy.deepcopy(
            definition.get("executor_defaults") or {}),
    }


__all__ = ["project_definition"]
