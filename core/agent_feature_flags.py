"""Dormant server-owned activation flags for the agent architecture rollout."""

from __future__ import annotations

import os

from pydantic import ConfigDict

from core.agent_contracts import ContractModel


class AgentFeatureFlags(ContractModel):
    """WP0 defaults; request payloads must never instantiate or override this."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workflow_agents_enabled: bool = False
    structured_effects_enabled: bool = False
    tool_authorization_v2_enabled: bool = False
    tool_lifecycle_v2_enabled: bool = False
    resource_bindings_v2_enabled: bool = False
    agent_run_registry_enabled: bool = False
    agent_groups_enabled: bool = False


DEFAULT_AGENT_FEATURE_FLAGS = AgentFeatureFlags()

BASE_AGENT_RUNTIME_KINDS = frozenset({"llm", "external_mcp", "external_agui"})
WORKFLOW_AGENT_RUNTIME_KIND = "workflow"
WORKFLOW_AGENTS_ENV = "PAWFLOW_WORKFLOW_AGENTS_ENABLED"
AGENT_GROUPS_ENV = "PAWFLOW_AGENT_GROUPS_ENABLED"
RESOURCE_BINDINGS_V2_ENV = "PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _server_bool(name: str, default: bool = False) -> bool:
    """Read one server-owned boolean without permissive truthiness."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be one of: 1, true, yes, on, 0, false, no, off")


def get_agent_feature_flags() -> AgentFeatureFlags:
    """Resolve process configuration; request data is deliberately absent."""
    return AgentFeatureFlags(
        workflow_agents_enabled=_server_bool(WORKFLOW_AGENTS_ENV),
        resource_bindings_v2_enabled=_server_bool(RESOURCE_BINDINGS_V2_ENV),
        agent_groups_enabled=_server_bool(AGENT_GROUPS_ENV),
    )


def workflow_agents_enabled() -> bool:
    return get_agent_feature_flags().workflow_agents_enabled


def agent_groups_enabled() -> bool:
    return get_agent_feature_flags().agent_groups_enabled


def resource_bindings_v2_enabled() -> bool:
    return get_agent_feature_flags().resource_bindings_v2_enabled


def allowed_agent_runtime_kinds() -> frozenset[str]:
    kinds = set(BASE_AGENT_RUNTIME_KINDS)
    if workflow_agents_enabled():
        kinds.add(WORKFLOW_AGENT_RUNTIME_KIND)
    return frozenset(kinds)


def validate_agent_runtime_kind(value: object) -> str:
    """Normalize one runtime kind and enforce the server capability gate."""
    runtime_kind = str(value or "llm").strip()
    if runtime_kind == WORKFLOW_AGENT_RUNTIME_KIND and not workflow_agents_enabled():
        raise ValueError("runtime_kind 'workflow' is disabled by the server")
    allowed = allowed_agent_runtime_kinds()
    if runtime_kind not in allowed:
        raise ValueError(
            "runtime_kind must be one of: " + ", ".join(sorted(allowed)))
    return runtime_kind
