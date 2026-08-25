"""Dormant server-owned flags for declarative workflow rollout."""

from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})

MULTI_VIEW_LAYOUTS_ENV = "PAWFLOW_MULTI_VIEW_LAYOUTS_ENABLED"
DECLARATIVE_WORKFLOWS_ENV = "PAWFLOW_DECLARATIVE_WORKFLOWS_ENABLED"
WORKFLOW_PROPOSALS_ENV = "PAWFLOW_WORKFLOW_PROPOSALS_ENABLED"
FLOW_RUNS_ENV = "PAWFLOW_FLOW_RUNS_ENABLED"
PLAN_MIGRATION_ENV = "PAWFLOW_PLAN_MIGRATION_ENABLED"


def _server_bool(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be one of: 1, true, yes, on, 0, false, no, off")


@dataclass(frozen=True)
class FlowFeatureFlags:
    """Resolved process configuration; request data can never override it."""

    multi_view_layouts_enabled: bool = False
    declarative_workflows_enabled: bool = False
    workflow_proposals_enabled: bool = False
    flow_runs_enabled: bool = False
    plan_migration_enabled: bool = False


def get_flow_feature_flags() -> FlowFeatureFlags:
    return FlowFeatureFlags(
        multi_view_layouts_enabled=_server_bool(MULTI_VIEW_LAYOUTS_ENV),
        declarative_workflows_enabled=_server_bool(DECLARATIVE_WORKFLOWS_ENV),
        workflow_proposals_enabled=_server_bool(WORKFLOW_PROPOSALS_ENV),
        flow_runs_enabled=_server_bool(FLOW_RUNS_ENV),
        plan_migration_enabled=_server_bool(PLAN_MIGRATION_ENV),
    )


def multi_view_layouts_enabled() -> bool:
    return get_flow_feature_flags().multi_view_layouts_enabled


def declarative_workflows_enabled() -> bool:
    return get_flow_feature_flags().declarative_workflows_enabled


def workflow_proposals_enabled() -> bool:
    return get_flow_feature_flags().workflow_proposals_enabled


def flow_runs_enabled() -> bool:
    return get_flow_feature_flags().flow_runs_enabled


def plan_migration_enabled() -> bool:
    return get_flow_feature_flags().plan_migration_enabled
