"""Pure contracts and one-shot helpers for versioned flow presentation."""

from __future__ import annotations

import copy
import hashlib
import math
import re
from typing import Any, Iterable


LAYOUT_SCHEMA_VERSION = 1
LAYOUT_KINDS = frozenset({"technical", "declarative", "operations", "custom"})
ROUTING_MODES = frozenset({"auto", "bezier", "smoothstep", "straight"})
FLOW_DIRECTIONS = frozenset({"LR", "RL", "TB", "BT"})
EXECUTOR_KINDS = frozenset(
    {"pawflow", "llm", "agent", "workflow_agent", "human"})
EXECUTION_STRATEGIES = frozenset(
    {"single", "sequence", "parallel", "primary_then_review"})
VALIDATION_CRITERION_KINDS = frozenset({
    "semantic", "expression", "json_schema", "artifact",
})

_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?$")
_UNSAFE_CONTENT_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|link|meta)|"
    r"javascript\s*:|on[a-z]+\s*=",
    re.IGNORECASE,
)
_SECRET_KEY_RE = re.compile(
    r"(?:credential|password|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token)",
    re.IGNORECASE,
)

_NODE_STYLE = frozenset({
    "fill", "border", "text", "accent", "border_width", "border_style",
    "opacity",
})
_RELATION_STYLE = frozenset({
    "stroke", "stroke_width", "stroke_style", "animated", "arrow", "opacity",
})
_PRESENTATION_STYLE = frozenset({
    "fill", "border", "text", "border_width", "border_style", "opacity",
})
_COLOR_FIELDS = frozenset({"fill", "border", "text", "accent", "stroke"})
_BORDER_STYLES = frozenset({"solid", "dashed", "dotted"})
_ARROWS = frozenset({"none", "open", "closed"})


def _problem(code: str, message: str, *, field: str = "",
             entity_type: str = "flow", entity_id: str = "") -> dict[str, str]:
    return {
        "severity": "error", "code": code, "message": message,
        "entity_type": entity_type, "entity_id": entity_id, "field": field,
    }


def _normalize_relation(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(relation.get("from", relation.get("source", "")) or ""),
        str(relation.get("type", relation.get("relationship", "success")) or "success"),
        str(relation.get("to", relation.get("target", "")) or ""),
    )


def relation_id_seed(relation: dict[str, Any]) -> str:
    """Stable legacy migration identity independent of list order."""
    source, relationship, target = _normalize_relation(relation)
    raw = f"{source}\x1f{relationship}\x1f{target}".encode("utf-8")
    return "rel_" + hashlib.sha256(raw).hexdigest()[:16]


def _relation_containers(definition: dict[str, Any]) -> Iterable[list[Any]]:
    relations = definition.get("relations")
    if isinstance(relations, list):
        yield relations
    groups = definition.get("groups")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        if not isinstance(group, dict) or group.get("flow_ref"):
            continue
        yield from _relation_containers(group)


