"""Atomic backup, activation marker, and rollback for PlanStore migration."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.plan_migration import legacy_plan_digest


def _required_text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(value: Any) -> Path:
    text = _required_text(value, "source_path")
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source_path must be a safe relative path")
    return relative


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(
        path,
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2,
        ).encode("utf-8"),
    )


class PlanMigrationManifestStore:
    """Persist preflight backups and fence rollback after the first new write."""

    def __init__(
        self,
        root: Path,
        *,
        activation_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        if activation_enabled is None:
            from core.flow_feature_flags import plan_migration_enabled

            activation_enabled = plan_migration_enabled
        if not callable(activation_enabled):
            raise TypeError("activation_enabled must be callable")
        self.activation_enabled = activation_enabled

    def _directory(self, migration_id: str) -> Path:
        migration_id = _required_text(migration_id, "migration_id")
        if (
            not migration_id.startswith("pm_")
            or len(migration_id) != 27
            or any(value not in "0123456789abcdef" for value in migration_id[3:])
        ):
            raise ValueError("invalid migration_id")
        return self.root / migration_id

    def _load(self, migration_id: str) -> dict[str, Any]:
        path = self._directory(migration_id) / "manifest.json"
        if not path.exists():
            raise KeyError(migration_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("migration_id") != migration_id:
            raise ValueError("invalid plan migration manifest")
        return value

    def _save(self, manifest: dict[str, Any]) -> None:
        _atomic_json(
            self._directory(str(manifest["migration_id"])) / "manifest.json",
            manifest,
        )

    @staticmethod
    def _copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    def get(self, migration_id: str) -> dict[str, Any]:
        """Return an isolated manifest snapshot."""

        with self._lock:
            return self._copy(self._load(migration_id))

    @staticmethod
    def _verify_record_source(
        source_root: Path,
        record: dict[str, Any],
    ) -> tuple[Path, bytes]:
        relative = _safe_relative(record.get("source_path"))
        source = source_root / relative
        try:
            payload = source.read_bytes()
            decoded = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"legacy plan changed after preflight: {relative.as_posix()}"
            ) from exc
        if legacy_plan_digest(decoded) != record.get("source_digest"):
            raise ValueError(
                f"legacy plan changed after preflight: {relative.as_posix()}")
        return relative, payload

    def prepare(self, report: dict[str, Any]) -> dict[str, Any]:
        """Back up exact source bytes and persist an immutable preflight report."""

        if not isinstance(report, dict) or report.get("schema_version") != 1:
            raise ValueError("preflight report schema_version must be 1")
        source_root = Path(_required_text(report.get("source_root"), "source_root"))
        report_digest = _canonical_digest(report)
        migration_id = f"pm_{report_digest[:24]}"
        with self._lock:
            manifest_path = self._directory(migration_id) / "manifest.json"
            if manifest_path.exists():
                current = self._load(migration_id)
                if current.get("report_digest") != report_digest:
                    raise ValueError("migration manifest digest collision")
                result = self._copy(current)
                result["idempotent"] = True
                return result

            backups = []
            for record in report.get("records") or []:
                if not isinstance(record, dict):
                    raise TypeError("preflight records must be objects")
                relative, payload = self._verify_record_source(
                    source_root, record)
                backup = self._directory(migration_id) / "backups" / relative
                _atomic_bytes(backup, payload)
                backups.append({
                    "source_path": relative.as_posix(),
                    "source_digest": str(record.get("source_digest") or ""),
                    "byte_digest": hashlib.sha256(payload).hexdigest(),
                })
            manifest = {
                "schema_version": 1,
                "migration_id": migration_id,
                "state": "prepared",
                "report_digest": report_digest,
                "report": self._copy(report),
                "source_root": source_root.as_posix(),
                "backups": backups,
                "artifacts": [],
                "activated_at": None,
                "first_write_at": None,
                "rolled_back_at": None,
            }
            self._save(manifest)
            result = self._copy(manifest)
            result["idempotent"] = False
            return result

    def validate_activation(self, migration_id: str) -> dict[str, Any]:
        """Recheck the activation fence without changing manifest state."""

        if not self.activation_enabled():
            raise ValueError("plan migration is disabled by the server")
        with self._lock:
            manifest = self._load(migration_id)
            if manifest["state"] == "active":
                return self._copy(manifest)
            if manifest["state"] != "prepared":
                raise ValueError("only a prepared migration can be activated")
            report = manifest["report"]
            if report.get("blockers") or not report.get("activation_allowed"):
                raise ValueError("plan migration preflight has blockers")
            source_root = Path(manifest["source_root"])
            for record in report.get("records") or []:
                self._verify_record_source(source_root, record)
            return self._copy(manifest)

    def activate(
        self,
        migration_id: str,
        *,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record activation only after rechecking blockers and every source."""

        if not isinstance(artifacts, list) or any(
            not isinstance(value, dict) for value in artifacts
        ):
            raise TypeError("artifacts must be an array of objects")
        if not self.activation_enabled():
            raise ValueError("plan migration is disabled by the server")
        with self._lock:
            manifest = self._load(migration_id)
            if manifest["state"] == "active":
                result = self._copy(manifest)
                result["idempotent"] = True
                return result
            if manifest["state"] != "prepared":
                raise ValueError("only a prepared migration can be activated")
            report = manifest["report"]
            if report.get("blockers") or not report.get("activation_allowed"):
                raise ValueError("plan migration preflight has blockers")
            source_root = Path(manifest["source_root"])
            for record in report.get("records") or []:
                self._verify_record_source(source_root, record)
            manifest["state"] = "active"
            manifest["artifacts"] = self._copy(artifacts)
            manifest["activated_at"] = time.time()
            self._save(manifest)
            result = self._copy(manifest)
            result["idempotent"] = False
            return result

    def mark_first_write(self, migration_id: str) -> dict[str, Any]:
        """Permanently fence rollback after the first new-path mutation."""

        with self._lock:
            manifest = self._load(migration_id)
            if manifest["state"] != "active":
                raise ValueError("first write requires an active migration")
            if manifest.get("first_write_at") is None:
                manifest["first_write_at"] = time.time()
                self._save(manifest)
            return self._copy(manifest)

    def mark_active_first_write(self) -> list[dict[str, Any]]:
        """Fence every active migration before a canonical live mutation."""

        with self._lock:
            if not self.root.exists():
                return []
            active = []
            for path in sorted(self.root.glob("pm_*/manifest.json")):
                manifest = self._load(path.parent.name)
                if manifest["state"] != "active":
                    continue
                if manifest.get("first_write_at") is None:
                    manifest["first_write_at"] = time.time()
                    self._save(manifest)
                active.append(self._copy(manifest))
            return active

    def rollback(
        self,
        migration_id: str,
        *,
        remove_artifact: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Remove created artifacts and restore exact legacy bytes before writes."""

        with self._lock:
            manifest = self._load(migration_id)
            if manifest["state"] == "rolled_back":
                return self._copy(manifest)
            if manifest["state"] != "active":
                raise ValueError("only an active migration can be rolled back")
            if manifest.get("first_write_at") is not None:
                raise ValueError(
                    "rollback is unavailable after the first post-activation write")
            artifacts = manifest.get("artifacts") or []
            if artifacts and remove_artifact is None:
                raise ValueError("remove_artifact is required for rollback")
            for artifact in reversed(artifacts):
                remove_artifact(self._copy(artifact))
            source_root = Path(manifest["source_root"])
            directory = self._directory(migration_id)
            for backup in manifest.get("backups") or []:
                relative = _safe_relative(backup.get("source_path"))
                payload = (directory / "backups" / relative).read_bytes()
                if hashlib.sha256(payload).hexdigest() != backup.get("byte_digest"):
                    raise ValueError("plan migration backup digest mismatch")
                _atomic_bytes(source_root / relative, payload)
            manifest["state"] = "rolled_back"
            manifest["rolled_back_at"] = time.time()
            self._save(manifest)
            return self._copy(manifest)



__all__ = ["PlanMigrationManifestStore"]
