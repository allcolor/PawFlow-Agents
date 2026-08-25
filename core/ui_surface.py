"""Portable interactive UI surface contracts.

A UI surface is durable semantic state plus optional rich presentation. Clients
select a presentation from declared capabilities; they never infer completion or
execute an action that the server did not declare.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

FORMAT = "pawflow.ui-surface.v1"
STATUSES = {"open", "waiting_for_compatible_client", "resolved", "cancelled"}
FALLBACK_MODES = {"semantic", "handoff"}
ACTION_KINDS = {"default", "primary", "success", "danger"}
FIELD_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_COMPONENT_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]{1,120}[a-z0-9]:"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_MAX_BYTES = 256 * 1024


class UiSurfaceValidationError(ValueError):
    """A surface does not satisfy the portable UI contract."""


def _required_text(value: Any, name: str, limit: int = 2048) -> str:
    result = str(value or "").strip()
    if not result:
        raise UiSurfaceValidationError(f"{name} is required")
    if len(result) > limit:
        raise UiSurfaceValidationError(f"{name} exceeds {limit} characters")
    return result


def _identifier(value: Any, name: str) -> str:
    result = _required_text(value, name, 128)
    if not _ID_RE.fullmatch(result):
        raise UiSurfaceValidationError(f"{name} is invalid")
    return result


def _capabilities(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise UiSurfaceValidationError(f"{name} must be an array")
    result: list[str] = []
    for raw in value:
        capability = str(raw or "").strip()
        if not _CAPABILITY_RE.fullmatch(capability):
            raise UiSurfaceValidationError(
                f"{name} contains an invalid capability")
        if capability not in result:
            result.append(capability)
    return result


def _json_copy(value: Any, name: str) -> Any:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise UiSurfaceValidationError(
            f"{name} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise UiSurfaceValidationError(f"{name} exceeds {_MAX_BYTES} bytes")
    return json.loads(encoded)


def _validate_input_schema(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {"type": "object", "properties": {}}
    if not isinstance(value, dict) or value.get("type", "object") != "object":
        raise UiSurfaceValidationError(f"{name} must be an object schema")
    allowed = {"type", "properties", "required", "additionalProperties"}
    if set(value) - allowed:
        raise UiSurfaceValidationError(f"{name} contains unsupported keywords")
    properties = value.get("properties", {})
    if not isinstance(properties, dict):
        raise UiSurfaceValidationError(f"{name}.properties must be an object")
    clean_properties: dict[str, Any] = {}
    for key, spec in properties.items():
        field_id = _identifier(key, f"{name}.properties key")
        if not isinstance(spec, dict):
            raise UiSurfaceValidationError(
                f"{name}.properties.{field_id} must be an object")
        field_type = str(spec.get("type") or "")
        if field_type not in FIELD_TYPES:
            raise UiSurfaceValidationError(
                f"{name}.properties.{field_id}.type is invalid")
        clean = {"type": field_type}
        for optional in ("title", "description", "default"):
            if optional in spec:
                clean[optional] = _json_copy(
                    spec[optional], f"{name}.properties.{field_id}.{optional}")
        if "enum" in spec:
            if not isinstance(spec["enum"], list) or not spec["enum"]:
                raise UiSurfaceValidationError(
                    f"{name}.properties.{field_id}.enum must be a non-empty array")
            clean["enum"] = _json_copy(
                spec["enum"], f"{name}.properties.{field_id}.enum")
        clean_properties[field_id] = clean
    required = value.get("required", [])
    if not isinstance(required, list) or any(
        item not in clean_properties for item in required
    ):
        raise UiSurfaceValidationError(
            f"{name}.required must reference declared properties")
    return {
        "type": "object",
        "properties": clean_properties,
        "required": list(dict.fromkeys(required)),
        "additionalProperties": False,
    }


def _validate_dispatch(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UiSurfaceValidationError(f"{name} is required")
    action = _identifier(value.get("action"), f"{name}.action")
    extension = str(value.get("extension") or "").strip()
    if extension and not _CAPABILITY_RE.fullmatch(extension):
        raise UiSurfaceValidationError(f"{name}.extension is invalid")
    arguments = value.get("arguments", {})
    if not isinstance(arguments, dict):
        raise UiSurfaceValidationError(f"{name}.arguments must be an object")
    result = {"action": action, "arguments": _json_copy(arguments, name)}
    if extension:
        result["extension"] = extension
    return result


def _validate_semantic(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UiSurfaceValidationError("semantic is required")
    role = _identifier(value.get("role"), "semantic.role")
    title = _required_text(value.get("title"), "semantic.title", 256)
    result: dict[str, Any] = {"role": role, "title": title}
    for key in ("summary", "body"):
        if key in value:
            result[key] = str(value[key] or "")[:16384]
    fields = value.get("fields", [])
    if not isinstance(fields, list):
        raise UiSurfaceValidationError("semantic.fields must be an array")
    clean_fields = []
    seen_fields: set[str] = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            raise UiSurfaceValidationError(
                f"semantic.fields[{index}] must be an object")
        field_id = _identifier(field.get("id"), f"semantic.fields[{index}].id")
        if field_id in seen_fields:
            raise UiSurfaceValidationError(f"duplicate semantic field: {field_id}")
        seen_fields.add(field_id)
        field_type = str(field.get("type") or "")
        if field_type not in FIELD_TYPES:
            raise UiSurfaceValidationError(
                f"semantic.fields[{index}].type is invalid")
        clean = {
            "id": field_id,
            "type": field_type,
            "label": _required_text(
                field.get("label"), f"semantic.fields[{index}].label", 256),
            "required": field.get("required") is True,
        }
        for key in ("value", "placeholder", "description", "options"):
            if key in field:
                clean[key] = _json_copy(
                    field[key], f"semantic.fields[{index}].{key}")
        clean_fields.append(clean)
    result["fields"] = clean_fields

    actions = value.get("actions", [])
    if not isinstance(actions, list):
        raise UiSurfaceValidationError("semantic.actions must be an array")
    clean_actions = []
    seen_actions: set[str] = set()
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise UiSurfaceValidationError(
                f"semantic.actions[{index}] must be an object")
        action_id = _identifier(
            action.get("id"), f"semantic.actions[{index}].id")
        if action_id in seen_actions:
            raise UiSurfaceValidationError(f"duplicate semantic action: {action_id}")
        seen_actions.add(action_id)
        kind = str(action.get("kind") or "default")
        if kind not in ACTION_KINDS:
            raise UiSurfaceValidationError(
                f"semantic.actions[{index}].kind is invalid")
        clean = {
            "id": action_id,
            "label": _required_text(
                action.get("label"), f"semantic.actions[{index}].label", 256),
            "kind": kind,
            "input_schema": _validate_input_schema(
                action.get("input_schema"),
                f"semantic.actions[{index}].input_schema"),
            "dispatch": _validate_dispatch(
                action.get("dispatch"),
                f"semantic.actions[{index}].dispatch"),
            "requires": _capabilities(
                action.get("requires"), f"semantic.actions[{index}].requires"),
            "terminal": action.get("terminal") is True,
        }
        if action.get("confirm"):
            clean["confirm"] = str(action["confirm"])[:1024]
        if action.get("handoff"):
            if not isinstance(action["handoff"], dict):
                raise UiSurfaceValidationError(
                    f"semantic.actions[{index}].handoff must be an object")
            clean["handoff"] = _json_copy(
                action["handoff"], f"semantic.actions[{index}].handoff")
        clean_actions.append(clean)
    result["actions"] = clean_actions
    return result


def validate_ui_surface(surface: Any) -> dict[str, Any]:
    """Validate and return a canonical detached UI-surface document."""
    if not isinstance(surface, dict):
        raise UiSurfaceValidationError("surface must be an object")
    if str(surface.get("format") or "") != FORMAT:
        raise UiSurfaceValidationError(f"format must be {FORMAT}")
    surface_id = _identifier(surface.get("surface_id"), "surface_id")
    revision = surface.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise UiSurfaceValidationError("revision must be an integer >= 1")
    status = str(surface.get("status") or "")
    if status not in STATUSES:
        raise UiSurfaceValidationError("status is invalid")
    producer = surface.get("producer")
    if not isinstance(producer, dict):
        raise UiSurfaceValidationError("producer is required")
    clean_producer = {
        "kind": _identifier(producer.get("kind"), "producer.kind"),
        "id": _identifier(producer.get("id"), "producer.id"),
    }
    result: dict[str, Any] = {
        "format": FORMAT,
        "surface_id": surface_id,
        "revision": revision,
        "user_id": _required_text(surface.get("user_id"), "user_id", 256),
        "conversation_id": _required_text(
            surface.get("conversation_id"), "conversation_id", 256),
        "status": status,
        "producer": clean_producer,
        "semantic": _validate_semantic(surface.get("semantic")),
        "required_capabilities": _capabilities(
            surface.get("required_capabilities"), "required_capabilities"),
    }
    presentation = surface.get("presentation")
    if presentation is not None:
        if not isinstance(presentation, dict):
            raise UiSurfaceValidationError("presentation must be an object")
        component = str(presentation.get("component") or "").strip()
        if component and not _COMPONENT_RE.fullmatch(component):
            raise UiSurfaceValidationError("presentation.component is invalid")
        result["presentation"] = {
            "component": component,
            "props": _json_copy(presentation.get("props", {}), "presentation.props"),
            "requires": _capabilities(
                presentation.get("requires", ["ui.component"]),
                "presentation.requires"),
        }
    fallback = surface.get("fallback", {"mode": "semantic"})
    if not isinstance(fallback, dict):
        raise UiSurfaceValidationError("fallback must be an object")
    mode = str(fallback.get("mode") or "")
    if mode not in FALLBACK_MODES:
        raise UiSurfaceValidationError("fallback.mode is invalid")
    clean_fallback = {"mode": mode}
    if fallback.get("message"):
        clean_fallback["message"] = str(fallback["message"])[:2048]
    if fallback.get("uri"):
        clean_fallback["uri"] = str(fallback["uri"])[:4096]
    if mode == "handoff" and not clean_fallback.get("message"):
        raise UiSurfaceValidationError("handoff fallback requires a message")
    result["fallback"] = clean_fallback
    for key in ("created_at", "updated_at"):
        if surface.get(key):
            result[key] = str(surface[key])
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise UiSurfaceValidationError(f"surface exceeds {_MAX_BYTES} bytes")
    return result


def make_ui_surface(
    *, user_id: str, conversation_id: str, producer_kind: str,
    producer_id: str, semantic: dict[str, Any], revision: int = 1,
    surface_id: str = "", status: str = "open",
    required_capabilities: list[str] | None = None,
    presentation: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a timestamped, UUID-addressed surface and validate it."""
    now = datetime.now(timezone.utc).isoformat()
    document: dict[str, Any] = {
        "format": FORMAT,
        "surface_id": surface_id or f"uis_{uuid.uuid4()}",
        "revision": revision,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "status": status,
        "producer": {"kind": producer_kind, "id": producer_id},
        "semantic": copy.deepcopy(semantic),
        "required_capabilities": required_capabilities or [],
        "fallback": fallback or {"mode": "semantic"},
        "created_at": now,
        "updated_at": now,
    }
    if presentation is not None:
        document["presentation"] = copy.deepcopy(presentation)
    return validate_ui_surface(document)


def select_ui_surface_mode(
    surface: dict[str, Any], client_capabilities: set[str] | list[str],
) -> str:
    """Return rich, semantic, or handoff for one validated client view."""
    clean = validate_ui_surface(surface)
    capabilities = set(client_capabilities)
    required = set(clean["required_capabilities"])
    if not required.issubset(capabilities):
        return "handoff"
    presentation = clean.get("presentation") or {}
    if presentation.get("component") and set(
        presentation.get("requires") or []
    ).issubset(capabilities):
        return "rich"
    if clean["fallback"]["mode"] == "semantic":
        return "semantic"
    return "handoff"


def available_ui_surface_actions(
    surface: dict[str, Any], client_capabilities: set[str] | list[str],
) -> list[dict[str, Any]]:
    """Return actions executable by this client; unavailable actions stay visible."""
    clean = validate_ui_surface(surface)
    capabilities = set(client_capabilities)
    result = []
    for action in clean["semantic"]["actions"]:
        row = copy.deepcopy(action)
        missing = sorted(set(row["requires"]) - capabilities)
        row["available"] = not missing
        row["missing_capabilities"] = missing
        result.append(row)
    return result
