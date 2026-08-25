"""Deterministic semantic mutations over canonical FlowDefinition."""

from __future__ import annotations

import copy
from typing import Any

from core.declarative_flow.contracts import (
    require_semantic_id,
    validate_operation,
)
from core.declarative_flow.macros import lower_control_block
from core.declarative_flow.registry import DeclarativeBlockRegistry
from core.declarative_flow.validation import find_cycle
from core.flow_layout_contracts import relation_id_seed, resolve_executor_profile_ref


def _tasks(definition: dict[str, Any]) -> dict[str, Any]:
    tasks = definition.setdefault("tasks", {})
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be an object")
    return tasks


def _relations(definition: dict[str, Any]) -> list[Any]:
    relations = definition.setdefault("relations", [])
    if not isinstance(relations, list):
        raise ValueError("relations must be a list")
    return relations


def _groups(definition: dict[str, Any]) -> dict[str, Any]:
    groups = definition.setdefault("groups", {})
    if not isinstance(groups, dict):
        raise ValueError("groups must be an object")
    return groups


def _group_ports(group: dict[str, Any], direction: str) -> dict[str, str]:
    declarative = group.get("declarative")
    if not isinstance(declarative, dict):
        return {}
    ports = declarative.get("ports")
    if not isinstance(ports, dict):
        return {}
    values = ports.get(direction)
    return values if isinstance(values, dict) else {}


def _endpoint(
    definition: dict[str, Any], block_id: str, direction: str, port_name: str,
) -> str:
    if block_id in _tasks(definition):
        return block_id
    group = _groups(definition).get(block_id)
    if not isinstance(group, dict):
        raise ValueError("connection endpoints must reference existing blocks")
    ports = _group_ports(group, "outputs" if direction == "from" else "inputs")
    requested = port_name or ("success" if direction == "from" else "input")
    if requested not in ports:
        raise ValueError(
            f"block '{block_id}' has no {direction} port '{requested}'")
    return str(ports[requested])


def _all_task_ids(definition: dict[str, Any]) -> set[str]:
    result = set(map(str, _tasks(definition)))
    pending = list(_groups(definition).values())
    while pending:
        group = pending.pop()
        if not isinstance(group, dict):
            continue
        result.update(map(str, (group.get("tasks") or {})))
        children = group.get("child_groups") or {}
        if isinstance(children, dict):
            pending.extend(children.values())
    return result


def _all_relations(definition: dict[str, Any]) -> list[dict[str, Any]]:
    result = [
        item for item in _relations(definition) if isinstance(item, dict)]
    pending = list(_groups(definition).values())
    while pending:
        group = pending.pop()
        if not isinstance(group, dict):
            continue
        result.extend(
            item for item in (group.get("relations") or {})
            if isinstance(item, dict))
        children = group.get("child_groups") or {}
        if isinstance(children, dict):
            pending.extend(children.values())
    return result


