"""WP0 contract tests for workflow agents, collaboration, and tool safety."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.agent_contracts import CapabilityMetadata
from core.agent_group_contracts import AgentGroupDefinition, ParticipantPost
from core.agent_run_contracts import AgentRunSummary
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import AssignedSkill, ResourceOrigin, ResourceRef
from core.tool_authorization_contracts import ApprovalRequest, ToolGrant
from core.tool_execution_context import ToolExecutionContext
from core.tool_lifecycle import ToolLifecycleEvent, validate_tool_lifecycle_transition
from core.workflow_agent_contracts import (
    AgentInboxItem,
    AgentWorkflowDefinition,
    AgentWorkflowRequest,
    AgentWorkflowResult,
    PreparedAgentTurn,
    WorkflowRunContext,
    WorkflowRunError,
    WorkflowRunEvent,
    WorkflowRunRecord,
    validate_workflow_run_transition,
)

NOW = "2026-08-24T08:00:00Z"
LATER = "2026-08-24T08:05:00+00:00"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
AUTH_ID = "0f1e2d3c-4b5a-4698-8071-625344332211"
EVENT_ID = "11111111-2222-4333-8444-555555555555"


def identity_dict(**overrides):
    value = {
        "schema_version": 1,
        "conversation_id": "conv-1",
        "root_conversation_id": "conv-1",
        "agent_instance": "Wiki",
        "turn_id": "web:turn-1",
        "ingress_msg_id": "web:turn-1",
        "turn_epoch": 3,
        "run_generation": 4,
        "authorization_context_id": AUTH_ID,
        "authorization_revision_at_start": 7,
        "source_kind": "user",
        "source_id": "alice",
        "created_at": NOW,
    }
    value.update(overrides)
    return value


def auth_ref(**overrides):
    value = {"context_id": AUTH_ID, "revision": 7, "root_turn_id": "web:turn-1"}
    value.update(overrides)
    return value


def flow_ref(**overrides):
    value = {
        "schema_version": 1,
        "resource_type": "flow",
        "name": "pawflow.agents.wiki:1.0.0",
        "scope": "global",
        "owner_id": None,
        "package_id": "pawflow.agents",
        "package_version": "1.0.0",
        "version": "1.0.0",
        "content_digest": DIGEST,
        "source_id": "flow:wiki:1.0.0",
    }
    value.update(overrides)
    return value


def agent_ref(name: str, source_id: str):
    return {
        "schema_version": 1,
        "resource_type": "agent",
        "name": name,
        "scope": "global",
        "owner_id": None,
        "package_id": None,
        "package_version": None,
        "version": "1.0.0",
        "content_digest": DIGEST,
        "source_id": source_id,
    }


def test_agent_architecture_has_no_rollout_feature_flags():
    import core.agent_feature_flags as runtime_kinds

    assert not any(name.endswith("_ENV") for name in vars(runtime_kinds))
    assert not hasattr(runtime_kinds, "get_agent_feature_flags")


@pytest.mark.parametrize("change", [
    {"schema_version": 2},
    {"turn_id": ""},
    {"turn_epoch": 0},
    {"run_generation": True},
    {"authorization_context_id": "not-a-uuid"},
    {"created_at": "2026-08-24T08:00:00"},
    {"source_kind": "model"},
])
def test_turn_identity_fails_closed(change):
    with pytest.raises(ValueError):
        AgentTurnIdentity.from_dict(identity_dict(**change))


def test_turn_identity_is_immutable_and_round_trips():
    identity = AgentTurnIdentity.from_dict(identity_dict())
    assert AgentTurnIdentity.from_dict(identity.to_dict()) == identity
    with pytest.raises((TypeError, ValueError)):
        identity.turn_epoch = 9
    with pytest.raises(ValueError):
        AgentTurnIdentity.from_dict(identity_dict(unexpected=True))


def test_capability_metadata_rejects_unknown_and_contradictory_effects():
    valid = CapabilityMetadata.from_dict({
        "schema_version": 1,
        "effects": ["filesystem.read", "resource.read"],
        "read_only": True,
        "destructive": False,
        "idempotency": "natural",
        "authorization_target_kind": "filesystem_paths",
        "workflow_safe": True,
        "group_safe": True,
    })
    assert valid.effects[0].value == "filesystem.read"
    for update in (
        {"effects": ["made.up"], "read_only": False},
        {"read_only": False},
        {"effects": ["filesystem.write"], "read_only": False, "destructive": False},
        {"idempotency": "unsafe", "workflow_safe": True},
        {"effects": [], "read_only": True},
    ):
        raw = valid.to_dict()
        raw.update(update)
        with pytest.raises(ValueError):
            CapabilityMetadata.from_dict(raw)


def test_resource_ref_requires_exact_scope_owner_package_and_digest():
    ref = ResourceRef.from_dict(flow_ref())
    assert ResourceRef.from_dict(ref.to_dict()) == ref
    invalid = (
        flow_ref(owner_id="nobody"),
        flow_ref(scope="user", owner_id=None),
        flow_ref(name="pawflow.agents.wiki"),
        flow_ref(package_version=None),
        flow_ref(content_digest="sha256"),
        flow_ref(schema_version=2),
    )
    for raw in invalid:
        with pytest.raises(ValueError):
            ResourceRef.from_dict(raw)


def test_resource_origin_and_assigned_skill_are_versioned():
    origin = ResourceOrigin.from_dict({
        "kind": "pfp_package",
        "scope": "global",
        "publisher": {"name": "PawFlow"},
        "package": {"id": "pawflow.agents", "version": "1.0.0"},
        "signature_status": "verified",
        "review_status": "trusted",
        "installed_at": NOW,
        "updated_at": NOW,
        "content_digest": DIGEST,
        "mutable": False,
    })
    assert origin.content_digest == DIGEST
    skill = AssignedSkill.from_dict({
        "schema_version": 2,
        "ref": {**agent_ref("wiki-skill", "skill:wiki"), "resource_type": "skill"},
        "params": {},
        "condition": None,
        "invocation_policy_override": "explicit_only",
        "assigned_at": NOW,
        "assigned_by": "alice",
        "assignment_digest": OTHER_DIGEST,
    })
    assert AssignedSkill.from_dict(skill.to_dict()) == skill
    with pytest.raises(ValueError):
        AssignedSkill.from_dict({**skill.to_dict(), "schema_version": 1})


def test_tool_execution_context_binds_authority_to_the_turn():
    raw = {
        "schema_version": 1,
        "turn": identity_dict(),
        "tool_call_id": "tool-1",
        "tool_name": "read",
        "handler_identity": "core.handlers.files.ReadHandler",
        "arguments_digest": DIGEST,
        "target_fingerprint": "relay:workspace:/workspace/README.md",
        "capability_effects": ["filesystem.read"],
        "relay_id": "MyWorkspace",
        "service_id": None,
        "resource_paths": ["/workspace/README.md"],
        "authorization_ref": auth_ref(),
        "policy_snapshot_digest": OTHER_DIGEST,
        "created_at": NOW,
    }
    context = ToolExecutionContext.from_dict(raw)
    assert ToolExecutionContext.from_dict(context.to_dict()) == context
    with pytest.raises(ValueError):
        ToolExecutionContext.from_dict({
            **raw, "authorization_ref": auth_ref(root_turn_id="another-turn")
        })


def test_approval_request_for_policy_gate_cannot_offer_broad_grants():
    raw = {
        "schema_version": 1,
        "request_id": EVENT_ID,
        "conversation_id": "conv-1",
        "root_conversation_id": "conv-1",
        "agent_instance": "Wiki",
        "turn_id": "web:turn-1",
        "turn_epoch": 3,
        "run_generation": 4,
        "tool_call_id": "tool-1",
        "handler_identity": "files.write",
        "effective_policy_name": "default",
        "arguments_digest": DIGEST,
        "target_fingerprint": "relay:path",
        "capability_effects": ["filesystem.write"],
        "resource_paths": ["/workspace/a"],
        "relay_id": "MyWorkspace",
        "service_id": None,
        "summary": "Write /workspace/a",
        "redacted_arguments": {"path": "/workspace/a"},
        "allowed_choices": ["deny", "allow_once"],
        "authorization_ref": auth_ref(),
        "policy_snapshot_digest": OTHER_DIGEST,
        "request_kind": "policy_gate_ask",
        "status": "pending",
        "created_at": NOW,
        "expires_at": LATER,
        "settled_at": None,
        "settled_by": None,
        "settlement_reason": None,
    }
    request = ApprovalRequest.from_dict(raw)
    assert request.status == "pending"
    with pytest.raises(ValueError):
        ApprovalRequest.from_dict({**raw, "allowed_choices": ["deny", "allow_turn"]})
    with pytest.raises(ValueError):
        ApprovalRequest.from_dict({**raw, "status": "allowed"})


@pytest.mark.parametrize("scope", [
    {"kind": "call", "turn_id": None, "turn_epoch": None, "tool_call_id": None},
    {"kind": "turn", "turn_id": "turn", "turn_epoch": None, "tool_call_id": None},
    {"kind": "conversation", "turn_id": "turn", "turn_epoch": None, "tool_call_id": None},
])
def test_tool_grant_scope_cannot_be_underspecified_or_widened(scope):
    raw = {
        "schema_version": 1,
        "grant_id": EVENT_ID,
        "conversation_id": "conv-1",
        "agent_instance": "Wiki",
        "scope": scope,
        "matcher": {
            "handler_identity": "files.read",
            "effective_policy_name": "default",
            "target_fingerprint": "relay:path",
            "relay_id": "MyWorkspace",
            "service_id": None,
            "resource_path_prefixes": ["/workspace"],
            "capability_effects": ["filesystem.read"],
        },
        "policy_snapshot_digest": DIGEST,
        "created_at": NOW,
        "expires_at": None,
        "created_by": "alice",
        "retired_at": None,
        "retirement_reason": None,
    }
    with pytest.raises(ValueError):
        ToolGrant.from_dict(raw)


def test_tool_lifecycle_contract_and_transition_table():
    raw = {
        "schema_version": 1,
        "event_id": EVENT_ID,
        "conversation_id": "conv-1",
        "root_conversation_id": "conv-1",
        "agent_instance": "Wiki",
        "turn_id": "web:turn-1",
        "turn_epoch": 3,
        "run_generation": 4,
        "execution_epoch": str(uuid.uuid4()),
        "sequence": 1,
        "tool_call_id": "tool-1",
        "request_id": None,
        "tool_name": "read",
        "handler_identity": "files.read",
        "phase": "announced",
        "timestamp": NOW,
        "data": {},
    }
    event = ToolLifecycleEvent.from_dict(raw)
    assert ToolLifecycleEvent.from_dict(event.to_dict()) == event
    validate_tool_lifecycle_transition(None, "announced")
    validate_tool_lifecycle_transition("announced", "dispatched")
    validate_tool_lifecycle_transition("dispatched", "completed")
    validate_tool_lifecycle_transition("progress", "progress")
    with pytest.raises(ValueError):
        validate_tool_lifecycle_transition(None, "completed")
    with pytest.raises(ValueError):
        validate_tool_lifecycle_transition("completed", "progress")


def test_agent_run_projection_requires_namespaced_identity_and_terminal_time():
    raw = {
        "schema_version": 1,
        "run_id": "workflow:wr-1",
        "runtime_kind": "workflow",
        "conversation_id": "conv-1",
        "root_conversation_id": "conv-1",
        "owner_agent": "Wiki",
        "target_agent": None,
        "title": "Refresh wiki",
        "status": "running",
        "created_at": NOW,
        "updated_at": NOW,
        "terminal_at": None,
        "progress": {},
        "cost_summary": None,
        "artifact_count": 0,
        "source_task_id": None,
    }
    assert AgentRunSummary.from_dict(raw).run_id == "workflow:wr-1"
    with pytest.raises(ValueError):
        AgentRunSummary.from_dict({**raw, "run_id": "task:wr-1"})
    with pytest.raises(ValueError):
        AgentRunSummary.from_dict({**raw, "status": "completed"})


def group_dict(**overrides):
    value = {
        "schema_version": 1,
        "name": "reviewers",
        "description": "Two bounded reviewers",
        "members": [
            {"member_id": "a", "member_kind": "conversation_instance",
             "agent_ref": agent_ref("A", "agent:a"), "instance_name": "A",
             "display_name": None, "role": "reviewer", "required": True},
            {"member_id": "b", "member_kind": "conversation_instance",
             "agent_ref": agent_ref("B", "agent:b"), "instance_name": "B",
             "display_name": None, "role": "reviewer", "required": True},
        ],
        "selection": {"mode": "all", "classifier_service_role": None},
        "deliberation": {"max_rounds": 2, "max_messages_per_member_per_round": 1,
                         "max_total_participant_calls": 12, "max_parallelism": 4,
                         "allow_pass": True, "rotate_first_speaker": True},
        "context_policy": {"private_context": "none", "shared_history_limit": 24,
                           "attachments": "explicit_only"},
        "tool_policy": {"mode": "none", "allowed_effects": []},
        "synthesis": {"mode": "designated_member", "member_id": "a",
                      "llm_service_role": None},
        "budgets": {"max_tokens": 4000, "max_cost": 1.0, "timeout_seconds": 60},
        "output": {"include_attributions": True, "include_dissent": True},
    }
    value.update(overrides)
    return value


def test_group_contract_enforces_distinct_members_and_safe_tools():
    group = AgentGroupDefinition.from_dict(group_dict())
    assert AgentGroupDefinition.from_dict(group.to_dict()) == group
    with pytest.raises(ValueError):
        AgentGroupDefinition.from_dict(group_dict(members=group_dict()["members"][:1]))
    duplicated = group_dict()["members"]
    duplicated[1] = {**duplicated[1], "member_id": "A"}
    with pytest.raises(ValueError):
        AgentGroupDefinition.from_dict(group_dict(members=duplicated))
    with pytest.raises(ValueError):
        AgentGroupDefinition.from_dict(group_dict(
            tool_policy={"mode": "declared", "allowed_effects": ["filesystem.write"]}))


def test_group_contract_defaults_to_unlimited_and_has_no_arbitrary_ceilings():
    raw = group_dict()
    raw.pop("deliberation")
    raw.pop("context_policy")
    raw.pop("budgets")
    unlimited = AgentGroupDefinition.from_dict(raw)
    assert unlimited.deliberation.max_rounds == 0
    assert unlimited.deliberation.max_total_participant_calls == 0
    assert unlimited.deliberation.max_parallelism == 0
    assert unlimited.context_policy.shared_history_limit == 0
    assert unlimited.budgets.max_tokens == 0
    assert unlimited.budgets.timeout_seconds == 0

    explicit = group_dict(
        deliberation={
            "max_rounds": 500,
            "max_messages_per_member_per_round": 20,
            "max_total_participant_calls": 5000,
            "max_parallelism": 50,
        },
        context_policy={
            "private_context": "none",
            "shared_history_limit": 1000,
            "attachments": "explicit_only",
        },
        budgets={"max_tokens": 0, "max_cost": 0, "timeout_seconds": 0},
    )
    configured = AgentGroupDefinition.from_dict(explicit)
    assert configured.deliberation.max_rounds == 500
    assert configured.deliberation.max_total_participant_calls == 5000
    assert configured.context_policy.shared_history_limit == 1000


def test_non_streaming_agent_rounds_do_not_gain_an_implicit_single_round_cap():
    source = Path("tasks/ai/_alc_setup.py").read_text(encoding="utf-8")
    assert "if st.emitter.is_streaming else 1" not in source
    assert 'st.ctx.get("max_rounds", 0) or 0' in source


def test_participant_pass_cannot_smuggle_content():
    raw = {
        "schema_version": 1,
        "group_run_id": EVENT_ID,
        "round": 1,
        "member_id": "a",
        "member_snapshot_digest": DIGEST,
        "disposition": "pass",
        "content": "",
        "citations": [],
        "confidence": None,
        "token_usage": {"input": 10, "output": 1},
        "created_at": NOW,
    }
    assert ParticipantPost.from_dict(raw).disposition == "pass"
    with pytest.raises(ValueError):
        ParticipantPost.from_dict({**raw, "content": "hidden answer"})


def test_agent_workflow_definition_requires_v1_kind_distinct_ports_and_safe_policy():
    raw = {
        "id": "wiki-agent",
        "name": "Wiki Agent",
        "version": "1.0.0",
        "kind": "agent_workflow",
        "agent_contract": {
            "version": 1,
            "input": {"port": "agent_request"},
            "terminal": {"port": "agent_response"},
            "parameters": {"llm": {"type": "service_ref", "capability": "llm",
                                     "required": True}},
            "supported_preempt_policies": ["checkpoint", "queue"],
            "allowed_effects": ["resource.read"],
        },
    }
    definition = AgentWorkflowDefinition.from_dict(raw)
    assert definition.kind == "agent_workflow"
    with pytest.raises(ValueError):
        AgentWorkflowDefinition.from_dict({
            **raw, "agent_contract": {**raw["agent_contract"],
                                      "terminal": {"port": "agent_request"}}
        })
    with pytest.raises(ValueError):
        AgentWorkflowDefinition.from_dict({
            **raw, "agent_contract": {**raw["agent_contract"], "version": 2}
        })


def prepared_turn_dict(**overrides):
    value = {
        "schema_version": 1,
        "turn_identity": identity_dict(),
        "conversation_id": "conv-1",
        "agent_name": "Wiki",
        "user_id": "alice",
        "root_turn_id": "web:turn-1",
        "request_message_ids": ["web:turn-1"],
        "channel": "web",
        "message": "Refresh the wiki",
        "attachments": [],
        "source": {"type": "user", "name": "alice"},
        "permission_mode": "default",
        "authorization_ref": auth_ref(),
    }
    value.update(overrides)
    return value


def test_prepared_turn_has_one_shared_turn_identity():
    turn = PreparedAgentTurn.from_dict(prepared_turn_dict())
    assert PreparedAgentTurn.from_dict(turn.to_dict()) == turn
    with pytest.raises(ValueError):
        PreparedAgentTurn.from_dict(prepared_turn_dict(root_turn_id="new-root"))
    with pytest.raises(ValueError):
        PreparedAgentTurn.from_dict(prepared_turn_dict(request_message_ids=["other-message"]))


def test_workflow_request_and_result_allow_only_success_terminals():
    request = AgentWorkflowRequest.from_dict({
        "schema_version": 1,
        "request": {"message": "Refresh", "attachments": []},
        "conversation": {"id": "conv-1", "agent": "Wiki"},
        "turn": {"root_turn_id": "turn-1", "request_message_ids": ["turn-1"]},
        "parameters": {},
    })
    assert request.turn.root_turn_id == "turn-1"
    result = AgentWorkflowResult.from_dict({
        "schema_version": 1,
        "status": "no_change",
        "response": "No source-backed changes were needed.",
        "artifacts": [],
        "metrics": {"sources_processed": 0},
        "answered_turn_ids": ["turn-1"],
    })
    assert AgentWorkflowResult.from_dict(result.to_dict()) == result
    with pytest.raises(ValueError):
        AgentWorkflowResult.from_dict({**result.to_dict(), "status": "failed"})


def test_workflow_run_context_pins_identity_generation_flow_and_authority():
    raw = {
        "schema_version": 1,
        "run_id": "wr-1",
        "turn_identity": identity_dict(),
        "conversation_id": "conv-1",
        "agent_name": "Wiki",
        "user_id": "alice",
        "root_turn_id": "web:turn-1",
        "run_generation": 4,
        "flow_ref": flow_ref(),
        "channel": "web",
        "invocation_mode": "conversation",
        "permission_mode": "default",
        "authorization_ref": auth_ref(),
        "deadline_at": LATER,
        "limits": {"max_duration_seconds": 900, "max_llm_calls": 24,
                   "max_flowfiles": 200, "max_fanout": 16, "max_cost_usd": 2.0},
        "service_snapshot": {},
        "cancel_token": "internal-token",
        "event_sink": "internal-sink",
    }
    context = WorkflowRunContext.from_dict(raw)
    assert WorkflowRunContext.from_dict(context.to_dict()) == context
    with pytest.raises(ValueError):
        WorkflowRunContext.from_dict({**raw, "run_generation": 5})
    with pytest.raises(ValueError):
        WorkflowRunContext.from_dict({
            **raw, "flow_ref": {**agent_ref("Wiki", "agent:wiki")}
        })


def test_inbox_claim_state_requires_owner_and_lease():
    raw = {
        "schema_version": 1,
        "conversation_id": "conv-1",
        "agent_key": "wiki",
        "msg_id": "msg-1",
        "sequence": 1,
        "payload": {"msg_id": "msg-1", "content": "new focus"},
        "source": "web",
        "state": "pending",
        "owner_run_id": None,
        "lease_expires_at": None,
        "enqueued_at": NOW,
        "updated_at": NOW,
    }
    assert AgentInboxItem.from_dict(raw).state == "pending"
    with pytest.raises(ValueError):
        AgentInboxItem.from_dict({**raw, "state": "claimed"})
    claimed = {**raw, "state": "claimed", "owner_run_id": "wr-1",
               "lease_expires_at": LATER}
    assert AgentInboxItem.from_dict(claimed).owner_run_id == "wr-1"


def test_workflow_state_machine_and_terminal_record_fail_closed():
    validate_workflow_run_transition("accepted", "running")
    validate_workflow_run_transition("running", "committing")
    validate_workflow_run_transition("committing", "completed")
    with pytest.raises(ValueError):
        validate_workflow_run_transition("running", "completed")
    with pytest.raises(ValueError):
        validate_workflow_run_transition("completed", "running")

    raw = {
        "schema_version": 1,
        "run_id": "wr-1",
        "conversation_id": "conv-1",
        "agent_name": "Wiki",
        "root_turn_id": "turn-1",
        "run_generation": 1,
        "flow_ref": flow_ref(),
        "status": "completed",
        "reason": None,
        "answered_turn_ids": ["turn-1"],
        "assistant_msg_id": "assistant-1",
        "terminal_event_id": "event-1",
        "created_at": NOW,
        "updated_at": LATER,
        "terminal_at": LATER,
        "recovery_count": 0,
    }
    assert WorkflowRunRecord.from_dict(raw).status == "completed"
    with pytest.raises(ValueError):
        WorkflowRunRecord.from_dict({**raw, "assistant_msg_id": None})
    with pytest.raises(ValueError):
        WorkflowRunRecord.from_dict({**raw, "schema_version": 9})


def test_workflow_event_and_error_require_stable_ids_and_versions():
    event = WorkflowRunEvent.from_dict({
        "schema_version": 1,
        "event_id": EVENT_ID,
        "run_id": "wr-1",
        "sequence": 1,
        "conversation_id": "conv-1",
        "agent_name": "Wiki",
        "turn_id": "turn-1",
        "run_generation": 1,
        "event_type": "started",
        "timestamp": NOW,
        "data": {},
    })
    assert WorkflowRunEvent.from_dict(event.to_dict()) == event
    error = WorkflowRunError.from_dict({
        "schema_version": 1,
        "error_id": str(uuid.uuid4()),
        "run_id": "wr-1",
        "code": "unsafe_task",
        "message": "Task does not declare safe effects",
        "retryable": False,
        "task_id": "task-1",
        "details": {},
        "created_at": NOW,
    })
    assert error.code == "unsafe_task"
    with pytest.raises(ValueError):
        WorkflowRunError.from_dict({**error.to_dict(), "code": "whatever"})
