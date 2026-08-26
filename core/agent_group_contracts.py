"""Version-1 agent-group resource and participant contracts."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import StrictBool, field_validator, model_validator

from core.agent_contracts import (
    CapabilityEffect,
    ContractModel,
    VersionedContract,
    require_non_negative,
    require_positive,
    require_sha256,
    require_text,
    require_utc_timestamp,
    require_uuid,
)
from core.resource_identity import ResourceRef


class AgentGroupMember(ContractModel):
    member_id: str
    member_kind: Literal["conversation_instance"]
    agent_ref: ResourceRef
    instance_name: str
    display_name: str | None = None
    role: str | None = None
    required: StrictBool

    @field_validator("member_id", "instance_name")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("display_name", "role")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @model_validator(mode="after")
    def _ref_is_agent(self):
        if self.agent_ref.resource_type != "agent":
            raise ValueError("group members must reference agents")
        return self


class GroupSelection(ContractModel):
    mode: Literal["all", "mentioned", "classifier"] = "all"
    classifier_service_role: str | None = None

    @field_validator("classifier_service_role")
    @classmethod
    def _optional_role(cls, value: str | None) -> str | None:
        return None if value is None else require_text(value, "classifier_service_role")

    @model_validator(mode="after")
    def _classifier_role_matches_mode(self):
        if (self.mode == "classifier") != (self.classifier_service_role is not None):
            raise ValueError("classifier mode alone requires classifier_service_role")
        return self


class GroupDeliberation(ContractModel):
    max_rounds: int = 0
    max_messages_per_member_per_round: int = 0
    max_total_participant_calls: int = 0
    max_parallelism: int = 0
    allow_pass: StrictBool = True
    rotate_first_speaker: StrictBool = True

    @field_validator(
        "max_rounds",
        "max_messages_per_member_per_round",
        "max_total_participant_calls",
        "max_parallelism",
        mode="before",
    )
    @classmethod
    def _limits_are_non_negative(cls, value: int, info) -> int:
        return require_non_negative(value, info.field_name)


class GroupContextPolicy(ContractModel):
    private_context: Literal["none"] = "none"
    shared_history_limit: int = 0
    attachments: Literal["explicit_only"] = "explicit_only"

    @field_validator("shared_history_limit", mode="before")
    @classmethod
    def _history_limit_is_non_negative(cls, value: int) -> int:
        return require_non_negative(value, "shared_history_limit")


class GroupToolPolicy(ContractModel):
    mode: Literal["none", "read_only", "declared"] = "none"
    allowed_effects: tuple[CapabilityEffect, ...] = ()

    @model_validator(mode="after")
    def _v1_policy_is_safe(self):
        if self.mode == "declared":
            raise ValueError("declared group tools are reserved and unavailable in version 1")
        if self.mode == "none" and self.allowed_effects:
            raise ValueError("tool mode none cannot allow effects")
        read_only = {
            CapabilityEffect.FILESYSTEM_READ, CapabilityEffect.NETWORK_READ,
            CapabilityEffect.BROWSER_OBSERVE, CapabilityEffect.DESKTOP_OBSERVE,
            CapabilityEffect.RESOURCE_READ,
        }
        if self.mode == "read_only" and (
            not self.allowed_effects or not set(self.allowed_effects) <= read_only
        ):
            raise ValueError("read_only group tools require only read-only effects")
        return self


class GroupSynthesis(ContractModel):
    mode: Literal["designated_member", "dedicated_llm_role", "deterministic_concat"]
    member_id: str | None = None
    llm_service_role: str | None = None

    @field_validator("member_id", "llm_service_role")
    @classmethod
    def _optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else require_text(value, info.field_name)

    @model_validator(mode="after")
    def _target_matches_mode(self):
        if self.mode == "designated_member" and not self.member_id:
            raise ValueError("designated_member synthesis requires member_id")
        if self.mode == "dedicated_llm_role" and not self.llm_service_role:
            raise ValueError("dedicated_llm_role synthesis requires llm_service_role")
        if self.mode == "deterministic_concat" and (
            self.member_id is not None or self.llm_service_role is not None
        ):
            raise ValueError("deterministic_concat accepts no synthesis target")
        return self


class GroupBudgets(ContractModel):
    max_tokens: int = 0
    max_cost: float | None = None
    timeout_seconds: int = 0

    @field_validator("max_tokens", "timeout_seconds", mode="before")
    @classmethod
    def _limits_are_non_negative(cls, value: int, info) -> int:
        return require_non_negative(value, info.field_name)

    @field_validator("max_cost", mode="before")
    @classmethod
    def _cost_is_positive_and_finite(cls, value: float | None) -> float | None:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
        ):
            raise ValueError("max_cost must be non-negative and finite")
        return value


class GroupOutput(ContractModel):
    include_attributions: StrictBool = True
    include_dissent: StrictBool = True


class AgentGroupDefinition(VersionedContract):
    name: str
    description: str
    members: tuple[AgentGroupMember, ...]
    selection: GroupSelection = GroupSelection()
    deliberation: GroupDeliberation = GroupDeliberation()
    context_policy: GroupContextPolicy = GroupContextPolicy()
    tool_policy: GroupToolPolicy = GroupToolPolicy()
    synthesis: GroupSynthesis
    budgets: GroupBudgets = GroupBudgets()
    output: GroupOutput = GroupOutput()

    @field_validator("name", "description")
    @classmethod
    def _required_text(cls, value: str, info) -> str:
        return require_text(value, info.field_name)

    @field_validator("members")
    @classmethod
    def _members_are_distinct(cls, value: tuple[AgentGroupMember, ...]):
        if len(value) < 2:
            raise ValueError("agent groups require at least two members")
        ids = [member.member_id.casefold() for member in value]
        instances = [member.instance_name.casefold() for member in value]
        if len(set(ids)) != len(ids) or len(set(instances)) != len(instances):
            raise ValueError("agent group members must be distinct")
        return value

    @model_validator(mode="after")
    def _synthesis_member_exists(self):
        if self.synthesis.member_id is not None and self.synthesis.member_id not in {
            member.member_id for member in self.members
        }:
            raise ValueError("synthesis member_id does not exist")
        return self


class ParticipantPost(VersionedContract):
    group_run_id: str
    round: int
    member_id: str
    member_snapshot_digest: str
    disposition: Literal["post", "pass"]
    content: str
    citations: tuple[dict[str, Any], ...] = ()
    confidence: float | None = None
    token_usage: dict[str, Any]
    created_at: str

    @field_validator("group_run_id")
    @classmethod
    def _run_id_is_uuid(cls, value: str) -> str:
        return require_uuid(value, "group_run_id")

    @field_validator("round", mode="before")
    @classmethod
    def _round_is_positive(cls, value: int) -> int:
        return require_positive(value, "round")

    @field_validator("member_id")
    @classmethod
    def _member_id_is_present(cls, value: str) -> str:
        return require_text(value, "member_id")

    @field_validator("member_snapshot_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        return require_sha256(value, "member_snapshot_digest")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_is_bounded(cls, value: float | None) -> float | None:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1
        ):
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: str) -> str:
        return require_utc_timestamp(value, "created_at")

    @model_validator(mode="after")
    def _content_matches_disposition(self):
        if self.disposition == "pass" and self.content:
            raise ValueError("pass participant posts cannot contain content")
        if self.disposition == "post" and not self.content.strip():
            raise ValueError("post participant posts require content")
        return self
