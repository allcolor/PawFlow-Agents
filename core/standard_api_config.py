"""Configuration authority for published OpenAI and Anthropic-compatible APIs.

This module owns the typed publication contract shared by the owner action,
HTTP admission, and UI capability view. Dialect availability is intentionally
false until the corresponding implementation phase passes its gate.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Mapping


DIALECT_AVAILABILITY: Dict[str, bool] = {
    "chat_completions": True,
    "responses": True,
    "anthropic_messages": True,
}

DIALECT_FIELD_NAMES = {
    "chat_completions": "api_chat_completions_enabled",
    "responses": "api_responses_enabled",
    "anthropic_messages": "api_anthropic_messages_enabled",
}

STANDARD_API_FIELDS = (
    "standard_api_enabled",
    "api_model_id",
    "api_permission_mode",
    "api_session_ttl_seconds",
    "api_max_sessions_per_key",
    "api_max_concurrent_runs_per_key",
    "strict_fields",
    "api_request_overrides_json",
    "api_input_modalities_json",
    "api_chat_completions_enabled",
    "api_responses_enabled",
    "api_anthropic_messages_enabled",
    "api_disconnect_policy",
)

BOOLEAN_FIELDS = frozenset({
    "standard_api_enabled",
    "strict_fields",
    *DIALECT_FIELD_NAMES.values(),
})

INTEGER_BOUNDS = {
    "api_session_ttl_seconds": (60, 2_592_000),
    "api_max_sessions_per_key": (1, 1_000),
    "api_max_concurrent_runs_per_key": (1, 32),
}

PERMISSION_MODES = ("read_only", "default")
INPUT_MODALITIES = ("text",)
DISCONNECT_POLICIES = ("cancel", "finish_detached")
MODEL_ID_MAX_LENGTH = 128
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

# Populated by later dialect phases. Keeping the policy closed here prevents a
# forged owner action from enabling a control the running build cannot honor.
REQUEST_OVERRIDE_FIELDS: Dict[str, Dict[str, Any]] = {}

_DEFAULTS: Dict[str, Any] = {
    "standard_api_enabled": False,
    "api_model_id": "",
    "api_permission_mode": "",
    "api_session_ttl_seconds": 0,
    "api_max_sessions_per_key": 0,
    "api_max_concurrent_runs_per_key": 0,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": [],
    "api_chat_completions_enabled": False,
    "api_responses_enabled": False,
    "api_anthropic_messages_enabled": False,
    "api_disconnect_policy": "",
}

_SAFE_SUGGESTIONS = {
    "api_permission_mode": "read_only",
    "api_session_ttl_seconds": 86_400,
    "api_max_sessions_per_key": 100,
    "api_max_concurrent_runs_per_key": 4,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": ["text"],
    "api_disconnect_policy": "cancel",
}


def default_standard_api_config() -> Dict[str, Any]:
    """Return a fresh disabled publication configuration."""

    return copy.deepcopy(_DEFAULTS)


def get_standard_api_capabilities() -> Dict[str, Any]:
    """Return the safe, content-free capability contract exposed to owners."""

    return {
        "dialects": dict(DIALECT_AVAILABILITY),
        "permission_modes": list(PERMISSION_MODES),
        "modalities": list(INPUT_MODALITIES),
        "disconnect_policies": list(DISCONNECT_POLICIES),
        "request_override_fields": copy.deepcopy(REQUEST_OVERRIDE_FIELDS),
        "bounds": {
            "api_model_id_max_length": MODEL_ID_MAX_LENGTH,
            **{
                field: {"min": bounds[0], "max": bounds[1]}
                for field, bounds in INTEGER_BOUNDS.items()
            },
        },
        "suggestions": copy.deepcopy(_SAFE_SUGGESTIONS),
    }


def _validate_boolean(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _validate_integer(field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value == 0:
        return value
    minimum, maximum = INTEGER_BOUNDS[field]
    if value < minimum or value > maximum:
        raise ValueError(
            f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_model_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("api_model_id must be a string")
    model_id = value.strip()
    if model_id and not _MODEL_ID_RE.fullmatch(model_id):
        raise ValueError(
            "api_model_id must be an opaque token of at most "
            f"{MODEL_ID_MAX_LENGTH} characters")
    return model_id


def _validate_permission_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("api_permission_mode must be a string")
    normalized = value.strip()
    if normalized and normalized not in PERMISSION_MODES:
        raise ValueError(
            "api_permission_mode must be 'read_only' or 'default'")
    return normalized


def _validate_modalities(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("api_input_modalities_json must be an array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(
            "api_input_modalities_json entries must be strings")
    normalized = [item.strip().lower() for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError(
            "api_input_modalities_json must not contain duplicates")
    unsupported = sorted(set(normalized) - set(INPUT_MODALITIES))
    if unsupported:
        raise ValueError(
            "Unsupported API input modalities: " + ", ".join(unsupported))
    if normalized and "text" not in normalized:
        raise ValueError("api_input_modalities_json must include 'text'")
    return normalized


def _validate_overrides(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("api_request_overrides_json must be an object")
    unsupported = sorted(set(value) - set(REQUEST_OVERRIDE_FIELDS))
    if unsupported:
        raise ValueError(
            "Unsupported API request override fields: " + ", ".join(unsupported))
    # A JSON round trip both copies the value and rejects non-serializable data.
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"))
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "api_request_overrides_json must contain finite JSON values") from exc


def _validate_disconnect_policy(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("api_disconnect_policy must be a string")
    normalized = value.strip().lower()
    if normalized and normalized not in DISCONNECT_POLICIES:
        raise ValueError(
            "api_disconnect_policy must be 'cancel' or 'finish_detached'")
    return normalized


def _validated_updates(updates: Mapping[str, Any]) -> Dict[str, Any]:
    unknown = sorted(set(updates) - set(STANDARD_API_FIELDS))
    if unknown:
        raise ValueError(
            "Unknown standard API fields: " + ", ".join(unknown))

    result: Dict[str, Any] = {}
    for field, value in updates.items():
        if field in BOOLEAN_FIELDS:
            result[field] = _validate_boolean(field, value)
        elif field in INTEGER_BOUNDS:
            result[field] = _validate_integer(field, value)
        elif field == "api_model_id":
            result[field] = _validate_model_id(value)
        elif field == "api_permission_mode":
            result[field] = _validate_permission_mode(value)
        elif field == "api_input_modalities_json":
            result[field] = _validate_modalities(value)
        elif field == "api_request_overrides_json":
            result[field] = _validate_overrides(value)
        elif field == "api_disconnect_policy":
            result[field] = _validate_disconnect_policy(value)
    return result


def normalize_standard_api_update(
        existing: Mapping[str, Any],
        updates: Mapping[str, Any],
        *,
        context_policy: str,
) -> Dict[str, Any]:
    """Merge and atomically validate one standard API configuration update."""

    if not isinstance(updates, Mapping):
        raise ValueError("standard_api_config must be an object")
    normalized_updates = _validated_updates(updates)
    candidate = default_standard_api_config()
    for field in STANDARD_API_FIELDS:
        if field in existing:
            candidate[field] = copy.deepcopy(existing[field])
    candidate.update(normalized_updates)

    for dialect, field in DIALECT_FIELD_NAMES.items():
        if candidate[field] and not DIALECT_AVAILABILITY[dialect]:
            raise ValueError(
                f"{dialect} is not available in this PawFlow build")

    explicitly_enabling = updates.get("standard_api_enabled") is True
    if explicitly_enabling:
        missing = [field for field in STANDARD_API_FIELDS
                   if field not in updates]
        if missing:
            raise ValueError(
                "Enabling requires the complete standard API fieldset; "
                "missing: " + ", ".join(missing))

    if candidate["standard_api_enabled"]:
        if context_policy != "isolated":
            raise ValueError(
                "standard API export requires context_policy='isolated'")
        if not candidate["api_model_id"]:
            raise ValueError("api_model_id is required when standard API is enabled")
        if not candidate["api_permission_mode"]:
            raise ValueError(
                "api_permission_mode is required when standard API is enabled")
        for field in INTEGER_BOUNDS:
            if not candidate[field]:
                raise ValueError(
                    f"{field} is required when standard API is enabled")
        if candidate["api_input_modalities_json"] != ["text"]:
            raise ValueError(
                "api_input_modalities_json must include the supported 'text' modality")
        if not candidate["api_disconnect_policy"]:
            raise ValueError(
                "api_disconnect_policy is required when standard API is enabled")
        if not any(candidate[field] for field in DIALECT_FIELD_NAMES.values()):
            raise ValueError(
                "At least one available standard API dialect must be enabled")

    return candidate


def standard_api_material_changed(
        before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Return whether session identity must move to a new generation."""

    return any(before.get(field) != after.get(field)
               for field in STANDARD_API_FIELDS)


def standard_api_runtime_summary(
        publication: Mapping[str, Any],
        *,
        live_key_count: int = 0,
        session_count: int = 0,
        active_run_count: int = 0,
        draining_generations: list[int] | None = None,
) -> Dict[str, Any]:
    """Build the content-free owner runtime view model."""

    deleting = bool(publication.get("delete_requested_at"))
    globally_enabled = bool(publication.get("enabled")) and not deleting
    standard_enabled = (
        globally_enabled
        and publication.get("context_policy") == "isolated"
        and bool(publication.get("standard_api_enabled"))
    )
    dialects = {
        dialect: bool(
            standard_enabled
            and DIALECT_AVAILABILITY[dialect]
            and publication.get(field)
        )
        for dialect, field in DIALECT_FIELD_NAMES.items()
    }
    return {
        "deleting": deleting,
        "publication_enabled": globally_enabled,
        "standard_api_enabled": standard_enabled,
        "dialects": dialects,
        "api_generation": int(publication.get("api_generation") or 0),
        "live_key_count": int(live_key_count),
        "session_count": int(session_count),
        "active_run_count": int(active_run_count),
        "draining_generations": list(draining_generations or []),
    }