def _unique_relation_id(
    definition: dict[str, Any], relation: dict[str, Any],
) -> str:
    existing = {
        str(item.get("relation_id") or "")
        for item in _relations(definition) if isinstance(item, dict)
    }
    requested = str(relation.get("relation_id") or "")
    base = requested or relation_id_seed(relation)
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _resolve_workflow_agent_config(
    definition: dict[str, Any], config: Any,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    result = copy.deepcopy(config)
    profile_ref = result.get("executor_profile")
    if profile_ref is None:
        return result
    profiles = definition.get("executor_profiles") or {}
    defaults = definition.get("executor_defaults") or {}
    resolved = resolve_executor_profile_ref(profile_ref, profiles, defaults)
    if resolved is None:
        raise ValueError("workflow_agent executor_profile does not resolve")
    profile_id, _source = resolved
    profile = profiles.get(profile_id) or {}
    kind = profile.get("kind")
    if kind != "workflow_agent":
        if kind == "agent":
            raise ValueError(
                "general agent executor profiles are unavailable before WP9")
        raise ValueError(
            "workflow_agent requires a workflow_agent executor profile")
    agent_ref = profile.get("agent_ref")
    if result.get("agent_ref") not in (None, agent_ref):
        raise ValueError("workflow_agent agent_ref conflicts with executor_profile")
    result["executor_profile"] = profile_id
    result["agent_ref"] = copy.deepcopy(agent_ref)
    limits = profile.get("limits") or {}
    duration = limits.get("max_duration_seconds")
    if duration is not None and "terminal_timeout" not in result:
        result["terminal_timeout"] = f"{duration}s"
    return result


def apply_operation(
    definition: dict[str, Any], operation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one validated operation and return copy plus change summary."""
    if not isinstance(definition, dict):
        raise ValueError("definition must be an object")
    operation = validate_operation(operation)
    result = copy.deepcopy(definition)
    name = operation["op"]
    changed: list[str] = []
    tasks = _tasks(result)

    if name == "add_control_block":
        block_id = require_semantic_id(operation.get("block_id"), "block_id")
        groups = _groups(result)
        if block_id in tasks or block_id in groups:
            raise ValueError(f"block '{block_id}' already exists")
        control_type = operation.get("control_type")
        config = operation.get("config", {})
        if control_type == "workflow_agent":
            config = _resolve_workflow_agent_config(result, config)
        group = lower_control_block(block_id, control_type, config)
        collisions = set(group["tasks"]) & _all_task_ids(result)
        if collisions:
            raise ValueError(
                f"generated task ids already exist: {sorted(collisions)}")
        groups[block_id] = group
        changed.extend([block_id, *group["tasks"]])
    elif name == "add_processor":
        block_id = require_semantic_id(operation.get("block_id"), "block_id")
        if block_id in tasks:
            raise ValueError(f"block '{block_id}' already exists")
        descriptor = DeclarativeBlockRegistry.by_block_type(
            str(operation.get("block_type") or ""))
        parameters = operation.get("config", {})
        if not isinstance(parameters, dict):
            raise ValueError("config must be an object")
        task = {
            "type": descriptor["task_type"],
            "parameters": copy.deepcopy(parameters),
        }
        if operation.get("execution") is not None:
            if not isinstance(operation["execution"], dict):
                raise ValueError("execution must be an object")
            task["execution"] = copy.deepcopy(operation["execution"])
        tasks[block_id] = task
        changed.append(block_id)
        position = operation.get("position")
        layout_id = str(operation.get("layout_id") or "")
        if position is not None:
            layouts = result.get("layouts")
            if not layout_id or not isinstance(layouts, dict) or layout_id not in layouts:
                raise ValueError("layout_id must reference an existing layout")
            if not isinstance(position, dict):
                raise ValueError("position must be an object")
            layouts[layout_id].setdefault("nodes", {})[block_id] = copy.deepcopy(
                position)
    elif name == "update_processor":
        block_id = require_semantic_id(operation.get("block_id"), "block_id")
        if block_id not in tasks or not isinstance(tasks[block_id], dict):
            raise KeyError(block_id)
        if "config" in operation:
            if not isinstance(operation["config"], dict):
                raise ValueError("config must be an object")
            tasks[block_id]["parameters"] = copy.deepcopy(operation["config"])
        if "execution" in operation:
            if not isinstance(operation["execution"], dict):
                raise ValueError("execution must be an object")
            tasks[block_id]["execution"] = copy.deepcopy(operation["execution"])
        changed.append(block_id)
    elif name == "remove_block":
        block_id = require_semantic_id(operation.get("block_id"), "block_id")
        groups = _groups(result)
        removed_ids = {block_id}
        if block_id in tasks:
            del tasks[block_id]
        elif block_id in groups and isinstance(groups[block_id], dict):
            removed_ids.update(map(str, (groups[block_id].get("tasks") or {})))
            del groups[block_id]
        else:
            raise KeyError(block_id)
        result["relations"] = [
            relation for relation in _relations(result)
            if not isinstance(relation, dict)
            or (
                str(relation.get("from", relation.get("source", ""))) not in removed_ids
                and str(relation.get("to", relation.get("target", ""))) not in removed_ids
            )
        ]
        for field in ("entries", "exits"):
            result[field] = [
                item for item in result.get(field, []) if str(item) != block_id]
        for layout in (result.get("layouts") or {}).values():
            if isinstance(layout, dict) and isinstance(layout.get("nodes"), dict):
                layout["nodes"].pop(block_id, None)
        changed.append(block_id)
    elif name == "connect_blocks":
        source = require_semantic_id(operation.get("from"), "from")
        target = require_semantic_id(operation.get("to"), "to")
        relationship = str(operation.get("output") or "success")
        if not relationship:
            raise ValueError("output is required")
        source_endpoint = _endpoint(result, source, "from", relationship)
        target_endpoint = _endpoint(
            result, target, "to", str(operation.get("input") or "input"))
        relation = {
            "from": source_endpoint, "to": target_endpoint, "type": relationship}
        if operation.get("relation_id"):
            relation["relation_id"] = require_semantic_id(
                operation["relation_id"], "relation_id")
        relation["relation_id"] = _unique_relation_id(result, relation)
        cycle = find_cycle(
            _all_task_ids(result), [*_all_relations(result), relation])
        if cycle:
            raise ValueError(
                "declarative connections must remain acyclic: "
                + " -> ".join(cycle))
        _relations(result).append(relation)
        changed.append(relation["relation_id"])
    elif name == "disconnect_blocks":
        relation_id = require_semantic_id(
            operation.get("relation_id"), "relation_id")
        before = len(_relations(result))
        result["relations"] = [
            relation for relation in _relations(result)
            if not isinstance(relation, dict)
            or str(relation.get("relation_id") or "") != relation_id
        ]
        if len(result["relations"]) == before:
            raise KeyError(relation_id)
        for layout in (result.get("layouts") or {}).values():
            if isinstance(layout, dict) and isinstance(
                layout.get("relations"), dict
            ):
                layout["relations"].pop(relation_id, None)
        changed.append(relation_id)
    elif name == "set_executor_profile":
        profile_id = require_semantic_id(
            operation.get("profile_id"), "profile_id")
        profile = operation.get("profile")
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        profile = copy.deepcopy(profile)
        profile["id"] = profile_id
        result.setdefault("executor_profiles", {})[profile_id] = profile
        changed.append(profile_id)
    elif name == "set_executor_defaults":
        defaults = operation.get("defaults")
        if not isinstance(defaults, dict):
            raise ValueError("defaults must be an object")
        result["executor_defaults"] = copy.deepcopy(defaults)
        changed.extend(sorted(map(str, defaults)))
    elif name == "remove_executor_profile":
        profile_id = require_semantic_id(
            operation.get("profile_id"), "profile_id")
        profiles = result.get("executor_profiles")
        if not isinstance(profiles, dict) or profile_id not in profiles:
            raise KeyError(profile_id)
        for task in tasks.values():
            roles = (
                task.get("execution", {}).get("roles", {})
                if isinstance(task, dict) else {})
            if any(
                isinstance(binding, dict)
                and binding.get("executor_profile") == profile_id
                for binding in roles.values()
            ):
                raise ValueError(
                    f"executor profile '{profile_id}' is still referenced")
        del profiles[profile_id]
        changed.append(profile_id)
    elif name == "set_block_execution":
        block_id = require_semantic_id(operation.get("block_id"), "block_id")
        if block_id not in tasks or not isinstance(tasks[block_id], dict):
            raise KeyError(block_id)
        execution = operation.get("execution")
        if not isinstance(execution, dict):
            raise ValueError("execution must be an object")
        tasks[block_id]["execution"] = copy.deepcopy(execution)
        changed.append(block_id)
    return result, {
        "operation_version": 1,
        "operation": name,
        "changed_entity_ids": changed,
    }


__all__ = ["apply_operation"]
