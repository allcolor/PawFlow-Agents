"""Protocol-neutral types for inbound standard agent API turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


DIALECTS = frozenset({
    "chat_completions",
    "responses",
    "anthropic_messages",
})

VISIBLE_ITEM_KINDS = frozenset({
    "client_instruction",
    "user_message",
    "assistant_message",
    "client_tool_call_batch",
    "client_tool_result_batch",
    "response_output",
})


@dataclass(frozen=True)
class StandardApiNamespace:
    """Every field that isolates content-addressed API state."""

    publication_id: str
    api_generation: int
    key_id: str
    dialect: str
    api_model_id: str
    canonicalization_version: int = 1
    hash_secret_version: int = 1

    def __post_init__(self) -> None:
        for field_name in ("publication_id", "key_id", "api_model_id"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if self.dialect not in DIALECTS:
            raise ValueError(f"Unsupported standard API dialect: {self.dialect}")
        for field_name in (
                "api_generation", "canonicalization_version",
                "hash_secret_version"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "api_generation": self.api_generation,
            "key_id": self.key_id,
            "dialect": self.dialect,
            "api_model_id": self.api_model_id,
            "canonicalization_version": self.canonicalization_version,
            "hash_secret_version": self.hash_secret_version,
        }


@dataclass(frozen=True)
class NormalizedVisibleItem:
    """One client-visible semantic item after dialect validation."""

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VISIBLE_ITEM_KINDS:
            raise ValueError(f"Unsupported normalized item kind: {self.kind}")
        if not isinstance(self.data, Mapping):
            raise ValueError("Normalized visible item data must be an object")


@dataclass(frozen=True)
class NormalizedApiTurn:
    """Transport-neutral request passed from a dialect into session resolution."""

    namespace: StandardApiNamespace
    visible_items: Tuple[NormalizedVisibleItem, ...]
    actionable_suffix_start: int
    request_id: str
    body_fingerprint: str
    client_tools: Tuple[Mapping[str, Any], ...] = ()
    tool_choice: Any = "auto"
    stream: bool = False
    provider_overrides: Mapping[str, Any] = field(default_factory=dict)
    attachments: Tuple[Mapping[str, Any], ...] = ()
    previous_response_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, StandardApiNamespace):
            raise ValueError("namespace must be a StandardApiNamespace")
        if not isinstance(self.visible_items, tuple):
            object.__setattr__(self, "visible_items", tuple(self.visible_items))
        if not 0 <= self.actionable_suffix_start <= len(self.visible_items):
            raise ValueError(
                "actionable_suffix_start is outside visible_items")
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        if not str(self.body_fingerprint or "").strip():
            raise ValueError("body_fingerprint is required")
        if not isinstance(self.stream, bool):
            raise ValueError("stream must be a boolean")


@dataclass(frozen=True)
class ApiTurnResolution:
    """Resolved session/run plus the items the runtime must ingest."""

    outcome: str
    session: Mapping[str, Any]
    run: Mapping[str, Any]
    lease_id: str
    matched_item_count: int
    ingress_items: Tuple[NormalizedVisibleItem, ...]
    lookup_status: str
    checkpoint_unavailable: bool = False
