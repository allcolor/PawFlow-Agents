"""Versioned approval-request and reusable-grant data contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import field_validator, model_validator

from core.agent_contracts import (
    AuthorizationRefContract,
    CapabilityEffect,
    ContractModel,
    VersionedContract,
    require_positive,
    require_sha256,
    require_text,
    require_utc_timestamp,
    require_uuid,
)


class ApprovalRequest(VersionedContract):
    request_id: str
    conversation_id: str
    root_conversation_id: str
    agent_instance: str
    turn_id: str
    turn_epoch: int
    run_generation: int
    tool_call_id: str
    handler_identity: str
    effective_policy_name: str
    arguments_digest: str
    target_fingerprint: str
    capability_effects: tuple[CapabilityEffect, ...]
    resource_paths: tuple[str, ...] = ()
    relay_id: str | None = None
    service_id: str | None = None
    summary: str
    redacted_arguments: dict[str, Any]
    allowed_choices: tuple[Literal["deny", "allow_once", "allow_turn", "allow_conversation"], ...]
    authorization_ref: AuthorizationRefContract
    policy_snapshot_digest: str
    request_kind: Literal["generic_permission", "policy_gate_ask", "hard_confirm"]
    status: Literal["pending", "allowed", "denied", "expired", "cancelled", "retired"]
    created_at: str
    expires_at: str
    settled_at: str | None = None
    settled_by: str | None = None
    settlement_reason: str | None = None

    @field_validator("request_id")
    @classmethod
    def _request_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "request_id")

    @field_validator(
        "conversation_id", "root_conversation_id", "agent_instance", "turn_id",
        "tool_call_id", "handler_identity", "effective_policy_name",
        "target_fingerprint", "summary",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("turn_epoch", "run_generation", mode="before")
    @classmethod
    def _positive_counters(cls, value: int, info) -> int:
        return require_positive(value, info.field_name)

    @field_validator("arguments_digest", "policy_snapshot_digest")
    @classmethod
    def _digests_are_sha256(cls, value: str, info) -> str:
        return require_sha256(value, info.field_name)

    @field_validator("capability_effects", "allowed_choices")
    @classmethod
    def _lists_are_non_empty_and_unique(cls, value, info):
        if not value or len(set(value)) != len(value):
            raise ValueError(f"{info.field_name} must be non-empty and unique")
        return value

    @field_validator("created_at", "expires_at", "settled_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @field_validator("settled_by", "settlement_reason", "relay_id", "service_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @model_validator(mode="after")
    def _settlement_matches_status(self):
        settled = self.status != "pending"
        if settled != (self.settled_at is not None):
            raise ValueError("settled_at must be present exactly when the request is settled")
        if self.authorization_ref.root_turn_id != self.turn_id:
            raise ValueError("authorization_ref root_turn_id does not match turn_id")
        if self.request_kind != "generic_permission" and any(
            choice in self.allowed_choices for choice in ("allow_turn", "allow_conversation")
        ):
            raise ValueError("hard and policy-gate requests permit exact-call approval only")
        return self


class ToolGrantScope(ContractModel):
    kind: Literal["call", "turn", "conversation"]
    turn_id: str | None = None
    turn_epoch: int | None = None
    tool_call_id: str | None = None

    @field_validator("turn_id", "tool_call_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("turn_epoch", mode="before")
    @classmethod
    def _optional_positive_epoch(cls, value: int | None) -> int | None:
        return None if value is None else require_positive(value, "turn_epoch")

    @model_validator(mode="after")
    def _scope_fields_are_exact(self):
        if self.kind == "call" and not self.tool_call_id:
            raise ValueError("call grants require tool_call_id")
        if self.kind == "turn" and (not self.turn_id or self.turn_epoch is None):
            raise ValueError("turn grants require turn_id and turn_epoch")
        if self.kind == "conversation" and any(
            value is not None for value in (self.turn_id, self.turn_epoch, self.tool_call_id)
        ):
            raise ValueError("conversation grants cannot carry narrower scope identities")
        return self


class ToolGrantMatcher(ContractModel):
    handler_identity: str
    effective_policy_name: str
    target_fingerprint: str
    relay_id: str | None = None
    service_id: str | None = None
    resource_path_prefixes: tuple[str, ...] = ()
    capability_effects: tuple[CapabilityEffect, ...]

    @field_validator("handler_identity", "effective_policy_name", "target_fingerprint")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("relay_id", "service_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("capability_effects")
    @classmethod
    def _effects_are_non_empty_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("capability_effects must be non-empty and unique")
        return value


class ToolGrant(VersionedContract):
    grant_id: str
    conversation_id: str
    agent_instance: str
    scope: ToolGrantScope
    matcher: ToolGrantMatcher
    policy_snapshot_digest: str
    created_at: str
    expires_at: str | None = None
    created_by: str
    retired_at: str | None = None
    retirement_reason: str | None = None

    @field_validator("grant_id")
    @classmethod
    def _grant_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "grant_id")

    @field_validator("conversation_id", "agent_instance", "created_by")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        return require_sha256(value, "policy_snapshot_digest")

    @field_validator("created_at", "expires_at", "retired_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @field_validator("retirement_reason")
    @classmethod
    def _optional_reason(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "retirement_reason")

    @model_validator(mode="after")
    def _retirement_is_complete(self):
        if (self.retired_at is None) != (self.retirement_reason is None):
            raise ValueError("retired_at and retirement_reason must be supplied together")
        return self
