"""Normalize visible media services into immutable Media Studio snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.media_studio import (
    MediaCapability,
    canonical_digest,
    new_contract_id,
    utc_now,
)
from core.comfyui_workflow import ComfyWorkflowRevision
from core.service_definition_revision import compute_service_definition_revision


_OPERATION_ROLES = {
    "generate": (),
    "edit_image": (
        "source_image", "subject_reference", "style_reference",
        "composition_reference",
    ),
    "image_to_video": (
        "source_image", "subject_reference", "style_reference",
    ),
    "frame_to_video": ("start_frame", "end_frame", "source_image"),
    "reference_to_video": (
        "source_image", "subject_reference", "style_reference",
        "composition_reference",
    ),
    "video_edit": ("source_video",),
    "video_extend": ("source_video",),
    "generate_audio": ("source_audio", "music_bed"),
    "speak": (),
    "clone_voice": ("voice_reference",),
    "compose": (
        "source_image", "source_video", "source_audio", "music_bed",
        "sound_effect", "subtitle_source",
    ),
}
_OUTPUTS = {
    "image": ("image/png", "image/jpeg", "image/webp"),
    "video": ("video/mp4", "video/webm"),
    "audio": ("audio/wav", "audio/mpeg", "audio/flac", "audio/ogg"),
    "speech": ("audio/wav", "audio/mpeg", "audio/flac", "audio/ogg"),
    "voice_clone": ("audio/wav", "audio/mpeg", "audio/flac", "audio/ogg"),
    "compose": ("video/*", "audio/*", "image/*"),
}
_LOCAL_TYPES = frozenset({
    "comfyUIImageGeneration", "comfyUIVideoGeneration",
    "comfyUIAudioGeneration", "pocketTTS", "supertonicTTS", "voicebox",
    "luxTTS", "ffmpegMedia",
})


@dataclass(frozen=True)
class MediaCapabilitySnapshot:
    snapshot_id: str
    created_at: str
    user_id: str
    conversation_id: str
    capabilities: tuple[MediaCapability, ...]
    digest: str

    def __post_init__(self):
        if not self.snapshot_id or not self.created_at:
            raise ValueError("snapshot UUID and timestamp are required")
        if not self.user_id or not self.conversation_id:
            raise ValueError("snapshot user and conversation are required")
        ids = tuple(item.capability_id for item in self.capabilities)
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot capability IDs must be unique")
        expected = canonical_digest(self.digest_payload())
        if self.digest != expected:
            raise ValueError("snapshot digest does not match its capabilities")

    def digest_payload(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "capabilities": [
                capability_to_dict(item) for item in self.capabilities
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.digest_payload(), "digest": self.digest}

    @classmethod
    def create(
        cls, *, user_id: str, conversation_id: str,
        capabilities: Iterable[MediaCapability],
    ) -> "MediaCapabilitySnapshot":
        snapshot_id = new_contract_id("media_snapshot")
        created_at = utc_now()
        ordered = tuple(sorted(
            capabilities, key=lambda item: item.capability_id))
        payload = {
            "snapshot_id": snapshot_id,
            "created_at": created_at,
            "user_id": str(user_id or ""),
            "conversation_id": str(conversation_id or ""),
            "capabilities": [capability_to_dict(item) for item in ordered],
        }
        return cls(
            snapshot_id=snapshot_id,
            created_at=created_at,
            user_id=str(user_id or ""),
            conversation_id=str(conversation_id or ""),
            capabilities=ordered,
            digest=canonical_digest(payload),
        )


def capability_to_dict(capability: MediaCapability) -> dict[str, object]:
    return {
        "capability_id": capability.capability_id,
        "engine": capability.engine,
        "service_id": capability.service_id,
        "service_revision": capability.service_revision,
        "scope": capability.scope,
        "media_kinds": list(capability.media_kinds),
        "operations": list(capability.operations),
        "accepted_reference_roles": list(
            capability.accepted_reference_roles),
        "output_content_types": list(capability.output_content_types),
        "tags": list(capability.tags),
        "preset_id": capability.preset_id,
        "model": capability.model,
        "estimated_cost_usd": capability.estimated_cost_usd,
        "max_duration_seconds": capability.max_duration_seconds,
        "max_width": capability.max_width,
        "max_height": capability.max_height,
        "available": capability.available,
        "unavailable_reason": capability.unavailable_reason,
    }


def snapshot_media_capabilities(
    user_id: str,
    conversation_id: str,
    *,
    registry=None,
    service_factory=None,
) -> MediaCapabilitySnapshot:
    """Freeze visible enabled media definitions without connecting services."""

    user_id = str(user_id or "").strip()
    conversation_id = str(conversation_id or "").strip()
    if not user_id or not conversation_id:
        raise ValueError("user_id and conversation_id are required")
    if registry is None:
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
    if service_factory is None:
        from core import ServiceFactory
        service_factory = ServiceFactory

    definitions = registry.resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    capabilities: list[MediaCapability] = []
    for definition in definitions.values():
        capabilities.extend(_definition_capabilities(
            definition, service_factory=service_factory))
    return MediaCapabilitySnapshot.create(
        user_id=user_id,
        conversation_id=conversation_id,
        capabilities=capabilities,
    )


def _definition_capabilities(
    definition: Any, *, service_factory,
) -> list[MediaCapability]:
    service_type = str(getattr(definition, "service_type", "") or "")
    service_id = str(getattr(definition, "service_id", "") or "")
    config = dict(getattr(definition, "config", {}) or {})
    scope = str(getattr(definition, "scope", "") or "")
    if not service_type or not service_id or scope not in {"global", "user", "conv"}:
        return []
    try:
        service_class = service_factory.get(service_type)
    except Exception:
        return []

    revision = compute_service_definition_revision(definition)
    media_shapes = _media_shapes(service_class, service_type, config)
    results: list[MediaCapability] = []
    for media_kind, operation, metadata in media_shapes:
        tags = _tags(service_type, config, metadata)
        constraints = _constraints(config, metadata)
        preset_id = str(metadata.get("preset_id") or "")
        model = str(metadata.get("model") or config.get("model") or "")
        results.append(MediaCapability(
            capability_id=_capability_id(
                scope, getattr(definition, "scope_id", ""), service_id,
                operation, preset_id),
            engine=_engine(service_type, service_class),
            service_id=service_id,
            service_revision=revision,
            scope=scope,
            media_kinds=(media_kind,),
            operations=(operation,),
            accepted_reference_roles=tuple(
                _OPERATION_ROLES.get(operation, ())),
            output_content_types=_output_types(media_kind, metadata),
            tags=tags,
            preset_id=preset_id,
            model=model,
            estimated_cost_usd=_cost(config, metadata),
            max_duration_seconds=constraints.get("max_duration_seconds"),
            max_width=constraints.get("max_width"),
            max_height=constraints.get("max_height"),
        ))
    return results


def _media_shapes(
    service_class: type, service_type: str, config: dict,
) -> list[tuple[str, str, dict]]:
    custom = getattr(service_class, "media_capability_definitions", None)
    if callable(custom):
        rows = custom(config)
        return [
            (
                str(row["media_kind"]),
                str(row["operation"]),
                dict(row),
            )
            for row in rows
        ]

    if service_type.startswith("comfyUI"):
        if "Image" in service_type:
            media_kind = "image"
        elif "Video" in service_type:
            media_kind = "video"
        elif "Audio" in service_type:
            media_kind = "audio"
        else:
            return []
        workflows = config.get("workflows")
        if not isinstance(workflows, dict):
            return []
        results = []
        for operation, preset in sorted(workflows.items()):
            try:
                revision = ComfyWorkflowRevision.from_preset(
                    operation, preset, media_kind=media_kind)
            except ValueError:
                continue
            metadata = revision.capability_metadata()
            output = preset.get("output")
            if isinstance(output, dict):
                metadata["output_content_types"] = output.get("content_types")
            results.append((media_kind, str(operation), metadata))
        return results

    names = {base.__name__ for base in service_class.__mro__}
    shapes: list[tuple[str, str, dict]] = []
    if "BaseVoiceCloneService" in names:
        shapes.extend([
            ("voice_clone", "clone_voice", {}),
            ("speech", "speak", {}),
        ])
    elif "BaseTTSService" in names:
        shapes.append(("speech", "speak", {}))
    if "BaseImageGenerationService" in names:
        shapes.append(("image", "generate", {}))
        if callable(getattr(service_class, "edit_image", None)):
            shapes.append(("image", "edit_image", {}))
    if "BaseVideoGenerationService" in names:
        for operation in (
            "generate", "image_to_video", "frame_to_video",
            "reference_to_video", "video_edit", "video_extend",
        ):
            if callable(getattr(service_class, operation, None)):
                shapes.append(("video", operation, {}))
    if "BaseAudioGenerationService" in names:
        shapes.append(("audio", "generate_audio", {}))
    if service_type == "ffmpegMedia":
        shapes.append(("compose", "compose", {}))
    unique = {}
    for row in shapes:
        unique[(row[0], row[1])] = row
    return list(unique.values())


def _tags(service_type: str, config: dict, metadata: dict) -> tuple[str, ...]:
    values: list[str] = []
    base_url = str(config.get("base_url") or "").lower()
    local = (
        service_type in _LOCAL_TYPES
        or bool(config.get("local"))
        or base_url.startswith("relay://")
        or "localhost" in base_url
        or "127.0.0.1" in base_url
    )
    values.append("local" if local else "remote")
    values.append("private" if local else "external")
    for source in (config.get("media_tags"), metadata.get("tags")):
        if isinstance(source, (list, tuple)):
            values.extend(str(item).strip() for item in source if str(item).strip())
    return tuple(dict.fromkeys(values))


def _constraints(config: dict, metadata: dict) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in (config.get("media_constraints"), metadata.get("constraints")):
        if isinstance(source, dict):
            merged.update(source)
    result = {}
    for name in ("max_duration_seconds", "max_width", "max_height"):
        value = merged.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[name] = value
    return result


def _cost(config: dict, metadata: dict) -> float | None:
    value = metadata.get(
        "estimated_cost_usd", config.get("estimated_cost_usd"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _output_types(media_kind: str, metadata: dict) -> tuple[str, ...]:
    values = metadata.get("output_content_types")
    if isinstance(values, (list, tuple)) and values:
        return tuple(str(item) for item in values)
    return _OUTPUTS[media_kind]


def _engine(service_type: str, service_class: type) -> str:
    if service_type.startswith("comfyUI"):
        return "comfyui"
    if service_type == "ffmpegMedia":
        return "ffmpeg"
    provider = str(getattr(service_class, "PROVIDER", "") or "").strip()
    return provider or service_type


def _capability_id(
    scope: str, scope_id: object, service_id: str,
    operation: str, preset_id: str,
) -> str:
    return ":".join((
        scope,
        str(scope_id or "__global__"),
        service_id,
        operation,
        preset_id or "-",
    ))
