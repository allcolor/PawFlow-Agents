"""Typed secret entries shared by file-backed and conversation secret stores."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

EXTERNAL_SECRET_TYPE = "external_secret"
EXTERNAL_SECRET_VERSION = 1
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SecretEntryError(ValueError):
    """A persisted secret entry is malformed or unsupported."""


@dataclass(frozen=True)
class ExternalSecretRef:
    """Reference to a value owned by a configured secretProvider service."""

    provider_service: str
    locator: Mapping[str, Any] = field(repr=False)
    version: int = EXTERNAL_SECRET_VERSION

    def __post_init__(self) -> None:
        service_id = str(self.provider_service or "").strip()
        if not _PROVIDER_ID_RE.fullmatch(service_id):
            raise SecretEntryError("external secret provider_service is invalid")
        if self.version != EXTERNAL_SECRET_VERSION:
            raise SecretEntryError(
                f"unsupported external secret entry version: {self.version}")
        if not isinstance(self.locator, Mapping) or not self.locator:
            raise SecretEntryError("external secret locator must be a non-empty object")


def is_external_secret_raw(value: Any) -> bool:
    return isinstance(value, dict) and value.get("$type") == EXTERNAL_SECRET_TYPE


def encode_external_secret_ref(
        provider_service: str, locator: Mapping[str, Any]) -> dict[str, Any]:
    """Build a persisted reference; the locator is always encrypted."""
    ref = ExternalSecretRef(
        provider_service=str(provider_service or "").strip(),
        locator=dict(locator or {}),
    )
    try:
        canonical = json.dumps(
            dict(ref.locator), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SecretEntryError("external secret locator must be JSON-serializable") from exc

    from core.secrets import get_secrets_manager
    encrypted = get_secrets_manager().encrypt(canonical)
    return {
        "$type": EXTERNAL_SECRET_TYPE,
        "version": EXTERNAL_SECRET_VERSION,
        "provider_service": ref.provider_service,
        "locator": encrypted,
    }


def decode_external_secret_ref(value: Any) -> ExternalSecretRef:
    """Decode a persisted external reference without ever accepting plaintext."""
    if not is_external_secret_raw(value):
        raise SecretEntryError("secret entry is not an external reference")

    version = value.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise SecretEntryError("external secret version must be an integer")
    encrypted = value.get("locator")
    if not isinstance(encrypted, str) or not encrypted.startswith("enc:"):
        raise SecretEntryError("external secret locator must be encrypted")

    from core.secrets import get_secrets_manager
    try:
        decoded = get_secrets_manager().decrypt(encrypted)
        locator = json.loads(decoded)
    except Exception as exc:
        raise SecretEntryError("external secret locator cannot be decrypted") from exc
    if not isinstance(locator, dict):
        raise SecretEntryError("external secret locator must decode to an object")

    return ExternalSecretRef(
        provider_service=str(value.get("provider_service") or "").strip(),
        locator=locator,
        version=version,
    )


def external_secret_metadata(value: Any) -> dict[str, Any] | None:
    """Return safe display metadata without decrypting or exposing the locator."""
    if not is_external_secret_raw(value):
        return None
    version = value.get("version")
    provider = value.get("provider_service")
    return {
        "type": EXTERNAL_SECRET_TYPE,
        "version": version if isinstance(version, int) else None,
        "provider_service": str(provider or ""),
        "valid_envelope": (
            isinstance(version, int)
            and version == EXTERNAL_SECRET_VERSION
            and bool(_PROVIDER_ID_RE.fullmatch(str(provider or "")))
            and isinstance(value.get("locator"), str)
            and value["locator"].startswith("enc:")
        ),
    }
