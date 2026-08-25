"""Shared, versioned contracts for agent runtimes and tool execution.

These models are deliberately side-effect free. Runtime integration is gated by
later work packages; importing this module cannot change existing agent behavior.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractModel(BaseModel):
    """Immutable JSON contract that rejects fields it does not understand."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, value: Any):
        if not isinstance(value, dict):
            raise TypeError(f"{cls.__name__} must be an object")
        return cls.model_validate(value)


class VersionedContract(ContractModel):
    schema_version: Literal[1] = 1


class CapabilityEffect(str, Enum):
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    PROCESS_EXECUTE = "process.execute"
    NETWORK_READ = "network.read"
    NETWORK_WRITE = "network.write"
    BROWSER_OBSERVE = "browser.observe"
    BROWSER_CONTROL = "browser.control"
    DESKTOP_OBSERVE = "desktop.observe"
    DESKTOP_CONTROL = "desktop.control"
    MESSAGING_SEND = "messaging.send"
    RESOURCE_READ = "resource.read"
    RESOURCE_WRITE = "resource.write"
    SECRET_USE = "secret.use"  # nosec B105 - capability identifier, not a password
    SECRET_WRITE = "secret.write"  # nosec B105 - capability identifier, not a password
    AGENT_SPAWN = "agent.spawn"
    AGENT_CONTROL = "agent.control"
    WORKFLOW_EXECUTE = "workflow.execute"
    EXTERNAL_SIDE_EFFECT = "external.side_effect"


class IdempotencyClass(str, Enum):
    PURE = "pure"
    NATURAL = "natural"
    RUN_CACHED = "run_cached"
    KEYED_EFFECT = "keyed_effect"
    UNSAFE = "unsafe"


READ_ONLY_EFFECTS = frozenset({
    CapabilityEffect.FILESYSTEM_READ,
    CapabilityEffect.NETWORK_READ,
    CapabilityEffect.BROWSER_OBSERVE,
    CapabilityEffect.DESKTOP_OBSERVE,
    CapabilityEffect.RESOURCE_READ,
})
DESTRUCTIVE_EFFECTS = frozenset({
    CapabilityEffect.FILESYSTEM_WRITE,
    CapabilityEffect.PROCESS_EXECUTE,
    CapabilityEffect.NETWORK_WRITE,
    CapabilityEffect.BROWSER_CONTROL,
    CapabilityEffect.DESKTOP_CONTROL,
    CapabilityEffect.MESSAGING_SEND,
    CapabilityEffect.RESOURCE_WRITE,
    CapabilityEffect.SECRET_WRITE,
    CapabilityEffect.AGENT_SPAWN,
    CapabilityEffect.AGENT_CONTROL,
    CapabilityEffect.WORKFLOW_EXECUTE,
    CapabilityEffect.EXTERNAL_SIDE_EFFECT,
})


class CapabilityMetadata(VersionedContract):
    """Declared effects and retry safety for one tool or workflow task."""

    effects: tuple[CapabilityEffect, ...]
    read_only: StrictBool
    destructive: StrictBool
    idempotency: IdempotencyClass
    open_world: StrictBool = False
    authorization_target_kind: str
    workflow_safe: StrictBool = False
    group_safe: StrictBool = False

    @field_validator("effects")
    @classmethod
    def _effects_are_non_empty_and_unique(cls, value):
        if not value:
            raise ValueError("at least one capability effect is required")
        if len(set(value)) != len(value):
            raise ValueError("capability effects must be unique")
        return value

    @field_validator("authorization_target_kind")
    @classmethod
    def _target_kind_is_present(cls, value: str) -> str:
        return require_text(value, "authorization_target_kind")

    @model_validator(mode="after")
    def _derived_flags_are_consistent(self):
        effects = frozenset(self.effects)
        expected_read_only = effects <= READ_ONLY_EFFECTS
        expected_destructive = bool(effects & DESTRUCTIVE_EFFECTS)
        if self.read_only != expected_read_only:
            raise ValueError("read_only contradicts the declared effects")
        if self.destructive != expected_destructive:
            raise ValueError("destructive contradicts the declared effects")
        if self.group_safe and (not self.read_only or self.open_world):
            raise ValueError("group-safe capability metadata must be bounded and read-only")
        if self.workflow_safe and self.idempotency is IdempotencyClass.UNSAFE:
            raise ValueError("unsafe effects cannot be declared workflow-safe")
        return self


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def require_positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def require_non_negative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def require_uuid(value: Any, field: str) -> str:
    value = require_text(value, field)
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    return value


def require_sha256(value: Any, field: str) -> str:
    value = require_text(value, field)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def require_utc_timestamp(value: Any, field: str) -> str:
    value = require_text(value, field)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must use UTC")
    return value


class AuthorizationRefContract(ContractModel):
    context_id: str
    revision: int
    root_turn_id: str

    _context_id = field_validator("context_id")(
        lambda value: require_uuid(value, "context_id"))
    _revision = field_validator("revision")(
        lambda value: require_positive(value, "revision"))
    _root_turn_id = field_validator("root_turn_id")(
        lambda value: require_text(value, "root_turn_id"))
