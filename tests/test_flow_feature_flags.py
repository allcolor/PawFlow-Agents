import pytest

from core.flow_feature_flags import (
    DECLARATIVE_WORKFLOWS_ENV,
    FLOW_RUNS_ENV,
    MULTI_VIEW_LAYOUTS_ENV,
    PLAN_MIGRATION_ENV,
    WORKFLOW_PROPOSALS_ENV,
    get_flow_feature_flags,
)
from core.tool_registry import create_default_registry


LEGACY_PLAN_TOOLS = {
    "create_plan", "update_plan", "approve_plan", "assign_plan",
    "cancel_plan", "delete_plan", "verify_plan_step",
}
WORKFLOW_PROPOSAL_TOOLS = {
    "propose_workflow", "get_workflow_proposal",
    "review_workflow_proposal",
}


def test_flow_feature_flags_default_off(monkeypatch):
    for name in (
        MULTI_VIEW_LAYOUTS_ENV, DECLARATIVE_WORKFLOWS_ENV,
        WORKFLOW_PROPOSALS_ENV, FLOW_RUNS_ENV, PLAN_MIGRATION_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    flags = get_flow_feature_flags()
    assert not any(flags.__dict__.values())


def test_flow_feature_flags_are_strict_server_values(monkeypatch):
    monkeypatch.setenv(WORKFLOW_PROPOSALS_ENV, "yes")
    assert get_flow_feature_flags().workflow_proposals_enabled is True
    monkeypatch.setenv(WORKFLOW_PROPOSALS_ENV, "sometimes")
    with pytest.raises(ValueError, match=WORKFLOW_PROPOSALS_ENV):
        get_flow_feature_flags()


def test_workflow_proposal_cutover_never_registers_two_plan_writers(
        monkeypatch):
    monkeypatch.delenv(WORKFLOW_PROPOSALS_ENV, raising=False)
    legacy = {
        value.name for value in create_default_registry().list_tools()}
    assert LEGACY_PLAN_TOOLS <= legacy
    assert WORKFLOW_PROPOSAL_TOOLS <= legacy

    monkeypatch.setenv(WORKFLOW_PROPOSALS_ENV, "true")
    canonical = {
        value.name for value in create_default_registry().list_tools()}
    assert not LEGACY_PLAN_TOOLS & canonical
    assert WORKFLOW_PROPOSAL_TOOLS <= canonical