def ensure_relation_ids(definition: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic, unique IDs on every relation."""
    result = copy.deepcopy(definition)
    used: set[str] = set()
    for relations in _relation_containers(result):
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            existing = str(relation.get("relation_id") or "")
            if existing and existing not in used:
                used.add(existing)
                continue
            base = relation_id_seed(relation)
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            relation["relation_id"] = candidate
            used.add(candidate)
    return result


def migrate_legacy_presentation(
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Convert one legacy layout and relation set without mutating input."""
    result = ensure_relation_ids(definition)
    if "layouts" in result:
        return result
    legacy = result.pop("layout", {})
    if not isinstance(legacy, dict):
        legacy = {}
    technical = copy.deepcopy(legacy)
    technical.update({
        "id": "technical",
        "name": str(technical.get("name") or "Technical"),
        "kind": "technical",
        "root_group_id": str(technical.get("root_group_id") or ""),
        "viewport": copy.deepcopy(
            technical.get("viewport") or {"x": 0, "y": 0, "zoom": 1}),
        "direction": str(technical.get("direction") or "LR"),
        "nodes": copy.deepcopy(technical.get("nodes") or {}),
        "relations": copy.deepcopy(technical.get("relations") or {}),
        "annotations": copy.deepcopy(technical.get("annotations") or {}),
        "frames": copy.deepcopy(technical.get("frames") or {}),
        "visibility": copy.deepcopy(technical.get("visibility") or {}),
    })
    result["layout_schema_version"] = LAYOUT_SCHEMA_VERSION
    result["default_layout_id"] = "technical"
    result["layouts"] = {"technical": technical}
    return result


_STAGE_PRESENTATION = {
    "inputs": {
        "label": "Inputs & triggers",
        "description": "Receives requests and starts scheduled work.",
        "fill": "#e0f2fe", "border": "#0284c7", "accent": "#0ea5e9",
    },
    "routing": {
        "label": "Validation & routing",
        "description": "Validates inputs, authenticates callers, and selects paths.",
        "fill": "#fef3c7", "border": "#d97706", "accent": "#f59e0b",
    },
    "processing": {
        "label": "Core processing",
        "description": "Performs the flow's main transformation or agent work.",
        "fill": "#ede9fe", "border": "#7c3aed", "accent": "#8b5cf6",
    },
    "outputs": {
        "label": "Delivery & outputs",
        "description": "Publishes results and completes external responses.",
        "fill": "#dcfce7", "border": "#16a34a", "accent": "#22c55e",
    },
}


def _presentation_stage(task_id: str, task_type: str) -> str:
    value = f"{task_id} {task_type}".lower()
    if any(token in value for token in (
        "receiver", "trigger", "inputport", "webhook", "receive", "http_in",
        "turn_in", "request",
    )):
        return "inputs"
    if any(token in value for token in (
        "validate", "verify", "auth", "route", "decide", "filter",
        "only_", "prepare",
    )):
        return "routing"
    if any(token in value for token in (
        "response", "send", "outputport", "publish", "complete", "terminal",
        "finalize", "reply", "redirect",
    )):
        return "outputs"
    return "processing"


def _human_label(identifier: str) -> str:
    words = re.sub(r"[-_.:]+", " ", str(identifier)).strip().split()
    return " ".join(word.upper() if len(word) <= 3 else word.capitalize()
                    for word in words) or "Task"


def _task_description(label: str, task_type: str, stage: str) -> str:
    action = {
        "inputs": "Receives or initiates",
        "routing": "Validates and routes",
        "processing": "Processes",
        "outputs": "Delivers",
    }[stage]
    return f"{action} the {label.lower()} step using {task_type}."


def normalize_flow_presentation(
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic functional presentation for a legacy flow.

    Existing versioned layouts are preserved. Missing task labels and
    descriptions, however, are filled for every flow so static and live viewers
    expose the same human-readable metadata.
    """
    result = ensure_relation_ids(definition)
    tasks = result.get("tasks")
    if not isinstance(tasks, dict):
        tasks = {}
    stages: dict[str, list[str]] = {key: [] for key in _STAGE_PRESENTATION}
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("type") or "task")
        stage = _presentation_stage(str(task_id), task_type)
        stages[stage].append(str(task_id))
        label = str(task.get("label") or "").strip() or _human_label(str(task_id))
        task["label"] = label
        if not str(task.get("description") or "").strip():
            task["description"] = _task_description(label, task_type, stage)

    if isinstance(result.get("layouts"), dict) and result["layouts"]:
        return result

    result.pop("layout", None)
    nodes: dict[str, Any] = {}
    frames: dict[str, Any] = {}
    visible_stages = [key for key, members in stages.items() if members]
    for column, stage in enumerate(visible_stages):
        members = stages[stage]
        spec = _STAGE_PRESENTATION[stage]
        x = column * 360
        for row, task_id in enumerate(members):
            nodes[task_id] = {
                "x": x, "y": row * 150, "width": 240, "height": 96,
                "style": {
                    "fill": spec["fill"], "border": spec["border"],
                    "text": "#111827", "accent": spec["accent"],
                    "border_width": 2, "border_style": "solid", "opacity": 1,
                },
            }
        frames[f"stage_{stage}"] = {
            "id": f"stage_{stage}", "label": spec["label"],
            "description": spec["description"],
            "x": x - 32, "y": -72, "width": 304,
            "height": max(210, len(members) * 150 + 64),
            "member_ids": members,
            "style": {
                "fill": spec["fill"], "border": spec["border"],
                "text": "#111827", "border_width": 2,
                "border_style": "solid", "opacity": 0.18,
            },
        }
    relation_styles = {}
    for relation in result.get("relations") or []:
        if not isinstance(relation, dict) or not relation.get("relation_id"):
            continue
        relation_styles[str(relation["relation_id"])] = {
            "routing": "smoothstep", "label_t": 0.5,
            "style": {
                "stroke": "#64748b", "stroke_width": 2,
                "stroke_style": "solid", "animated": False,
                "arrow": "closed", "opacity": 0.9,
            },
        }
    result["layout_schema_version"] = LAYOUT_SCHEMA_VERSION
    result["default_layout_id"] = "functional"
    result["layouts"] = {
        "functional": {
            "id": "functional",
            "name": f"{str(result.get('name') or result.get('id') or 'Flow')} functional stages",
            "kind": "declarative",
            "root_group_id": "",
            "viewport": {"x": 40, "y": 40, "zoom": 0.75},
            "direction": "LR",
            "nodes": nodes,
            "relations": relation_styles,
            "annotations": {},
            "frames": frames,
            "visibility": {},
        },
    }
    return result


def _all_node_ids(definition: dict[str, Any]) -> set[str]:
    result = set(map(str, (definition.get("tasks") or {}).keys()))
    groups = definition.get("groups") or {}
    if isinstance(groups, dict):
        for group_id, group in groups.items():
            result.add(str(group_id))
            if isinstance(group, dict) and not group.get("flow_ref"):
                result.update(_all_node_ids(group))
    return result


def _relation_ids(definition: dict[str, Any]) -> tuple[set[str], list[dict[str, str]]]:
    result: set[str] = set()
    problems: list[dict[str, str]] = []
    for relations in _relation_containers(definition):
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            relation_id = str(relation.get("relation_id") or "")
            if not relation_id:
                problems.append(_problem(
                    "missing_relation_id", "Every relation requires relation_id",
                    entity_type="relation"))
            elif not _ID_RE.fullmatch(relation_id):
                problems.append(_problem(
                    "invalid_relation_id",
                    f"Relation ID '{relation_id}' is invalid",
                    entity_type="relation", entity_id=relation_id,
                    field="relation_id"))
            elif relation_id in result:
                problems.append(_problem(
                    "duplicate_relation_id",
                    f"Relation ID '{relation_id}' is declared more than once",
                    entity_type="relation", entity_id=relation_id,
                    field="relation_id"))
            result.add(relation_id)
    return result, problems


def _finite(value: Any, *, minimum: float | None = None,
            maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    if minimum is not None and value < minimum:
        return False
    return maximum is None or value <= maximum


def _validate_style(
    style: Any, allowed: frozenset[str], *, entity_type: str,
    entity_id: str,
) -> list[dict[str, str]]:
    if style is None:
        return []
    if not isinstance(style, dict):
        return [_problem(
            "invalid_style", "Style must be an object",
            entity_type=entity_type, entity_id=entity_id, field="style")]
    problems = []
    for key, value in style.items():
        if key not in allowed:
            problems.append(_problem(
                "invalid_style_token", f"Unsupported style token '{key}'",
                entity_type=entity_type, entity_id=entity_id, field=f"style.{key}"))
        elif key in _COLOR_FIELDS and (
            not isinstance(value, str) or not _COLOR_RE.fullmatch(value)
        ):
            problems.append(_problem(
                "invalid_style_color", f"Invalid color for '{key}'",
                entity_type=entity_type, entity_id=entity_id, field=f"style.{key}"))
        elif key == "opacity" and not _finite(value, minimum=0, maximum=1):
            problems.append(_problem(
                "invalid_style_value", "Opacity must be between 0 and 1",
                entity_type=entity_type, entity_id=entity_id, field="style.opacity"))
        elif key in {"border_width", "stroke_width"} and not _finite(
            value, minimum=0, maximum=20
        ):
            problems.append(_problem(
                "invalid_style_value", f"{key} must be between 0 and 20",
                entity_type=entity_type, entity_id=entity_id, field=f"style.{key}"))
        elif key in {"border_style", "stroke_style"} and value not in _BORDER_STYLES:
            problems.append(_problem(
                "invalid_style_value", f"Invalid {key}",
                entity_type=entity_type, entity_id=entity_id, field=f"style.{key}"))
        elif key == "arrow" and value not in _ARROWS:
            problems.append(_problem(
                "invalid_style_value", "Invalid relation arrow",
                entity_type=entity_type, entity_id=entity_id, field="style.arrow"))
        elif key == "animated" and not isinstance(value, bool):
            problems.append(_problem(
                "invalid_style_value", "animated must be a boolean",
                entity_type=entity_type, entity_id=entity_id,
                field="style.animated"))
    return problems


def _validate_geometry(
    item: dict[str, Any], *, entity_type: str, entity_id: str,
    require_size: bool = False,
) -> list[dict[str, str]]:
    problems = []
    for field in ("x", "y"):
        if not _finite(item.get(field), minimum=-1_000_000, maximum=1_000_000):
            problems.append(_problem(
                "non_finite_geometry",
                f"{entity_type} '{entity_id}' has invalid {field}",
                entity_type=entity_type, entity_id=entity_id, field=field))
    for field in ("width", "height"):
        if field in item or require_size:
            if not _finite(item.get(field), minimum=1, maximum=100_000):
                problems.append(_problem(
                    "invalid_geometry",
                    f"{entity_type} '{entity_id}' has invalid {field}",
                    entity_type=entity_type, entity_id=entity_id, field=field))
    if "z_index" in item and (
        isinstance(item["z_index"], bool)
        or not isinstance(item["z_index"], int)
        or not -100_000 <= item["z_index"] <= 100_000
    ):
        problems.append(_problem(
            "invalid_geometry", "z_index must be a bounded integer",
            entity_type=entity_type, entity_id=entity_id, field="z_index"))
    return problems


def validate_flow_presentation(
    definition: dict[str, Any], *, require_relation_ids: bool = False,
) -> list[dict[str, str]]:
    """Validate presentation without resolving services or expressions."""
    problems: list[dict[str, str]] = []
    layouts = definition.get("layouts")
    uses_v1 = layouts is not None or definition.get("layout_schema_version") is not None
    relation_ids, relation_problems = _relation_ids(definition)
    if require_relation_ids or uses_v1:
        problems.extend(relation_problems)
    if not uses_v1:
        return problems
    if definition.get("layout_schema_version") != LAYOUT_SCHEMA_VERSION:
        problems.append(_problem(
            "unsupported_layout_schema_version",
            f"layout_schema_version must be {LAYOUT_SCHEMA_VERSION}",
            field="layout_schema_version"))
    if not isinstance(layouts, dict) or not layouts:
        problems.append(_problem(
            "invalid_layouts", "layouts must be a non-empty object",
            field="layouts"))
        return problems
    default_id = str(definition.get("default_layout_id") or "")
    if default_id not in layouts:
        problems.append(_problem(
            "missing_default_layout",
            "default_layout_id must reference an existing layout",
            field="default_layout_id"))
    node_ids = _all_node_ids(definition)
    for layout_id, layout in layouts.items():
        layout_id = str(layout_id)
        if not _ID_RE.fullmatch(layout_id):
            problems.append(_problem(
                "invalid_layout_id", f"Layout ID '{layout_id}' is invalid",
                entity_type="layout", entity_id=layout_id))
        if not isinstance(layout, dict):
            problems.append(_problem(
                "invalid_layout", f"Layout '{layout_id}' must be an object",
                entity_type="layout", entity_id=layout_id))
            continue
        if layout.get("id") != layout_id:
            problems.append(_problem(
                "layout_id_mismatch",
                f"Layout key '{layout_id}' does not match its id",
                entity_type="layout", entity_id=layout_id, field="id"))
        if not str(layout.get("name") or "").strip():
            problems.append(_problem(
                "missing_layout_name", f"Layout '{layout_id}' needs a name",
                entity_type="layout", entity_id=layout_id, field="name"))
        if layout.get("kind") not in LAYOUT_KINDS:
            problems.append(_problem(
                "invalid_layout_kind", f"Layout '{layout_id}' has invalid kind",
                entity_type="layout", entity_id=layout_id, field="kind"))
        if layout.get("direction", "LR") not in FLOW_DIRECTIONS:
            problems.append(_problem(
                "invalid_layout_direction", "Invalid layout direction",
                entity_type="layout", entity_id=layout_id, field="direction"))
        viewport = layout.get("viewport")
        if not isinstance(viewport, dict) or any(
            not _finite(viewport.get(key), minimum=(-1_000_000 if key != "zoom" else 0.05),
                        maximum=(1_000_000 if key != "zoom" else 8))
            for key in ("x", "y", "zoom")
        ):
            problems.append(_problem(
                "invalid_viewport", f"Layout '{layout_id}' has invalid viewport",
                entity_type="layout", entity_id=layout_id, field="viewport"))
        nodes = layout.get("nodes", {})
        if not isinstance(nodes, dict):
            problems.append(_problem(
                "invalid_layout_nodes", "Layout nodes must be an object",
                entity_type="layout", entity_id=layout_id, field="nodes"))
            nodes = {}
        for node_id, geometry in nodes.items():
            node_id = str(node_id)
            if node_id not in node_ids:
                problems.append(_problem(
                    "unknown_layout_node",
                    f"Layout references unknown node '{node_id}'",
                    entity_type="node", entity_id=node_id))
            if not isinstance(geometry, dict):
                problems.append(_problem(
                    "invalid_geometry", "Node geometry must be an object",
                    entity_type="node", entity_id=node_id))
                continue
            problems.extend(_validate_geometry(
                geometry, entity_type="node", entity_id=node_id))
            problems.extend(_validate_style(
                geometry.get("style"), _NODE_STYLE,
                entity_type="node", entity_id=node_id))
        routes = layout.get("relations", {})
        if not isinstance(routes, dict):
            problems.append(_problem(
                "invalid_layout_relations", "Layout relations must be an object",
                entity_type="layout", entity_id=layout_id, field="relations"))
            routes = {}
        for relation_id, route in routes.items():
            relation_id = str(relation_id)
            if relation_id not in relation_ids:
                problems.append(_problem(
                    "unknown_layout_relation",
                    f"Layout references unknown relation '{relation_id}'",
                    entity_type="relation", entity_id=relation_id))
            if not isinstance(route, dict):
                problems.append(_problem(
                    "invalid_relation_route", "Relation route must be an object",
                    entity_type="relation", entity_id=relation_id))
                continue
            if route.get("routing", "auto") not in ROUTING_MODES:
                problems.append(_problem(
                    "invalid_relation_route", "Invalid relation routing mode",
                    entity_type="relation", entity_id=relation_id,
                    field="routing"))
            for field in ("source_control", "target_control", "label_offset"):
                point = route.get(field)
                if point is not None and (
                    not isinstance(point, dict)
                    or not _finite(point.get("dx", point.get("x")),
                                   minimum=-1_000_000, maximum=1_000_000)
                    or not _finite(point.get("dy", point.get("y")),
                                   minimum=-1_000_000, maximum=1_000_000)
                ):
                    problems.append(_problem(
                        "invalid_control_point", f"Invalid {field}",
                        entity_type="relation", entity_id=relation_id,
                        field=field))
            if "label_t" in route and not _finite(
                route["label_t"], minimum=0, maximum=1
            ):
                problems.append(_problem(
                    "invalid_relation_label", "label_t must be between 0 and 1",
                    entity_type="relation", entity_id=relation_id,
                    field="label_t"))
            problems.extend(_validate_style(
                route.get("style"), _RELATION_STYLE,
                entity_type="relation", entity_id=relation_id))
        for field, entity_type in (
            ("annotations", "annotation"), ("frames", "frame")
        ):
            values = layout.get(field, {})
            if not isinstance(values, dict):
                problems.append(_problem(
                    f"invalid_{field}", f"{field} must be an object",
                    entity_type="layout", entity_id=layout_id, field=field))
                continue
            for object_id, item in values.items():
                object_id = str(object_id)
                if not isinstance(item, dict) or item.get("id") != object_id:
                    problems.append(_problem(
                        f"invalid_{entity_type}",
                        f"{entity_type} '{object_id}' must have matching id",
                        entity_type=entity_type, entity_id=object_id))
                    continue
                problems.extend(_validate_geometry(
                    item, entity_type=entity_type, entity_id=object_id,
                    require_size=True))
                problems.extend(_validate_style(
                    item.get("style"), _PRESENTATION_STYLE,
                    entity_type=entity_type, entity_id=object_id))
                if entity_type == "annotation":
                    content = str(item.get("content") or "")
                    if len(content) > 20_000 or _UNSAFE_CONTENT_RE.search(content):
                        problems.append(_problem(
                            "unsafe_annotation_content",
                            "Annotation content is unsafe or too large",
                            entity_type=entity_type, entity_id=object_id,
                            field="content"))
                else:
                    members = item.get("member_ids", [])
                    if not isinstance(members, list):
                        problems.append(_problem(
                            "invalid_frame_members",
                            "Frame member_ids must be a list",
                            entity_type=entity_type, entity_id=object_id,
                            field="member_ids"))
                    else:
                        for member_id in members:
                            if str(member_id) not in node_ids:
                                problems.append(_problem(
                                    "frame_member_missing",
                                    f"Frame member '{member_id}' does not exist",
                                    entity_type=entity_type,
                                    entity_id=object_id, field="member_ids"))
    return problems


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _SECRET_KEY_RE.search(str(key)) or _contains_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def resolve_executor_profile_ref(
    profile_ref: Any, profiles: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[str, str] | None:
    """Resolve one direct or explicit inherited profile reference."""
    value = str(profile_ref or "")
    if value.startswith("inherited:"):
        role = value.split(":", 1)[1]
        resolved = defaults.get(role)
        if not isinstance(resolved, str) or not resolved:
            return None
        return resolved, f"executor_defaults.{role}"
    if value in profiles:
        return value, f"executor_profiles.{value}"
    return None


def validate_executor_profiles(
    definition: dict[str, Any],
) -> list[dict[str, str]]:
    profiles = definition.get("executor_profiles")
    if profiles is None:
        return []
    if not isinstance(profiles, dict):
        return [_problem(
            "invalid_executor_profiles",
            "executor_profiles must be an object",
            field="executor_profiles")]
    problems: list[dict[str, str]] = []
    for profile_id, profile in profiles.items():
        profile_id = str(profile_id)
        if not _ID_RE.fullmatch(profile_id) or not isinstance(profile, dict):
            problems.append(_problem(
                "invalid_executor_profile",
                f"Executor profile '{profile_id}' is invalid",
                entity_type="executor_profile", entity_id=profile_id))
            continue
        if profile.get("id") != profile_id:
            problems.append(_problem(
                "executor_profile_id_mismatch",
                f"Executor profile key '{profile_id}' does not match its id",
                entity_type="executor_profile", entity_id=profile_id, field="id"))
        kind = profile.get("kind")
        if kind not in EXECUTOR_KINDS:
            problems.append(_problem(
                "invalid_executor_kind",
                f"Executor profile '{profile_id}' has invalid kind",
                entity_type="executor_profile", entity_id=profile_id, field="kind"))
        if _contains_secret_key(profile):
            problems.append(_problem(
                "executor_profile_contains_secret",
                "Executor profiles may contain references, never credentials",
                entity_type="executor_profile", entity_id=profile_id))
        if kind == "llm":
            for field in ("service_ref", "model", "context_policy"):
                if not str(profile.get(field) or "").strip():
                    problems.append(_problem(
                        "missing_executor_binding",
                        f"LLM executor '{profile_id}' requires {field}",
                        entity_type="executor_profile", entity_id=profile_id,
                        field=field))
        elif kind in {"agent", "workflow_agent"}:
            ref = profile.get("agent_ref")
            if not isinstance(ref, dict) or any(
                not str(ref.get(field) or "").strip()
                for field in ("scope", "name", "version", "content_digest")
            ):
                problems.append(_problem(
                    "invalid_agent_executor_ref",
                    f"Executor '{profile_id}' requires an exact agent_ref",
                    entity_type="executor_profile", entity_id=profile_id,
                    field="agent_ref"))
        elif kind == "human" and not str(profile.get("role") or "").strip():
            problems.append(_problem(
                "missing_executor_binding",
                f"Human executor '{profile_id}' requires role",
                entity_type="executor_profile", entity_id=profile_id, field="role"))
        limits = profile.get("limits", {})
        if not isinstance(limits, dict):
            problems.append(_problem(
                "invalid_executor_limits", "Executor limits must be an object",
                entity_type="executor_profile", entity_id=profile_id,
                field="limits"))
        else:
            for name, value in limits.items():
                if not _finite(value, minimum=0):
                    problems.append(_problem(
                        "invalid_executor_limit",
                        f"Executor limit '{name}' must be finite and non-negative",
                        entity_type="executor_profile", entity_id=profile_id,
                        field=f"limits.{name}"))
    defaults = definition.get("executor_defaults", {})
    if not isinstance(defaults, dict):
        problems.append(_problem(
            "invalid_executor_defaults",
            "executor_defaults must be an object",
            field="executor_defaults"))
        defaults = {}
    for role, profile_id in defaults.items():
        if not _ID_RE.fullmatch(str(role)) or profile_id not in profiles:
            problems.append(_problem(
                "unknown_executor_default",
                f"Executor default '{role}' must reference one profile",
                entity_type="executor_default", entity_id=str(role),
                field=f"executor_defaults.{role}"))
    for container_name in ("tasks", "groups"):
        container = definition.get(container_name, {})
        if not isinstance(container, dict):
            continue
        for block_id, block in container.items():
            if not isinstance(block, dict) or "execution" not in block:
                continue
            execution = block.get("execution")
            if not isinstance(execution, dict):
                problems.append(_problem(
                    "invalid_block_execution", "execution must be an object",
                    entity_type="block", entity_id=str(block_id), field="execution"))
                continue
            strategy = execution.get("strategy")
            if strategy not in EXECUTION_STRATEGIES:
                problems.append(_problem(
                    "invalid_execution_strategy",
                    f"Block '{block_id}' has invalid execution strategy",
                    entity_type="block", entity_id=str(block_id),
                    field="execution.strategy"))
            roles = execution.get("roles")
            required_roles = {
                "single": {"primary"},
                "sequence": {"primary"},
                "parallel": {"primary"},
                "primary_then_review": {"primary", "reviewer"},
            }.get(strategy, set())
            if not isinstance(roles, dict) or not required_roles.issubset(roles):
                problems.append(_problem(
                    "missing_execution_role",
                    f"Block '{block_id}' is missing required execution roles",
                    entity_type="block", entity_id=str(block_id),
                    field="execution.roles"))
                continue
            for role, binding in roles.items():
                profile_ref = (
                    binding.get("executor_profile")
                    if isinstance(binding, dict) else None)
                resolved = resolve_executor_profile_ref(
                    profile_ref, profiles, defaults)
                if resolved is None:
                    problems.append(_problem(
                        "unknown_executor_profile",
                        f"Role '{role}' does not resolve to one executor profile",
                        entity_type="block", entity_id=str(block_id),
                        field=f"execution.roles.{role}"))
                elif role == "reviewer" and profiles[resolved[0]].get(
                    "kind"
                ) not in {"llm", "agent", "workflow_agent"}:
                    problems.append(_problem(
                        "incompatible_reviewer_profile",
                        "Reviewer must be an LLM, agent, or Workflow Agent",
                        entity_type="block", entity_id=str(block_id),
                        field=f"execution.roles.{role}"))
            if strategy == "primary_then_review":
                policy = execution.get("review_policy")
                if not isinstance(policy, dict) or not isinstance(
                    policy.get("max_revisions"), int
                ) or not 1 <= policy["max_revisions"] <= 20:
                    problems.append(_problem(
                        "invalid_review_bound",
                        "primary_then_review requires max_revisions between 1 and 20",
                        entity_type="block", entity_id=str(block_id),
                        field="execution.review_policy.max_revisions"))
                elif (
                    policy.get("on_reject") != "redo_primary_with_review"
                    or not str(policy.get("feedback_input") or "").strip()
                    or not str(policy.get("result_input") or "").strip()
                ):
                    problems.append(_problem(
                        "invalid_review_policy",
                        "Review rejection must redo primary with explicit "
                        "feedback and result inputs",
                        entity_type="block", entity_id=str(block_id),
                        field="execution.review_policy"))
                criteria = execution.get("validation_criteria")
                if not isinstance(criteria, list) or not criteria:
                    problems.append(_problem(
                        "missing_validation_criteria",
                        "A reviewed step requires validation_criteria",
                        entity_type="block", entity_id=str(block_id),
                        field="execution.validation_criteria"))
                    continue
                if len(criteria) > 50:
                    problems.append(_problem(
                        "too_many_validation_criteria",
                        "A step may declare at most 50 validation criteria",
                        entity_type="block", entity_id=str(block_id),
                        field="execution.validation_criteria"))
                seen_criteria: set[str] = set()
                for index, criterion in enumerate(criteria):
                    field = f"execution.validation_criteria.{index}"
                    if not isinstance(criterion, dict):
                        problems.append(_problem(
                            "invalid_validation_criterion",
                            "Validation criterion must be an object",
                            entity_type="block", entity_id=str(block_id),
                            field=field))
                        continue
                    criterion_id = str(criterion.get("id") or "")
                    kind = criterion.get("kind")
                    description = str(criterion.get("description") or "")
                    if (
                        not _ID_RE.fullmatch(criterion_id)
                        or criterion_id in seen_criteria
                    ):
                        problems.append(_problem(
                            "invalid_validation_criterion_id",
                            "Validation criterion IDs must be stable and unique",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.id"))
                    seen_criteria.add(criterion_id)
                    if kind not in VALIDATION_CRITERION_KINDS:
                        problems.append(_problem(
                            "invalid_validation_criterion_kind",
                            f"Validation criterion '{criterion_id}' has invalid kind",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.kind"))
                    if not description.strip() or len(description) > 4000:
                        problems.append(_problem(
                            "invalid_validation_criterion_description",
                            "Criterion description is required and bounded",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.description"))
                    if not isinstance(criterion.get("required"), bool):
                        problems.append(_problem(
                            "invalid_validation_criterion_required",
                            "Criterion required must be a boolean",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.required"))
                    if kind == "expression" and not str(
                        criterion.get("expression") or ""
                    ).strip():
                        problems.append(_problem(
                            "missing_validation_expression",
                            "Expression criterion requires expression",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.expression"))
                    if kind == "json_schema" and not isinstance(
                        criterion.get("schema"), dict
                    ):
                        problems.append(_problem(
                            "missing_validation_schema",
                            "JSON Schema criterion requires schema",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.schema"))
                    if kind == "artifact" and not str(
                        criterion.get("artifact") or ""
                    ).strip():
                        problems.append(_problem(
                            "missing_validation_artifact",
                            "Artifact criterion requires artifact selector",
                            entity_type="block", entity_id=str(block_id),
                            field=f"{field}.artifact"))
    return problems


__all__ = [
    "EXECUTION_STRATEGIES", "EXECUTOR_KINDS", "LAYOUT_KINDS",
    "LAYOUT_SCHEMA_VERSION", "ROUTING_MODES", "ensure_relation_ids",
    "migrate_legacy_presentation", "relation_id_seed",
    "resolve_executor_profile_ref", "validate_executor_profiles",
    "validate_flow_presentation", "VALIDATION_CRITERION_KINDS",
]
