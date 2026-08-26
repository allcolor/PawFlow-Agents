from core.tool_registry import create_default_registry


LEGACY_PLAN_TOOLS = {
    "create_plan", "update_plan", "approve_plan", "assign_plan",
    "cancel_plan", "delete_plan", "verify_plan_step",
}
WORKFLOW_PROPOSAL_TOOLS = {
    "propose_workflow", "get_workflow_proposal",
    "review_workflow_proposal",
}


def test_workflow_proposals_are_the_only_plan_writer():
    canonical = {
        value.name for value in create_default_registry().list_tools()}
    assert not LEGACY_PLAN_TOOLS & canonical
    assert WORKFLOW_PROPOSAL_TOOLS <= canonical
