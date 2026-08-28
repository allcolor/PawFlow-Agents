"""Pure schemas for workflow-driven agent turns.

The executable workflow runtime is intentionally absent from this module. WP0
only establishes validated serialization boundaries for later gated adapters.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from core.agent_contracts import (
    AuthorizationRefContract,
    CapabilityEffect,
    ContractModel,
    VersionedContract,
    require_non_negative,
    require_positive,
    require_text,
    require_utc_timestamp,
    require_uuid,
)
from core.agent_turn_identity import AgentTurnIdentity
from core.resource_identity import ResourceRef

WorkflowPreemptPolicy = Literal["checkpoint", "queue", "restart"]
WorkflowRunStatus = Literal[
    "accepted", "running", "waiting", "retryable_failed", "cancelling",
    "committing", "completed", "cancelled",
    "superseded", "failed", "timed_out", "budget_exceeded", "force_stopped",
    "recovery_failed",
]


def _parameter_value_matches(kind: str, value: Any) -> bool:
    if kind in {"string", "service_ref"}:
        return isinstance(value, str) and bool(value.strip())
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    return False

WORKFLOW_TERMINAL_STATUSES = frozenset({
    "completed", "cancelled", "superseded", "failed", "timed_out",
    "budget_exceeded", "force_stopped", "recovery_failed",
})
_WORKFLOW_TRANSITIONS = {
    "accepted": frozenset({"running", "cancelled", "superseded", "failed"}),
    "running": frozenset({
        "waiting", "retryable_failed", "cancelling", "superseded", "committing",
        "failed", "timed_out",
        "budget_exceeded", "force_stopped",
    }),
    "waiting": frozenset({
        "running", "cancelling", "cancelled", "superseded", "failed",
        "timed_out", "force_stopped",
    }),
    "retryable_failed": frozenset({
        "running", "cancelling", "cancelled", "superseded", "failed",
        "timed_out", "force_stopped",
    }),
    "cancelling": frozenset({"cancelled", "force_stopped", "failed"}),
    "committing": WORKFLOW_TERMINAL_STATUSES,
}


def validate_workflow_run_transition(previous: str, current: str) -> None:
    if previous in WORKFLOW_TERMINAL_STATUSES:
        raise ValueError("terminal workflow states are immutable")
    if current not in _WORKFLOW_TRANSITIONS.get(previous, frozenset()):
        raise ValueError(f"invalid workflow state transition: {previous} -> {current}")


class AgentContractPort(ContractModel):
    port: str

    @field_validator("port")
    @classmethod
    def _port_is_present(cls, value: str) -> str:
        return require_text(value, "port")


class AgentContractParameter(ContractModel):
    type: Literal["service_ref", "string", "integer", "number", "boolean", "object", "array"]
    required: StrictBool = False
    capability: str | None = None
    default: Any = None
    label: str | None = None
    description: str | None = None

    @field_validator("capability")
    @classmethod
    def _optional_capability(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "capability")

    @field_validator("label", "description")
    @classmethod
    def _optional_help_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @model_validator(mode="after")
    def _service_ref_requires_capability(self):
        if self.type == "service_ref" and self.capability is None:
            raise ValueError("service_ref parameters require capability")
        if self.required and self.default is not None:
            raise ValueError("required parameters cannot define a default")
        if self.default is not None and not _parameter_value_matches(
                self.type, self.default):
            raise ValueError("parameter default does not match its type")
        return self


class AgentWorkflowContract(ContractModel):
    version: Literal[1]
    input: AgentContractPort
    terminal: AgentContractPort
    parameters: dict[str, AgentContractParameter] = Field(default_factory=dict)
    supported_preempt_policies: tuple[WorkflowPreemptPolicy, ...]
    allowed_effects: tuple[CapabilityEffect, ...]

    @field_validator("supported_preempt_policies")
    @classmethod
    def _policies_are_non_empty_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("supported_preempt_policies must be non-empty and unique")
        return value

    @field_validator("allowed_effects")
    @classmethod
    def _effects_are_non_empty_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("allowed_effects must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def _ports_are_distinct(self):
        if self.input.port == self.terminal.port:
            raise ValueError("agent workflow input and terminal ports must differ")
        return self


class AgentWorkflowDefinition(ContractModel):
    id: str
    name: str
    version: str
    kind: Literal["agent_workflow"]
    agent_contract: AgentWorkflowContract

    @field_validator("id", "name", "version")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)


class PreparedAgentTurn(VersionedContract):
    turn_identity: AgentTurnIdentity
    conversation_id: str
    agent_name: str
    user_id: str
    root_turn_id: str
    request_message_ids: tuple[str, ...]
    channel: str
    message: str
    attachments: tuple[dict[str, Any], ...] = ()
    source: dict[str, Any]
    permission_mode: str
    authorization_ref: AuthorizationRefContract

    @field_validator(
        "conversation_id", "agent_name", "user_id", "root_turn_id", "channel",
        "message", "permission_mode",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("request_message_ids")
    @classmethod
    def _message_ids_are_non_empty_and_unique(cls, value: tuple[str, ...]):
        if not value or len(set(value)) != len(value):
            raise ValueError("request_message_ids must be non-empty and unique")
        for item in value:
            require_text(item, "request_message_ids item")
        return value

    @model_validator(mode="after")
    def _identity_projections_match(self):
        if self.root_turn_id != self.turn_identity.turn_id:
            raise ValueError("root_turn_id must project turn_identity.turn_id")
        if self.conversation_id != self.turn_identity.conversation_id:
            raise ValueError("conversation_id must match turn_identity")
        if self.agent_name.casefold() != self.turn_identity.agent_instance.casefold():
            raise ValueError("agent_name must match turn_identity")
        if self.authorization_ref.context_id != self.turn_identity.authorization_context_id:
            raise ValueError("authorization_ref must belong to turn_identity")
        if self.authorization_ref.root_turn_id != self.root_turn_id:
            raise ValueError("authorization_ref root turn does not match")
        if self.turn_identity.ingress_msg_id not in self.request_message_ids:
            raise ValueError("request_message_ids must contain ingress_msg_id")
        return self


class WorkflowLimits(ContractModel):
    max_duration_seconds: int = 0
    max_llm_calls: int = 0
    max_flowfiles: int = 0
    max_fanout: int = 0
    max_cost_usd: float = 0.0

    @field_validator(
        "max_llm_calls", "max_flowfiles", "max_fanout",
        mode="before",
    )
    @classmethod
    def _limits_are_non_negative(
        cls, value: int | None, info,
    ) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{info.field_name} must be an integer >= 0")
        return value

    @field_validator("max_duration_seconds", mode="before")
    @classmethod
    def _duration_is_non_negative(cls, value: int | None) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("max_duration_seconds must be an integer >= 0")
        return value

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _cost_is_non_negative_and_finite(
        cls, value: float | None,
    ) -> float:
        if value is None:
            return 0.0
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("max_cost_usd must be finite and non-negative")
        return float(value)


def _default_workflow_limits() -> WorkflowLimits:
    return WorkflowLimits()


class WorkflowInstanceConfig(ContractModel):
    """Exact, user-visible binding for one workflow agent instance."""

    flow_fqn: str
    flow_scope: Literal["global", "user", "conversation"] | None = None
    input_port: str
    terminal_port: str
    preempt_policy: WorkflowPreemptPolicy
    allowed_effects: tuple[CapabilityEffect, ...]
    parameters: dict[str, Any] = Field(default_factory=dict)
    limits: WorkflowLimits = Field(default_factory=_default_workflow_limits)
    flow_ref: ResourceRef | None = None

    @field_validator("flow_fqn", "input_port", "terminal_port")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("allowed_effects")
    @classmethod
    def _allowed_effects_are_non_empty_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("allowed_effects must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def _binding_is_exact(self):
        if self.flow_fqn.count(":") != 1 or not self.flow_fqn.rsplit(":", 1)[1]:
            raise ValueError("flow_fqn must include an exact version")
        if self.input_port == self.terminal_port:
            raise ValueError("input_port and terminal_port must differ")
        if self.flow_ref is not None:
            if self.flow_ref.resource_type != "flow":
                raise ValueError("flow_ref must identify a flow")
            if self.flow_ref.name != self.flow_fqn:
                raise ValueError("flow_ref must match flow_fqn")
            if self.flow_scope != self.flow_ref.scope:
                raise ValueError("flow_scope must match flow_ref")
        return self


class AgentDefinitionRuntimeDefaults(ContractModel):
    """Optional runtime defaults stored on a reusable agent definition."""

    kind: Literal["workflow"]
    workflow: dict[str, Any]

    @field_validator("workflow")
    @classmethod
    def _workflow_defaults_are_valid(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("workflow defaults must be an object")
        required = {
            "flow_fqn", "input_port", "terminal_port", "preempt_policy",
            "allowed_effects",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(
                "workflow defaults are missing: " + ", ".join(missing))
        flow_fqn = require_text(value.get("flow_fqn"), "flow_fqn")
        if flow_fqn.count(":") != 1 or not flow_fqn.rsplit(":", 1)[1]:
            raise ValueError("flow_fqn must include an exact version")
        if value.get("preempt_policy") not in {"checkpoint", "queue", "restart"}:
            raise ValueError("preempt_policy is invalid")
        require_text(value.get("input_port"), "input_port")
        require_text(value.get("terminal_port"), "terminal_port")
        if value.get("input_port") == value.get("terminal_port"):
            raise ValueError("input_port and terminal_port must differ")
        if "parameters" in value and not isinstance(value["parameters"], dict):
            raise ValueError("workflow parameters must be an object")
        effects = value.get("allowed_effects")
        if not isinstance(effects, (tuple, list)) or not effects:
            raise ValueError("workflow allowed_effects must be a non-empty list")
        try:
            parsed_effects = tuple(CapabilityEffect(item) for item in effects)
        except ValueError as exc:
            raise ValueError("workflow allowed_effects contains an unknown effect") from exc
        if len(set(parsed_effects)) != len(parsed_effects):
            raise ValueError("workflow allowed_effects must be unique")
        return value


class WorkflowRunContext(VersionedContract):
    run_id: str
    turn_identity: AgentTurnIdentity
    conversation_id: str
    agent_name: str
    user_id: str
    root_turn_id: str
    run_generation: int
    flow_ref: ResourceRef
    channel: str
    invocation_mode: Literal[
        "conversation", "automation", "silent_maintenance", "flow"]
    permission_mode: str
    authorization_ref: AuthorizationRefContract
    deadline_at: str | None = None
    limits: WorkflowLimits
    service_snapshot: dict[str, Any]
    cancel_token: str
    event_sink: str
    parent_invocation: dict[str, Any] | None = None
    publish_to_conversation: StrictBool = False
    invocation_depth: int = 0
    ancestor_agent_refs: tuple[ResourceRef, ...] = ()
    ancestor_flow_refs: tuple[ResourceRef, ...] = ()

    @field_validator(
        "run_id", "conversation_id", "agent_name", "user_id", "root_turn_id",
        "channel", "permission_mode", "cancel_token", "event_sink",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("run_generation", mode="before")
    @classmethod
    def _generation_is_positive(cls, value: int) -> int:
        return require_positive(value, "run_generation")

    @field_validator("deadline_at")
    @classmethod
    def _deadline_is_utc(cls, value: str | None) -> str | None:
        return None if value is None else require_utc_timestamp(
            value, "deadline_at")

    @model_validator(mode="after")
    def _server_identity_is_consistent(self):
        if self.flow_ref.resource_type != "flow":
            raise ValueError("flow_ref must identify a flow")
        if self.root_turn_id != self.turn_identity.turn_id:
            raise ValueError("root_turn_id must project turn_identity.turn_id")
        if self.conversation_id != self.turn_identity.conversation_id:
            raise ValueError("conversation_id must match turn_identity")
        if self.run_generation != self.turn_identity.run_generation:
            raise ValueError("run_generation must match turn_identity")
        if self.authorization_ref.root_turn_id != self.root_turn_id:
            raise ValueError("authorization_ref root turn does not match")
        if self.invocation_mode == "flow":
            if not self.parent_invocation:
                raise ValueError("flow invocation requires a parent invocation")
            if self.invocation_depth < 1:
                raise ValueError("flow invocation depth must be positive")
        elif self.parent_invocation is not None:
            raise ValueError("only flow invocation may carry a parent invocation")
        return self


class WorkflowRequestBody(ContractModel):
    message: str
    attachments: tuple[dict[str, Any], ...] = ()

    @field_validator("message")
    @classmethod
    def _message_is_present(cls, value: str) -> str:
        return require_text(value, "message")


class WorkflowConversationRef(ContractModel):
    id: str
    agent: str

    @field_validator("id", "agent")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)


class WorkflowTurnRef(ContractModel):
    root_turn_id: str
    request_message_ids: tuple[str, ...]

    @field_validator("root_turn_id")
    @classmethod
    def _root_turn_is_present(cls, value: str) -> str:
        return require_text(value, "root_turn_id")

    @field_validator("request_message_ids")
    @classmethod
    def _message_ids_are_non_empty_and_unique(cls, value: tuple[str, ...]):
        if not value or len(set(value)) != len(value):
            raise ValueError("request_message_ids must be non-empty and unique")
        return value


class AgentWorkflowRequest(VersionedContract):
    request: WorkflowRequestBody
    conversation: WorkflowConversationRef
    turn: WorkflowTurnRef
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _root_is_an_input_message(self):
        if self.turn.root_turn_id not in self.turn.request_message_ids:
            raise ValueError("root_turn_id must be one of request_message_ids")
        return self


class WorkflowArtifact(ContractModel):
    kind: str
    id: str
    label: str

    @field_validator("kind", "id", "label")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)


class AgentWorkflowResult(VersionedContract):
    status: Literal["completed", "no_change"]
    response: str
    artifacts: tuple[WorkflowArtifact, ...] = ()
    metrics: dict[str, int] = Field(default_factory=dict)
    answered_turn_ids: tuple[str, ...]

    @field_validator("response")
    @classmethod
    def _response_is_present(cls, value: str) -> str:
        return require_text(value, "response")

    @field_validator("answered_turn_ids")
    @classmethod
    def _answered_ids_are_non_empty_and_unique(cls, value: tuple[str, ...]):
        if not value or len(set(value)) != len(value):
            raise ValueError("answered_turn_ids must be non-empty and unique")
        for item in value:
            require_text(item, "answered_turn_ids item")
        return value

    @field_validator("metrics")
    @classmethod
    def _metrics_are_non_negative(cls, value: dict[str, int]):
        for name, count in value.items():
            require_text(name, "metric name")
            require_non_negative(count, f"metric {name}")
        return value


class AgentInboxItem(VersionedContract):
    conversation_id: str
    agent_key: str
    msg_id: str
    sequence: int
    payload: dict[str, Any]
    source: str
    state: Literal["pending", "claimed", "acknowledged", "discarded"]
    owner_run_id: str | None = None
    lease_expires_at: str | None = None
    enqueued_at: str
    updated_at: str

    @field_validator("conversation_id", "agent_key", "msg_id", "source")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("sequence", mode="before")
    @classmethod
    def _sequence_is_positive(cls, value: int) -> int:
        return require_positive(value, "sequence")

    @field_validator("owner_run_id")
    @classmethod
    def _optional_owner(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "owner_run_id")

    @field_validator("lease_expires_at", "enqueued_at", "updated_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @model_validator(mode="after")
    def _claim_fields_match_state(self):
        claimed = self.state == "claimed"
        if claimed != (self.owner_run_id is not None and self.lease_expires_at is not None):
            raise ValueError("claimed inbox items require owner_run_id and lease_expires_at")
        return self


class AgentInboxClaim(VersionedContract):
    claim_id: str
    run_id: str
    task_id: str
    item_ids: tuple[str, ...]
    lease_expires_at: str

    @field_validator("claim_id")
    @classmethod
    def _claim_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "claim_id")

    @field_validator("run_id", "task_id")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("item_ids")
    @classmethod
    def _item_ids_are_non_empty_and_unique(cls, value: tuple[str, ...]):
        if not value or len(set(value)) != len(value):
            raise ValueError("item_ids must be non-empty and unique")
        return value

    @field_validator("lease_expires_at")
    @classmethod
    def _lease_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "lease_expires_at")


class WorkflowRunRecord(VersionedContract):
    run_id: str
    conversation_id: str
    agent_name: str
    root_turn_id: str
    run_generation: int
    flow_ref: ResourceRef
    status: WorkflowRunStatus
    reason: str | None = None
    answered_turn_ids: tuple[str, ...] = ()
    assistant_msg_id: str | None = None
    terminal_event_id: str | None = None
    created_at: str
    updated_at: str
    terminal_at: str | None = None
    recovery_count: int = 0

    @field_validator("run_id", "conversation_id", "agent_name", "root_turn_id")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("run_generation")
    @classmethod
    def _generation_is_positive(cls, value: int) -> int:
        return require_positive(value, "run_generation")

    @field_validator("recovery_count", mode="before")
    @classmethod
    def _recovery_count_is_non_negative(cls, value: int) -> int:
        return require_non_negative(value, "recovery_count")

    @field_validator("reason", "assistant_msg_id", "terminal_event_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("created_at", "updated_at", "terminal_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @model_validator(mode="after")
    def _terminal_metadata_matches_state(self):
        if self.flow_ref.resource_type != "flow":
            raise ValueError("flow_ref must identify a flow")
        terminal = self.status in WORKFLOW_TERMINAL_STATUSES
        if terminal != (self.terminal_at is not None):
            raise ValueError("terminal_at must be present exactly for terminal status")
        if self.status == "completed" and (
            self.assistant_msg_id is None or self.terminal_event_id is None
        ):
            raise ValueError("completed runs require assistant and terminal event ids")
        return self


class WorkflowRunEvent(VersionedContract):
    event_id: str
    run_id: str
    sequence: int
    conversation_id: str
    agent_name: str
    turn_id: str
    run_generation: int
    event_type: Literal[
        "accepted", "started", "stage_started", "progress", "usage", "stage_completed",
        "checkpoint", "terminal", "error", "cancelled", "force_stopped",
        "kanban_comment", "kanban_assignment", "kanban_command_requested",
        "kanban_command_succeeded", "kanban_command_rejected",
        "kanban_attachment_added", "kanban_attachment_removed", "kanban_review",
    ]
    timestamp: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def _event_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "event_id")

    @field_validator("run_id", "conversation_id", "agent_name", "turn_id")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("sequence", "run_generation", mode="before")
    @classmethod
    def _counters_are_positive(cls, value: int, info) -> int:
        return require_positive(value, info.field_name)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "timestamp")


class WorkflowRunError(VersionedContract):
    error_id: str
    run_id: str
    code: Literal[
        "invalid_request", "invalid_binding", "invalid_terminal", "unsafe_task",
        "authorization_denied", "cancelled", "superseded", "timed_out",
        "budget_exceeded", "task_failed", "recovery_failed", "internal_error",
    ]
    message: str
    retryable: StrictBool
    task_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str

    @field_validator("error_id")
    @classmethod
    def _error_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "error_id")

    @field_validator("run_id", "message")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("task_id")
    @classmethod
    def _optional_task_id(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "task_id")

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "created_at")
