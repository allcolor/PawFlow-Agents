"""Ordered tool lifecycle event contract and transition validator."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from core.agent_contracts import (
    VersionedContract,
    require_positive,
    require_text,
    require_utc_timestamp,
    require_uuid,
)

ToolLifecyclePhase = Literal[
    "announced", "approval_requested", "approved", "denied", "dispatched",
    "progress", "backgrounded", "cancel_requested", "completed", "failed",
    "cancelled", "retired", "reset",
]

TOOL_LIFECYCLE_TERMINAL_PHASES = frozenset({
    "denied", "completed", "failed", "cancelled", "retired",
})

_TRANSITIONS = {
    "announced": frozenset({"approval_requested", "dispatched", "retired"}),
    "approval_requested": frozenset({"approved", "denied", "retired"}),
    "approved": frozenset({"dispatched", "retired"}),
    "dispatched": frozenset({
        "progress", "backgrounded", "cancel_requested", "completed", "failed",
        "cancelled", "retired",
    }),
    "progress": frozenset({
        "progress", "backgrounded", "cancel_requested", "completed", "failed",
        "cancelled", "retired",
    }),
    "backgrounded": frozenset({
        "progress", "cancel_requested", "completed", "failed", "cancelled", "retired",
    }),
    "cancel_requested": frozenset({"completed", "failed", "cancelled", "retired"}),
}


def validate_tool_lifecycle_transition(previous: str | None, current: str) -> None:
    """Raise when ``current`` cannot causally follow ``previous``."""
    if current == "reset":
        return
    if previous is None:
        if current != "announced":
            raise ValueError("a tool lifecycle must start with announced")
        return
    if previous in TOOL_LIFECYCLE_TERMINAL_PHASES:
        raise ValueError("terminal tool lifecycle phases are immutable")
    if current not in _TRANSITIONS.get(previous, frozenset()):
        raise ValueError(f"invalid tool lifecycle transition: {previous} -> {current}")


class ToolLifecycleEvent(VersionedContract):
    event_id: str
    conversation_id: str
    root_conversation_id: str
    agent_instance: str
    turn_id: str
    turn_epoch: int
    run_generation: int
    execution_epoch: str
    sequence: int
    tool_call_id: str
    request_id: str | None = None
    tool_name: str
    handler_identity: str
    phase: ToolLifecyclePhase
    timestamp: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "execution_epoch")
    @classmethod
    def _ids_are_uuid(cls, value: str, info) -> str:
        return require_uuid(value, info.field_name)

    @field_validator(
        "conversation_id", "root_conversation_id", "agent_instance", "turn_id",
        "tool_call_id", "tool_name", "handler_identity",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("request_id")
    @classmethod
    def _optional_request_id(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "request_id")

    @field_validator("turn_epoch", "run_generation", "sequence", mode="before")
    @classmethod
    def _positive_counters(cls, value: int, info) -> int:
        return require_positive(value, info.field_name)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "timestamp")
