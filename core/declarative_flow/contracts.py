"""Versioned semantic operation contracts."""

from __future__ import annotations

import re
from typing import Any

OPERATION_SCHEMA_VERSION = 1
SEMANTIC_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
SUPPORTED_OPERATIONS = frozenset({
    "add_processor", "update_processor", "remove_block", "connect_blocks",
    "disconnect_blocks", "set_executor_profile", "remove_executor_profile",
    "set_block_execution",
    "set_executor_defaults",
    "add_control_block",
})


def validate_operation(operation: Any) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise ValueError("operation must be an object")
    if operation.get("version") != OPERATION_SCHEMA_VERSION:
        raise ValueError(
            f"operation version must be {OPERATION_SCHEMA_VERSION}")
    name = str(operation.get("op") or "")
    if name not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported declarative operation '{name}'")
    return operation


def require_semantic_id(value: Any, field: str) -> str:
    result = str(value or "")
    if not SEMANTIC_ID_RE.fullmatch(result):
        raise ValueError(f"{field} must be a stable semantic identifier")
    return result


__all__ = [
    "OPERATION_SCHEMA_VERSION", "SEMANTIC_ID_RE", "SUPPORTED_OPERATIONS",
    "require_semantic_id", "validate_operation",
]
