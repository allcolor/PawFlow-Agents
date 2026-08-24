"""Server-owned context created before tool approval and dispatch."""

from __future__ import annotations

from pydantic import field_validator, model_validator

from core.agent_contracts import (
    AuthorizationRefContract,
    CapabilityEffect,
    VersionedContract,
    require_sha256,
    require_text,
    require_utc_timestamp,
)
from core.agent_turn_identity import AgentTurnIdentity


class ToolExecutionContext(VersionedContract):
    turn: AgentTurnIdentity
    tool_call_id: str
    tool_name: str
    handler_identity: str
    arguments_digest: str
    target_fingerprint: str
    capability_effects: tuple[CapabilityEffect, ...]
    relay_id: str | None = None
    service_id: str | None = None
    resource_paths: tuple[str, ...] = ()
    authorization_ref: AuthorizationRefContract
    policy_snapshot_digest: str
    created_at: str

    @field_validator("tool_call_id", "tool_name", "handler_identity", "target_fingerprint")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("relay_id", "service_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("resource_paths")
    @classmethod
    def _paths_are_present_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for path in value:
            require_text(path, "resource_paths item")
        if len(set(value)) != len(value):
            raise ValueError("resource_paths must be unique")
        return value

    @field_validator("capability_effects")
    @classmethod
    def _effects_are_present_and_unique(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("capability_effects must be non-empty and unique")
        return value

    @field_validator("arguments_digest", "policy_snapshot_digest")
    @classmethod
    def _digests_are_sha256(cls, value: str, info) -> str:
        return require_sha256(value, info.field_name)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "created_at")

    @model_validator(mode="after")
    def _authorization_matches_turn(self):
        if self.authorization_ref.context_id != self.turn.authorization_context_id:
            raise ValueError("authorization_ref does not belong to the turn")
        if self.authorization_ref.root_turn_id != self.turn.turn_id:
            raise ValueError("authorization_ref root_turn_id does not match the turn")
        if self.authorization_ref.revision < self.turn.authorization_revision_at_start:
            raise ValueError("authorization_ref predates the accepted turn")
        return self
