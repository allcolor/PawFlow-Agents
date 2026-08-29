"""Immutable, reviewed template catalog for Website Creator."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit


TEMPLATE_CATALOG_VERSION = "1"
MAX_TEMPLATE_ARCHIVE_BYTES = 50 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_REQUIRED_FIELDS = frozenset({
    "provider", "name", "version", "package_url", "sha256",
    "license", "attribution", "artifact_root",
})

TEMPLATE_CATALOG = (
    {
        "provider": "startbootstrap",
        "name": "creative",
        "version": "7.0.7",
        "package_url": (
            "https://codeload.github.com/StartBootstrap/"
            "startbootstrap-creative/zip/"
            "b1762d8c690a2379c078c776dc0830bdd81c6f55"
        ),
        "sha256": "085435879015b0afe2b2adb4bdcd1226aa9cd4ac6e72e979c48e35e190875eef",
        "license": "MIT",
        "attribution": "Creative 7.0.7 by Start Bootstrap",
        "artifact_root": (
            "startbootstrap-creative-"
            "b1762d8c690a2379c078c776dc0830bdd81c6f55/dist"
        ),
    },
    {
        "provider": "html5up",
        "name": "identity",
        "version": "be7721e3",
        "package_url": (
            "https://codeload.github.com/html5up/identity/zip/"
            "be7721e3a3c17ba44da0f63df57617fdaf7ee491"
        ),
        "sha256": "1462b4240fbddfea1bf97cd37fb305f0ddd5eeb00d19d74a0d12f8ae537a9301",
        "license": "CC BY 3.0",
        "attribution": "Identity by HTML5 UP",
        "artifact_root": "identity-be7721e3a3c17ba44da0f63df57617fdaf7ee491",
    },
)


def _immutable_package_url(value: str) -> bool:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.hostname == "codeload.github.com":
        return len(segments) == 4 and _COMMIT_RE.fullmatch(segments[-1]) is not None
    if parsed.hostname == "github.com" and "/releases/download/" in parsed.path:
        return len(segments) >= 6 and segments[-2] not in {"latest", "main", "master"}
    return False


def validate_template_catalog(entries: Iterable[Mapping[str, object]]) -> None:
    """Reject incomplete, mutable, ambiguous, or malformed catalog entries."""

    refs: set[str] = set()
    count = 0
    for raw in entries:
        count += 1
        missing = _REQUIRED_FIELDS - set(raw)
        if missing:
            raise ValueError(f"template catalog entry missing fields: {sorted(missing)}")
        entry = {field: str(raw[field]).strip() for field in _REQUIRED_FIELDS}
        if not all(entry.values()):
            raise ValueError("template catalog fields must not be empty")
        if entry["provider"] not in {"startbootstrap", "html5up"}:
            raise ValueError(f"template provider is preview-only: {entry['provider']}")
        if not _immutable_package_url(entry["package_url"]):
            raise ValueError("template package_url must be immutable HTTPS")
        if _SHA256_RE.fullmatch(entry["sha256"]) is None:
            raise ValueError("template sha256 must contain 64 lowercase hex characters")
        root_parts = entry["artifact_root"].replace("\\", "/").split("/")
        if (
            entry["artifact_root"].startswith(("/", "\\"))
            or not all(root_parts)
            or any(part in {".", ".."} for part in root_parts)
        ):
            raise ValueError("template artifact_root must be a safe relative path")
        ref = ":".join((entry["provider"], entry["name"], entry["version"]))
        if ref in refs:
            raise ValueError(f"duplicate template catalog entry: {ref}")
        refs.add(ref)
    if count == 0:
        raise ValueError("template catalog must not be empty")


def resolve_template(reference: str) -> dict[str, str]:
    """Resolve one exact provider:name:version reference from the shipped catalog."""

    validate_template_catalog(TEMPLATE_CATALOG)
    requested = str(reference or "").strip().casefold()
    for raw in TEMPLATE_CATALOG:
        entry = {key: str(value) for key, value in raw.items()}
        ref = ":".join((entry["provider"], entry["name"], entry["version"]))
        if requested in {ref.casefold(), entry["package_url"].casefold()}:
            return entry
    provider = requested.split(":", 1)[0]
    if provider not in {"startbootstrap", "html5up"}:
        raise ValueError(f"template provider is preview-only: {provider or 'unknown'}")
    raise ValueError(f"template is not present in the immutable catalog: {reference}")


def template_cache_identity(
    entry: Mapping[str, object], *, catalog_version: str = TEMPLATE_CATALOG_VERSION,
) -> dict[str, object]:
    """Return the replay identity owned by catalog version and immutable bytes."""

    inputs = [
        str(catalog_version),
        str(entry["provider"]),
        str(entry["name"]),
        str(entry["version"]),
        str(entry["package_url"]),
        str(entry["sha256"]),
        str(entry["artifact_root"]),
    ]
    payload = json.dumps(inputs, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return {"inputs": inputs, "digest": hashlib.sha256(payload).hexdigest()}


validate_template_catalog(TEMPLATE_CATALOG)
