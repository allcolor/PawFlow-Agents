"""Immutable identity assigned once at accepted agent-turn ingress."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from core.agent_contracts import (
    VersionedContract,
    require_positive,
    require_text,
    require_utc_timestamp,
    require_uuid,
)


class AgentTurnIdentity(VersionedContract):
    conversation_id: str
    root_conversation_id: str
    agent_instance: str
    turn_id: str
    ingress_msg_id: str
    turn_epoch: int
    run_generation: int
    authorization_context_id: str
    authorization_revision_at_start: int
    source_kind: Literal[
        "user", "delegate", "task", "continuation", "automation", "a2a"
    ]
    source_id: str | None = None
    created_at: str

    @field_validator(
        "conversation_id", "root_conversation_id", "agent_instance", "turn_id",
        "ingress_msg_id",
    )
    @classmethod
    def _identities_are_present(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator(
        "turn_epoch", "run_generation", "authorization_revision_at_start", mode="before"
    )
    @classmethod
    def _counters_are_positive(cls, value: int, info) -> int:
        return require_positive(value, info.field_name)

    @field_validator("authorization_context_id")
    @classmethod
    def _authorization_context_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "authorization_context_id")

    @field_validator("source_id")
    @classmethod
    def _source_id_is_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "source_id")

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "created_at")
