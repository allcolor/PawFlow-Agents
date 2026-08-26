"""Immutable Media Studio capability contracts and deterministic routing."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


MEDIA_KINDS = frozenset({
    "image", "video", "audio", "speech", "voice_clone", "compose",
})
QUESTION_MODES = frozenset({"automatic", "ask_on_tradeoff", "always_ask"})
LOCAL_PREFERENCES = frozenset({"any", "local", "remote"})
QUALITY_PREFERENCES = frozenset({"balanced", "quality", "speed"})
INTENT_KINDS = frozenset({
    "unsupported", "image", "video", "audio", "speech", "voice_clone",
    "compose", "composite",
})
REFERENCE_ROLES = frozenset({
    "subject_reference", "style_reference", "composition_reference",
    "source_image", "start_frame", "end_frame", "source_video",
    "source_audio", "voice_reference", "music_bed", "sound_effect",
    "subtitle_source",
})


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _unique(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    if any(not str(value or "").strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


def utc_now(timestamp: float | None = None) -> str:
    """Return an explicit timezone-aware timestamp for persisted contracts."""

    value = time.time() if timestamp is None else float(timestamp)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timestamp must be finite and positive")
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def new_contract_id(prefix: str) -> str:
    return f"{_required(prefix, 'prefix')}_{uuid.uuid4()}"


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_contract_identity(identifier: str, created_at: str, name: str) -> None:
    identifier = _required(identifier, f"{name}_id")
    if "_" not in identifier:
        raise ValueError(f"{name}_id must include a type prefix")
    timestamp = _required(created_at, "created_at")
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError("created_at must include a timezone")


@dataclass(frozen=True)
class MediaIntent:
    """Strict classification result produced before media/project access."""

    intent_id: str
    created_at: str
    kind: str
    operation: str
    confidence: float
    explanation: str
    requires_references: bool
    requires_scenario: bool
    missing_fields: tuple[str, ...] = ()
    requested_project_id: str = ""
    revision_selector: str = ""

    def __post_init__(self):
        _validate_contract_identity(self.intent_id, self.created_at, "intent")
        if self.kind not in INTENT_KINDS:
            raise ValueError("intent kind is unsupported")
        _required(self.operation, "operation")
        if not math.isfinite(float(self.confidence)):
            raise ValueError("confidence must be finite")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        _required(self.explanation, "explanation")
        _unique(self.missing_fields, "missing_fields")

    @classmethod
    def create(cls, **values) -> "MediaIntent":
        return cls(
            intent_id=new_contract_id("intent"),
            created_at=utc_now(),
            **values,
        )


@dataclass(frozen=True)
class MediaReference:
    """One owner-authorized media artifact with an explicit creative role."""

    reference_id: str
    created_at: str
    role: str
    file_id: str
    filename: str
    content_type: str
    source_message_id: str
    revision_id: str = ""
    source_relay_id: str = ""
    source_path: str = ""

    def __post_init__(self):
        _validate_contract_identity(
            self.reference_id, self.created_at, "reference")
        if self.role not in REFERENCE_ROLES:
            raise ValueError("reference role is unsupported")
        for name in ("file_id", "filename", "content_type", "source_message_id"):
            _required(getattr(self, name), name)
        if bool(self.source_relay_id) != bool(self.source_path):
            raise ValueError(
                "source_relay_id and source_path must be provided together")

    @classmethod
    def create(cls, **values) -> "MediaReference":
        return cls(
            reference_id=new_contract_id("reference"),
            created_at=utc_now(),
            **values,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "created_at": self.created_at,
            "role": self.role,
            "file_id": self.file_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "source_message_id": self.source_message_id,
            "revision_id": self.revision_id,
            "source_relay_id": self.source_relay_id,
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class CreativeBrief:
    """Versioned creative request that preserves user text and assumptions."""

    brief_id: str
    created_at: str
    media_kind: str
    operation: str
    objective: str
    prompt_original: str
    prompt_refined: str
    negative_prompt: str = ""
    style: str = ""
    composition: str = ""
    motion: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    aspect_ratio: str = ""
    references: tuple[MediaReference, ...] = ()
    assumptions: tuple[str, ...] = ()
    exact_prompt: bool = False
    audio: dict[str, object] = field(default_factory=dict)
    output: dict[str, object] = field(default_factory=dict)

    def __post_init__(self):
        _validate_contract_identity(self.brief_id, self.created_at, "brief")
        if self.media_kind not in MEDIA_KINDS:
            raise ValueError("brief media_kind is unsupported")
        for name in ("operation", "objective", "prompt_original"):
            _required(getattr(self, name), name)
        if not self.exact_prompt:
            _required(self.prompt_refined, "prompt_refined")
        if self.exact_prompt and self.prompt_refined != self.prompt_original:
            raise ValueError(
                "exact_prompt requires prompt_refined to equal prompt_original")
        _unique(self.assumptions, "assumptions")
        if self.duration_seconds is not None:
            if (not math.isfinite(float(self.duration_seconds))
                    or self.duration_seconds <= 0):
                raise ValueError("duration_seconds must be positive")
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")
        reference_ids = tuple(item.reference_id for item in self.references)
        _unique(reference_ids, "references")

    @classmethod
    def create(cls, **values) -> "CreativeBrief":
        return cls(
            brief_id=new_contract_id("brief"),
            created_at=utc_now(),
            **values,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "brief_id": self.brief_id,
            "created_at": self.created_at,
            "media_kind": self.media_kind,
            "operation": self.operation,
            "objective": self.objective,
            "prompt_original": self.prompt_original,
            "prompt_refined": self.prompt_refined,
            "negative_prompt": self.negative_prompt,
            "style": self.style,
            "composition": self.composition,
            "motion": self.motion,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "references": [item.to_dict() for item in self.references],
            "assumptions": list(self.assumptions),
            "exact_prompt": self.exact_prompt,
            "audio": dict(self.audio),
            "output": dict(self.output),
        }


@dataclass(frozen=True)
class MediaProductionProposal:
    """Creative and technical proposal that must be approved by digest."""

    proposal_id: str
    created_at: str
    project_id: str
    parent_revision_id: str
    title: str
    creative_direction: str
    shots: tuple[dict[str, object], ...]
    audio: dict[str, object] = field(default_factory=dict)
    postproduction: dict[str, object] = field(default_factory=dict)
    engine_choices: tuple[dict[str, object], ...] = ()
    missing_assets: tuple[str, ...] = ()
    estimated_cost: dict[str, object] = field(default_factory=dict)
    estimated_duration: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    digest: str = ""

    def __post_init__(self):
        _validate_contract_identity(
            self.proposal_id, self.created_at, "proposal")
        _required(self.project_id, "project_id")
        _required(self.title, "title")
        _required(self.creative_direction, "creative_direction")
        if not self.shots:
            raise ValueError("shots is required")
        for index, shot in enumerate(self.shots):
            if not isinstance(shot, dict) or not str(shot.get("id") or ""):
                raise ValueError(f"shots[{index}].id is required")
            duration = shot.get("duration_seconds")
            if (not isinstance(duration, (int, float))
                    or isinstance(duration, bool) or duration <= 0):
                raise ValueError(
                    f"shots[{index}].duration_seconds must be positive")
        expected = canonical_digest(self.digest_payload())
        if self.digest and self.digest != expected:
            raise ValueError("proposal digest does not match its content")
        object.__setattr__(self, "digest", expected)

    def digest_payload(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "project_id": self.project_id,
            "parent_revision_id": self.parent_revision_id,
            "title": self.title,
            "creative_direction": self.creative_direction,
            "shots": list(self.shots),
            "audio": self.audio,
            "postproduction": self.postproduction,
            "engine_choices": list(self.engine_choices),
            "missing_assets": list(self.missing_assets),
            "estimated_cost": self.estimated_cost,
            "estimated_duration": self.estimated_duration,
            "warnings": list(self.warnings),
            "approvals": list(self.approvals),
        }

    @classmethod
    def create(cls, **values) -> "MediaProductionProposal":
        return cls(
            proposal_id=new_contract_id("proposal"),
            created_at=utc_now(),
            **values,
        )

    def to_dict(self) -> dict[str, object]:
        return {**self.digest_payload(), "digest": self.digest}


@dataclass(frozen=True)
class MediaCapability:
    """One immutable capability in a bounded Media Studio snapshot."""

    capability_id: str
    engine: str
    service_id: str
    service_revision: str
    scope: str
    media_kinds: tuple[str, ...]
    operations: tuple[str, ...]
    accepted_reference_roles: tuple[str, ...] = ()
    output_content_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    preset_id: str = ""
    model: str = ""
    estimated_cost_usd: float | None = None
    max_duration_seconds: float | None = None
    max_width: int | None = None
    max_height: int | None = None
    available: bool = True
    unavailable_reason: str = ""

    def __post_init__(self):
        for name in (
            "capability_id", "engine", "service_id", "service_revision", "scope"
        ):
            _required(getattr(self, name), name)
        if self.scope not in {"global", "user", "conv"}:
            raise ValueError("scope must be global, user, or conv")
        _unique(self.media_kinds, "media_kinds")
        _unique(self.operations, "operations")
        _unique(self.accepted_reference_roles, "accepted_reference_roles")
        _unique(self.output_content_types, "output_content_types")
        _unique(self.tags, "tags")
        if not self.media_kinds:
            raise ValueError("media_kinds is required")
        if not set(self.media_kinds) <= MEDIA_KINDS:
            raise ValueError("media_kinds contains an unsupported kind")
        if not self.operations:
            raise ValueError("operations is required")
        if not self.output_content_types:
            raise ValueError("output_content_types is required")
        if self.estimated_cost_usd is not None:
            if not math.isfinite(float(self.estimated_cost_usd)):
                raise ValueError("estimated_cost_usd must be finite")
            if self.estimated_cost_usd < 0:
                raise ValueError("estimated_cost_usd must not be negative")
        if self.max_duration_seconds is not None:
            if (not math.isfinite(float(self.max_duration_seconds))
                    or self.max_duration_seconds <= 0):
                raise ValueError("max_duration_seconds must be positive")
        for name in ("max_width", "max_height"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable capabilities require unavailable_reason")
        if self.available and self.unavailable_reason:
            raise ValueError("available capabilities cannot have unavailable_reason")

    @property
    def is_local(self) -> bool:
        return "local" in self.tags


@dataclass(frozen=True)
class MediaSelectionRequest:
    """Hard requirements for one Media Studio capability selection."""

    media_kind: str
    operation: str
    required_reference_roles: tuple[str, ...] = ()
    output_content_type: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self):
        _required(self.media_kind, "media_kind")
        _required(self.operation, "operation")
        if self.media_kind not in MEDIA_KINDS:
            raise ValueError("media_kind is unsupported")
        _unique(self.required_reference_roles, "required_reference_roles")
        if self.duration_seconds is not None:
            if (not math.isfinite(float(self.duration_seconds))
                    or self.duration_seconds <= 0):
                raise ValueError("duration_seconds must be positive")
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class MediaSelectionPreferences:
    """Soft routing preferences plus user-controlled hard policy limits."""

    question_mode: str = "ask_on_tradeoff"
    local_preference: str = "any"
    quality_preference: str = "balanced"
    max_cost_usd: float | None = None
    allow_remote: bool = True
    model: str = ""
    preset_id: str = ""

    def __post_init__(self):
        if self.question_mode not in QUESTION_MODES:
            raise ValueError("question_mode is invalid")
        if self.local_preference not in LOCAL_PREFERENCES:
            raise ValueError("local_preference is invalid")
        if self.quality_preference not in QUALITY_PREFERENCES:
            raise ValueError("quality_preference is invalid")
        if self.max_cost_usd is not None:
            if (not math.isfinite(float(self.max_cost_usd))
                    or self.max_cost_usd < 0):
                raise ValueError("max_cost_usd must be finite and non-negative")
            if self.max_cost_usd == 0:
                object.__setattr__(self, "max_cost_usd", None)


@dataclass(frozen=True)
class CandidateRejection:
    capability_id: str
    reason_code: str


@dataclass(frozen=True)
class MediaSelection:
    """Auditable selection, user-choice request, or unavailable result."""

    selected: MediaCapability | None
    alternatives: tuple[MediaCapability, ...] = ()
    rejected: tuple[CandidateRejection, ...] = ()
    requires_user_choice: bool = False
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self):
        if self.requires_user_choice and self.selected is not None:
            raise ValueError("a user-choice result cannot preselect a capability")
        if self.requires_user_choice and len(self.alternatives) < 2:
            raise ValueError("a user-choice result requires at least two alternatives")

    @property
    def outcome(self) -> str:
        if self.requires_user_choice:
            return "user_choice"
        if self.selected is not None:
            return "selected"
        return "unavailable"


@dataclass(frozen=True)
class _RankedCapability:
    capability: MediaCapability
    score: int
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


class MediaCapabilityCatalog:
    """Select a capability without consulting an LLM or mutable live state."""

    def __init__(self, capabilities: Iterable[MediaCapability]):
        entries = tuple(capabilities)
        ids = [entry.capability_id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("capability_id must be unique in one snapshot")
        self._capabilities = tuple(
            sorted(entries, key=lambda item: item.capability_id)
        )

    @property
    def capabilities(self) -> tuple[MediaCapability, ...]:
        return self._capabilities

    def select(
        self,
        request: MediaSelectionRequest,
        preferences: MediaSelectionPreferences | None = None,
    ) -> MediaSelection:
        preferences = preferences or MediaSelectionPreferences()
        ranked: list[_RankedCapability] = []
        rejected: list[CandidateRejection] = []

        for capability in self._capabilities:
            reason = self._reject_reason(capability, request, preferences)
            if reason:
                rejected.append(CandidateRejection(
                    capability.capability_id, reason
                ))
                continue
            ranked.append(self._rank(capability, preferences))

        ranked.sort(key=lambda item: (
            -item.score,
            (item.capability.estimated_cost_usd is None,
             item.capability.estimated_cost_usd or 0),
            item.capability.capability_id,
        ))
        rejected_result = tuple(rejected)
        if not ranked:
            return MediaSelection(
                selected=None,
                rejected=rejected_result,
                reason_codes=("no_compatible_capability",),
            )

        alternatives = tuple(item.capability for item in ranked)
        if self._requires_choice(ranked, preferences):
            return MediaSelection(
                selected=None,
                alternatives=alternatives,
                rejected=rejected_result,
                requires_user_choice=True,
                reason_codes=("material_tradeoff",),
            )

        winner = ranked[0]
        return MediaSelection(
            selected=winner.capability,
            alternatives=tuple(item.capability for item in ranked[1:]),
            rejected=rejected_result,
            reason_codes=(
                "supports_media_kind",
                "supports_operation",
                *winner.reason_codes,
            ),
        )

    @staticmethod
    def _reject_reason(
        capability: MediaCapability,
        request: MediaSelectionRequest,
        preferences: MediaSelectionPreferences,
    ) -> str:
        if not capability.available:
            return "unavailable"
        if request.media_kind not in capability.media_kinds:
            return "media_kind_mismatch"
        if request.operation not in capability.operations:
            return "operation_mismatch"
        if not set(request.required_reference_roles) <= set(
                capability.accepted_reference_roles):
            return "reference_role_mismatch"
        if (request.output_content_type
                and not MediaCapabilityCatalog._supports_output(
                    capability, request.output_content_type)):
            return "output_content_type_mismatch"
        if (request.duration_seconds is not None
                and capability.max_duration_seconds is not None
                and request.duration_seconds > capability.max_duration_seconds):
            return "duration_limit_exceeded"
        if (request.width is not None and capability.max_width is not None
                and request.width > capability.max_width):
            return "width_limit_exceeded"
        if (request.height is not None and capability.max_height is not None
                and request.height > capability.max_height):
            return "height_limit_exceeded"
        if not preferences.allow_remote and not capability.is_local:
            return "remote_disallowed"
        if preferences.max_cost_usd is not None:
            if capability.estimated_cost_usd is None:
                return "cost_unknown"
            if capability.estimated_cost_usd > preferences.max_cost_usd:
                return "cost_limit_exceeded"
        if preferences.model and capability.model != preferences.model:
            return "model_mismatch"
        if preferences.preset_id and capability.preset_id != preferences.preset_id:
            return "preset_mismatch"
        return ""

    @staticmethod
    def _rank(
        capability: MediaCapability,
        preferences: MediaSelectionPreferences,
    ) -> _RankedCapability:
        score = 0
        reasons: list[str] = []
        if preferences.model:
            score += 1000
            reasons.append("requested_model")
        if preferences.preset_id:
            score += 1000
            reasons.append("requested_preset")
        if preferences.local_preference != "any":
            wants_local = preferences.local_preference == "local"
            if capability.is_local == wants_local:
                score += 30
                reasons.append(f"{preferences.local_preference}_preferred")
        if preferences.quality_preference == "quality" and "high_quality" in capability.tags:
            score += 20
            reasons.append("quality_preferred")
        if preferences.quality_preference == "speed" and "fast" in capability.tags:
            score += 20
            reasons.append("speed_preferred")
        if capability.estimated_cost_usd == 0:
            score += 1
            reasons.append("no_estimated_cost")
        return _RankedCapability(capability, score, tuple(reasons))

    @classmethod
    def _requires_choice(
        cls,
        ranked: list[_RankedCapability],
        preferences: MediaSelectionPreferences,
    ) -> bool:
        if len(ranked) < 2 or preferences.question_mode == "automatic":
            return False
        if preferences.question_mode == "always_ask":
            return True
        first, second = ranked[:2]
        if abs(first.score - second.score) > 20:
            return False
        return cls._has_material_tradeoff(first.capability, second.capability)

    @staticmethod
    def _has_material_tradeoff(
        first: MediaCapability,
        second: MediaCapability,
    ) -> bool:
        if first.is_local != second.is_local:
            return True
        if (
            first.estimated_cost_usd is not None
            and second.estimated_cost_usd is not None
            and first.estimated_cost_usd != second.estimated_cost_usd
        ):
            quality_differs = (
                ("high_quality" in first.tags)
                != ("high_quality" in second.tags)
            )
            if quality_differs:
                return True
        return (
            ("high_quality" in first.tags and "fast" in second.tags)
            or ("fast" in first.tags and "high_quality" in second.tags)
        )

    @staticmethod
    def _supports_output(
        capability: MediaCapability, content_type: str,
    ) -> bool:
        for declared in capability.output_content_types:
            if declared == content_type:
                return True
            if declared.endswith("/*") and content_type.startswith(
                    declared[:-1]):
                return True
        return False
