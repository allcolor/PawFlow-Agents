"""Normalized read-only projections for heterogeneous agent runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from core.agent_contracts import (
    ContractModel,
    VersionedContract,
    require_non_negative,
    require_sha256,
    require_text,
    require_utc_timestamp,
)

AgentRunKind = Literal[
    "delegate", "flash", "task", "workflow", "plan", "a2a",
    "external_mcp", "external_agui",
]


class AgentRunSummary(VersionedContract):
    run_id: str
    runtime_kind: AgentRunKind
    conversation_id: str
    root_conversation_id: str
    owner_agent: str
    target_agent: str | None = None
    title: str
    status: Literal[
        "queued", "running", "waiting_user", "waiting_external", "cancelling",
        "completed", "failed", "cancelled", "superseded",
    ]
    created_at: str
    updated_at: str
    terminal_at: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    cost_summary: dict[str, Any] | None = None
    artifact_count: int = 0
    source_task_id: str | None = None

    @field_validator(
        "run_id", "conversation_id", "root_conversation_id", "owner_agent", "title",
    )
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("target_agent", "source_task_id")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("artifact_count", mode="before")
    @classmethod
    def _artifact_count_is_non_negative(cls, value: int) -> int:
        return require_non_negative(value, "artifact_count")

    @field_validator("created_at", "updated_at", "terminal_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @model_validator(mode="after")
    def _identity_and_terminal_are_consistent(self):
        if not self.run_id.startswith(self.runtime_kind + ":"):
            raise ValueError("run_id must use the runtime_kind namespace")
        terminal = self.status in {"completed", "failed", "cancelled", "superseded"}
        if terminal != (self.terminal_at is not None):
            raise ValueError("terminal_at must be present exactly for terminal status")
        return self


class AgentRunCapabilities(ContractModel):
    can_follow_up: StrictBool
    can_interrupt: StrictBool
    can_cancel: StrictBool
    can_retry: StrictBool
    can_archive: StrictBool
    can_delete: StrictBool
    can_list_artifacts: StrictBool
    reasons: dict[str, str] = Field(default_factory=dict)


class AgentArtifact(ContractModel):
    artifact_id: str
    run_id: str
    name: str
    media_type: str
    size_bytes: int | None = None
    source_kind: Literal["filestore", "workspace", "provider", "transcript", "report"]
    source_ref: str
    digest: str | None = None
    created_at: str
    downloadable: StrictBool
    expires_at: str | None = None

    @field_validator("artifact_id", "run_id", "name", "media_type", "source_ref")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("size_bytes", mode="before")
    @classmethod
    def _optional_size_is_non_negative(cls, value: int | None) -> int | None:
        return None if value is None else require_non_negative(value, "size_bytes")

    @field_validator("digest")
    @classmethod
    def _optional_digest_is_sha256(cls, value: str | None) -> str | None:
        return None if value is None else require_sha256(value, "digest")

    @field_validator("created_at", "expires_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)
