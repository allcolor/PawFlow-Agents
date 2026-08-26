"""Immutable contracts for reviewed ComfyUI workflow preset revisions."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


_MEDIA_KINDS = frozenset({"image", "video", "audio"})
_INVENTORY_KINDS = ("nodes", "models", "loras", "custom_nodes")
_ASSET_KINDS = frozenset({"model", "lora", "custom_node"})


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    values = tuple(_required(item, name) for item in value)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    return values


def _timestamp(value: object, name: str) -> str:
    text = _required(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return text


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ComfyWorkflowRevision:
    """One reviewed API-format preset revision pinned by a content digest."""

    operation: str
    preset_id: str
    revision: str
    created_at: str
    media_kind: str
    source: str
    license: str
    capabilities: tuple[str, ...]
    limits: tuple[tuple[str, int | float], ...]
    required_nodes: tuple[str, ...]
    required_models: tuple[str, ...]
    required_loras: tuple[str, ...]
    required_custom_nodes: tuple[str, ...]
    model: str
    estimated_cost_usd: float | None
    digest: str

    @classmethod
    def from_preset(
        cls, operation: object, preset: object, *, media_kind: str,
    ) -> "ComfyWorkflowRevision":
        operation_text = _required(operation, "operation")
        if media_kind not in _MEDIA_KINDS:
            raise ValueError("ComfyUI media_kind must be image, video, or audio")
        if not isinstance(preset, dict):
            raise ValueError(f"workflows.{operation_text} must be an object")
        metadata = preset.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError(
                f"workflows.{operation_text}.metadata must be an object")
        declared_kind = _required(
            metadata.get("media_kind"),
            f"workflows.{operation_text}.metadata.media_kind",
        )
        if declared_kind != media_kind:
            raise ValueError(
                f"workflows.{operation_text}.metadata.media_kind must be {media_kind}")
        provenance = metadata.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(
                f"workflows.{operation_text}.metadata.provenance must be an object")
        inventory = metadata.get("required_inventory")
        if not isinstance(inventory, dict):
            raise ValueError(
                f"workflows.{operation_text}.metadata.required_inventory must be an object")
        unknown_inventory = set(inventory) - set(_INVENTORY_KINDS)
        if unknown_inventory:
            raise ValueError(
                f"workflows.{operation_text}.metadata.required_inventory contains "
                f"unsupported keys: {', '.join(sorted(unknown_inventory))}")
        capabilities = _string_tuple(
            metadata.get("capabilities"),
            f"workflows.{operation_text}.metadata.capabilities",
        )
        limits_value = metadata.get("limits")
        if not isinstance(limits_value, dict):
            raise ValueError(
                f"workflows.{operation_text}.metadata.limits must be an object")
        allowed_limits = {
            "max_duration_seconds", "max_width", "max_height", "max_count",
        }
        unknown_limits = set(limits_value) - allowed_limits
        if unknown_limits:
            raise ValueError(
                f"workflows.{operation_text}.metadata.limits contains unsupported keys: "
                f"{', '.join(sorted(unknown_limits))}")
        limits: list[tuple[str, int | float]] = []
        for name, value in sorted(limits_value.items()):
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or value <= 0):
                raise ValueError(
                    f"workflows.{operation_text}.metadata.limits.{name} must be positive")
            limits.append((name, value))
        estimated_cost = metadata.get("estimated_cost_usd")
        if estimated_cost is not None:
            if (not isinstance(estimated_cost, (int, float))
                    or isinstance(estimated_cost, bool)
                    or not math.isfinite(float(estimated_cost))
                    or estimated_cost < 0):
                raise ValueError(
                    f"workflows.{operation_text}.metadata.estimated_cost_usd "
                    "must be finite and non-negative")
            estimated_cost = float(estimated_cost)
        payload = {
            "operation": operation_text,
            "workflow": preset.get("workflow"),
            "bindings": preset.get("bindings", {}),
            "output": preset.get("output"),
            "metadata": metadata,
        }
        return cls(
            operation=operation_text,
            preset_id=_required(
                metadata.get("preset_id"),
                f"workflows.{operation_text}.metadata.preset_id"),
            revision=_required(
                metadata.get("revision"),
                f"workflows.{operation_text}.metadata.revision"),
            created_at=_timestamp(
                metadata.get("created_at"),
                f"workflows.{operation_text}.metadata.created_at"),
            media_kind=declared_kind,
            source=_required(
                provenance.get("source"),
                f"workflows.{operation_text}.metadata.provenance.source"),
            license=_required(
                provenance.get("license"),
                f"workflows.{operation_text}.metadata.provenance.license"),
            capabilities=capabilities,
            limits=tuple(limits),
            required_nodes=_string_tuple(
                inventory.get("nodes"),
                f"workflows.{operation_text}.metadata.required_inventory.nodes"),
            required_models=_string_tuple(
                inventory.get("models"),
                f"workflows.{operation_text}.metadata.required_inventory.models"),
            required_loras=_string_tuple(
                inventory.get("loras"),
                f"workflows.{operation_text}.metadata.required_inventory.loras"),
            required_custom_nodes=_string_tuple(
                inventory.get("custom_nodes"),
                f"workflows.{operation_text}.metadata.required_inventory.custom_nodes"),
            model=str(metadata.get("model") or "").strip(),
            estimated_cost_usd=estimated_cost,
            digest=_digest(payload),
        )

    def capability_metadata(self) -> dict[str, object]:
        return {
            "preset_id": self.preset_id,
            "preset_revision": self.revision,
            "model": self.model,
            "tags": list(self.capabilities),
            "constraints": dict(self.limits),
            "estimated_cost_usd": self.estimated_cost_usd,
            "required_inventory": {
                "nodes": list(self.required_nodes),
                "models": list(self.required_models),
                "loras": list(self.required_loras),
                "custom_nodes": list(self.required_custom_nodes),
            },
            "workflow_digest": self.digest,
        }


@dataclass(frozen=True)
class ComfyProvisioningAsset:
    """One exact reviewed asset requested by a provisioning proposal."""

    kind: str
    name: str
    source: str
    license: str
    sha256: str = ""
    revision: str = ""

    def __post_init__(self):
        if self.kind not in _ASSET_KINDS:
            raise ValueError("provisioning asset kind is unsupported")
        _required(self.name, "asset name")
        parsed = urlparse(_required(self.source, "asset source"))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("asset source must be an absolute HTTPS URL")
        _required(self.license, "asset license")
        if self.kind in {"model", "lora"}:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
                raise ValueError("model and LoRA assets require a SHA-256 digest")
        elif not self.revision:
            raise ValueError("custom-node assets require a pinned revision")


@dataclass(frozen=True)
class ComfyProvisioningProposal:
    """Immutable proposal for assets missing from one workflow revision."""

    proposal_id: str
    created_at: str
    workflow_digest: str
    assets: tuple[ComfyProvisioningAsset, ...]
    digest: str

    def __post_init__(self):
        _required(self.proposal_id, "proposal_id")
        _timestamp(self.created_at, "created_at")
        if not re.fullmatch(r"[0-9a-f]{64}", self.workflow_digest):
            raise ValueError("workflow_digest must be a SHA-256 digest")
        if not self.assets:
            raise ValueError("provisioning assets are required")
        expected = _digest(self.digest_payload())
        if self.digest != expected:
            raise ValueError("provisioning proposal digest does not match its content")

    def digest_payload(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "workflow_digest": self.workflow_digest,
            "assets": [asset.__dict__ for asset in self.assets],
        }

    @classmethod
    def create(
        cls,
        workflow_revision: ComfyWorkflowRevision,
        *,
        assets: tuple[ComfyProvisioningAsset, ...],
    ) -> "ComfyProvisioningProposal":
        values = {
            "proposal_id": f"comfy_provisioning_{uuid.uuid4()}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workflow_digest": workflow_revision.digest,
            "assets": tuple(assets),
        }
        return cls(**values, digest=_digest({
            **values,
            "assets": [asset.__dict__ for asset in values["assets"]],
        }))
