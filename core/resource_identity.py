"""Exact resource identity, provenance, and assigned-skill contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from core.agent_contracts import (
    ContractModel,
    VersionedContract,
    require_sha256,
    require_text,
    require_utc_timestamp,
)

ResourceScope = Literal["global", "user", "conversation"]


class ResourceRef(VersionedContract):
    resource_type: Literal["skill", "agent", "agent_group", "flow", "tool", "service"]
    name: str
    scope: ResourceScope
    owner_id: str | None = None
    package_id: str | None = None
    package_version: str | None = None
    version: str | None = None
    content_digest: str
    source_id: str

    @field_validator("name", "source_id")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("owner_id", "package_id", "package_version", "version")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        return require_sha256(value, "content_digest")

    @model_validator(mode="after")
    def _scope_owner_is_exact(self):
        if self.scope == "global" and self.owner_id is not None:
            raise ValueError("global resources cannot have owner_id")
        if self.scope != "global" and self.owner_id is None:
            raise ValueError("user and conversation resources require owner_id")
        if self.resource_type == "flow" and self.name.count(":") != 1:
            raise ValueError("flow names must be exact package.flow:version FQNs")
        if (self.package_id is None) != (self.package_version is None):
            raise ValueError("package_id and package_version must be supplied together")
        return self


class ResourceOrigin(ContractModel):
    kind: Literal[
        "built_in", "user_authored", "conversation_authored", "pfp_package",
        "marketplace", "learned", "imported",
    ]
    scope: ResourceScope
    publisher: dict[str, Any] | None = None
    package: dict[str, Any] | None = None
    signature_status: Literal["verified", "unverified", "not_applicable"]
    review_status: Literal["trusted", "reviewed", "pending", "rejected"]
    installed_at: str | None = None
    updated_at: str
    content_digest: str
    mutable: StrictBool

    @field_validator("installed_at", "updated_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None, info) -> str | None:
        return None if value is None else require_utc_timestamp(value, info.field_name)

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        return require_sha256(value, "content_digest")


class AssignedSkill(ContractModel):
    schema_version: Literal[2] = 2
    ref: ResourceRef
    params: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = None
    invocation_policy_override: Literal["auto", "explicit_only", "disabled"] | None = None
    assigned_at: str
    assigned_by: str
    assignment_digest: str

    @field_validator("condition")
    @classmethod
    def _condition_is_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "condition")

    @field_validator("assigned_at")
    @classmethod
    def _assigned_at_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "assigned_at")

    @field_validator("assigned_by")
    @classmethod
    def _assigned_by_is_present(cls, value: str) -> str:
        return require_text(value, "assigned_by")

    @field_validator("assignment_digest")
    @classmethod
    def _assignment_digest_is_sha256(cls, value: str) -> str:
        return require_sha256(value, "assignment_digest")
