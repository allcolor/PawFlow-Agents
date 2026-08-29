"""Durable policy and bounded manifest ledger for Website Creator assets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import posixpath
import re
from typing import Any, Mapping, Sequence

from core.website_creator_contracts import canonical_origin, canonicalize_url


ASSET_MANIFEST_SCHEMA_VERSION = 1
ASSET_MANIFEST_BATCH_SIZE = 25
ASSET_KIND_LIMITS = {
    "image": 12 * 1024 * 1024,
    "stylesheet": 5 * 1024 * 1024,
    "script": 5 * 1024 * 1024,
    "font": 5 * 1024 * 1024,
    "media": 64 * 1024 * 1024,
    "manifest": 2 * 1024 * 1024,
}
_TRACKING_RE = re.compile(
    r"(?:^|[./_-])(?:analytics?|tracking|tracker|pixel|ads?|doubleclick|"
    r"gtag|googletagmanager|segment|mixpanel)(?:[./_?&=-]|$)",
    re.IGNORECASE,
)


def _stable_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def normalize_asset_kind(value: Any) -> str:
    kind = str(value or "").strip().casefold()
    if not kind:
        raise ValueError("source asset kind is required")
    if kind not in ASSET_KIND_LIMITS:
        raise ValueError(
            "source asset kind must be image, stylesheet, script, font, media, "
            "or manifest"
        )
    return kind


def asset_decision_key(url: str, path: str, kind: str) -> str:
    payload = _stable_json({
        "url": canonicalize_url(url),
        "path": posixpath.normpath(path),
        "kind": normalize_asset_kind(kind),
    })
    return hashlib.sha256(payload).hexdigest()


def validate_asset_policy(
    *,
    source_url: str,
    rights: Mapping[str, Any],
    approved_third_party_origins: Sequence[str],
    url: str,
    kind: str,
    third_party_approved: bool = False,
    immutable_url: str = "",
    license_name: str = "",
    provenance: str = "",
    source_application_bundle: bool = True,
) -> dict[str, Any]:
    """Validate one source subresource against immutable run authorization."""

    normalized_kind = normalize_asset_kind(kind)
    canonical_url = canonicalize_url(url)
    source_origin = canonical_origin(source_url)
    origin = canonical_origin(canonical_url)
    basis = str((rights or {}).get("basis") or "").strip().casefold()
    allowed = {
        str(item or "").strip().casefold()
        for item in ((rights or {}).get("allowed_asset_kinds") or [])
    }
    if basis not in {"owner", "permission"} or normalized_kind not in allowed:
        raise ValueError(
            f"source rights do not authorize {normalized_kind} asset reuse"
        )
    if _TRACKING_RE.search(canonical_url):
        raise ValueError("tracking, analytics and advertising assets are prohibited")
    if normalized_kind == "script" and source_application_bundle:
        raise ValueError(
            "source application bundle copying is prohibited by default"
        )

    is_third_party = origin != source_origin
    if is_third_party:
        approved_origins = {
            canonical_origin(item) for item in approved_third_party_origins
        }
        if origin not in approved_origins:
            raise ValueError("third-party asset origin is not approved")
        if not third_party_approved:
            raise ValueError("third-party asset requires explicit user approval")
        if not str(license_name or "").strip() or not str(provenance or "").strip():
            raise ValueError(
                "third-party asset requires license and provenance"
            )
        if not str(immutable_url or "").strip():
            raise ValueError("third-party asset requires an immutable URL")
        immutable_url = canonicalize_url(immutable_url)
        if canonical_origin(immutable_url) != origin:
            raise ValueError(
                "third-party immutable URL must use the approved asset origin"
            )

    return {
        "canonical_url": canonical_url,
        "source_origin": source_origin,
        "origin": origin,
        "third_party": is_third_party,
        "third_party_approved": bool(is_third_party and third_party_approved),
        "immutable_url": str(immutable_url or ""),
        "license": str(license_name or "").strip(),
        "provenance": str(provenance or "").strip(),
    }


@dataclass(frozen=True)
class AssetReservation:
    key: str
    generation: int
    max_bytes: int
    decision_path: str
    batch_path: str
    pending: dict[str, Any]


class AssetManifestLedger:
    """Store one replay record per asset and bounded 25-entry manifest batches."""

    def __init__(
        self,
        service,
        workspace: str,
        *,
        total_budget_bytes: int,
    ):
        if (
            isinstance(total_budget_bytes, bool)
            or not isinstance(total_budget_bytes, int)
            or total_budget_bytes <= 0
        ):
            raise ValueError("total asset-byte budget must be a positive integer")
        self.service = service
        self.workspace = posixpath.normpath(str(workspace or ""))
        self.total_budget_bytes = total_budget_bytes
        self.root = posixpath.join(self.workspace, "assets", "manifest")
        self.checkpoint_path = posixpath.join(self.root, "checkpoint.json")

    def _exists(self, path: str) -> bool:
        return bool(self.service.exists(path, local=False))

    def _read_json(self, path: str) -> dict[str, Any]:
        try:
            value = json.loads(self.service.read_file(path, local=False))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Website Creator asset ledger: {path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"invalid Website Creator asset ledger: {path}")
        return value

    def _write_json(self, path: str, value: Mapping[str, Any]) -> None:
        self.service.atomic_write_file(path, _stable_json(value), local=False)

    def _checkpoint(self) -> dict[str, Any]:
        if not self._exists(self.checkpoint_path):
            return {
                "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
                "generation": 0,
                "count": 0,
                "bytes": 0,
                "total_budget_bytes": self.total_budget_bytes,
            }
        value = self._read_json(self.checkpoint_path)
        if value.get("schema_version") != ASSET_MANIFEST_SCHEMA_VERSION:
            raise ValueError("asset manifest schema version mismatch")
        if int(value.get("total_budget_bytes") or 0) != self.total_budget_bytes:
            raise ValueError("confirmed total asset-byte budget changed")
        return value

    def _decision_path(self, key: str) -> str:
        return posixpath.join(self.root, "decisions", f"{key}.json")

    def _batch_path(self, generation: int) -> str:
        index = ((generation - 1) // ASSET_MANIFEST_BATCH_SIZE) + 1
        return posixpath.join(self.root, f"batch-{index:04d}.json")

    def _verify_file(self, decision: Mapping[str, Any]) -> None:
        path = str(decision.get("path") or "")
        if not path or not self._exists(path):
            raise ValueError("replayed source asset file is missing")
        expected_bytes = int(decision.get("bytes") or 0)
        stat = self.service.stat(path, local=False)
        if int(getattr(stat, "size", -1)) != expected_bytes:
            raise ValueError("replayed source asset size mismatch")
        hashed = self.service.hash_file(path, local=False)
        if (
            int(hashed.get("bytes") or 0) != expected_bytes
            or str(hashed.get("sha256") or "") != str(decision.get("sha256") or "")
        ):
            raise ValueError("replayed source asset hash mismatch")

    def _account_completed(
        self,
        decision: Mapping[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        generation = int(decision["generation"])
        current = int(checkpoint.get("generation") or 0)
        if current >= generation:
            return checkpoint
        if current != generation - 1:
            raise ValueError("asset ledger generation is not contiguous")
        batch_path = self._batch_path(generation)
        if self._exists(batch_path):
            batch = self._read_json(batch_path)
        else:
            batch = {
                "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
                "batch": ((generation - 1) // ASSET_MANIFEST_BATCH_SIZE) + 1,
                "entries": [],
            }
        entries = list(batch.get("entries") or [])
        if not any(str(item.get("key") or "") == decision["key"] for item in entries):
            if len(entries) >= ASSET_MANIFEST_BATCH_SIZE:
                raise ValueError("asset manifest batch exceeds 25 entries")
            entries.append(dict(decision))
            batch["entries"] = entries
            self._write_json(batch_path, batch)
        updated = {
            **checkpoint,
            "generation": generation,
            "count": int(checkpoint.get("count") or 0) + 1,
            "bytes": int(checkpoint.get("bytes") or 0) + int(decision["bytes"]),
        }
        if updated["bytes"] > self.total_budget_bytes:
            raise ValueError("total asset-byte budget exceeded")
        self._write_json(self.checkpoint_path, updated)
        return updated

    def begin(
        self,
        *,
        url: str,
        path: str,
        kind: str,
        policy: Mapping[str, Any],
    ) -> tuple[AssetReservation | None, dict[str, Any] | None]:
        normalized_kind = normalize_asset_kind(kind)
        key = asset_decision_key(url, path, normalized_kind)
        decision_path = self._decision_path(key)
        checkpoint = self._checkpoint()
        if self._exists(decision_path):
            decision = self._read_json(decision_path)
            status = str(decision.get("status") or "")
            if status == "complete":
                self._verify_file(decision)
                self._account_completed(decision, checkpoint)
                return None, decision
            if status != "pending":
                raise ValueError("asset replay record has an invalid status")
            generation = int(decision.get("generation") or 0)
            if generation != int(checkpoint.get("generation") or 0) + 1:
                raise ValueError("pending asset replay generation is invalid")
            pending = decision
        else:
            generation = int(checkpoint.get("generation") or 0) + 1
            pending = {
                "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
                "status": "pending",
                "key": key,
                "generation": generation,
                "url": canonicalize_url(url),
                "path": posixpath.normpath(path),
                "kind": normalized_kind,
                "policy": dict(policy),
            }
            self._write_json(decision_path, pending)

        remaining = self.total_budget_bytes - int(checkpoint.get("bytes") or 0)
        max_bytes = min(ASSET_KIND_LIMITS[normalized_kind], remaining)
        if max_bytes <= 0:
            raise ValueError("total asset-byte budget is exhausted")
        return AssetReservation(
            key=key,
            generation=int(pending["generation"]),
            max_bytes=max_bytes,
            decision_path=decision_path,
            batch_path=self._batch_path(int(pending["generation"])),
            pending=pending,
        ), None

    def complete(
        self,
        reservation: AssetReservation,
        result: Mapping[str, Any],
        *,
        final_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        size = int(result.get("bytes") or 0)
        if size <= 0:
            raise ValueError("source asset response is empty")
        if size > reservation.max_bytes:
            raise ValueError("source asset exceeds its effective byte limit")
        decision = {
            **reservation.pending,
            "status": "complete",
            "url": str(result.get("url") or reservation.pending["url"]),
            "bytes": size,
            "sha256": str(result.get("sha256") or ""),
            "content_type": str(result.get("content_type") or ""),
            "policy": dict(final_policy),
        }
        if len(decision["sha256"]) != 64:
            raise ValueError("source asset response is missing a SHA-256 digest")
        self._write_json(reservation.decision_path, decision)
        self._account_completed(decision, self._checkpoint())
        return decision


__all__ = [
    "ASSET_KIND_LIMITS",
    "ASSET_MANIFEST_BATCH_SIZE",
    "ASSET_MANIFEST_SCHEMA_VERSION",
    "AssetManifestLedger",
    "AssetReservation",
    "asset_decision_key",
    "normalize_asset_kind",
    "validate_asset_policy",
]
