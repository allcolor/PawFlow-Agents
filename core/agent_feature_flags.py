"""Supported agent runtime kinds."""

from __future__ import annotations

BASE_AGENT_RUNTIME_KINDS = frozenset({"llm", "external_mcp", "external_agui"})
WORKFLOW_AGENT_RUNTIME_KIND = "workflow"


def allowed_agent_runtime_kinds() -> frozenset[str]:
    return BASE_AGENT_RUNTIME_KINDS | {WORKFLOW_AGENT_RUNTIME_KIND}


def validate_agent_runtime_kind(value: object) -> str:
    """Normalize one runtime kind and reject unsupported values."""
    runtime_kind = str(value or "llm").strip()
    allowed = allowed_agent_runtime_kinds()
    if runtime_kind not in allowed:
        raise ValueError(
            "runtime_kind must be one of: " + ", ".join(sorted(allowed)))
    return runtime_kind
