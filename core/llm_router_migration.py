"""One-shot persisted-definition migration from llmFailover to llmRouter."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import core.paths as _paths


MIGRATION_VERSION = "llm-router-v1"
_SENSITIVE_PARTS = (
    "authorization", "api_key", "apikey", "access_token", "refresh_token",
    "password", "secret", "cookie")


class LLMRouterMigrationError(RuntimeError):
    """An old global definition cannot be transformed safely."""


def _migration_dir() -> Path:
    return _paths.SYSTEM_DIR / "migrations" / MIGRATION_VERSION


def _safe_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_"
                   for ch in str(value or "")) or "empty"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _backup_path(scope: str, scope_id: str, service_id: str) -> Path:
    return (_migration_dir() / "backups" / _safe_part(scope)
            / _safe_part(scope_id) / f"{_safe_part(service_id)}.json")


def _record(scope: str, scope_id: str, service_id: str, outcome: str) -> None:
    path = _migration_dir() / "report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        report = {}
    entries = report.setdefault("entries", {})
    key = f"{scope}:{scope_id}:{service_id}"
    entries[key] = {"scope": scope, "outcome": outcome}
    report.update({
        "version": MIGRATION_VERSION,
        "updated_at": time.time(),
        "transformed": sum(1 for item in entries.values()
                           if item.get("outcome") == "transformed"),
        "quarantined": sum(1 for item in entries.values()
                           if item.get("outcome") == "quarantined"),
    })
    _atomic_json(path, report)


def _new_config(config: Any) -> dict:
    if not isinstance(config, dict):
        raise ValueError("config_not_object")
    main = str(config.get("main_llm_service", "") or "").strip()
    fallbacks = config.get("fallback_llm_services", [])
    if isinstance(fallbacks, str):
        fallbacks = json.loads(fallbacks)
    if not main or not isinstance(fallbacks, list):
        raise ValueError("missing_candidates")
    ids = [main]
    for item in fallbacks:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("invalid_candidate")
        ids.append(item.strip())
    if len(ids) < 2 or len(ids) != len(set(ids)):
        raise ValueError("invalid_candidate_set")
    return {
        "candidates": [
            {"service_id": service_id, "priority": (index + 1) * 10,
             "weight": 1.0, "enabled": True}
            for index, service_id in enumerate(ids)
        ],
        "strategy": "ordered",
    }


def _backup_payload(payload: dict) -> dict:
    """Copy the legacy payload with sensitive-named config keys removed."""
    backup = dict(payload)
    config = payload.get("config")
    if isinstance(config, dict):
        backup["config"] = {
            key: value for key, value in config.items()
            if not any(part in str(key).lower().replace("-", "_")
                       for part in _SENSITIVE_PARTS)}
    return backup


def migrate_definition_payload(payload: dict, *, scope: str, scope_id: str,
                               service_id: str) -> tuple[dict, str]:
    """Return migrated payload and outcome; non-legacy input is unchanged."""
    if payload.get("service_type") != "llmFailover":
        return payload, "unchanged"
    backup = _backup_path(scope, scope_id, service_id)
    try:
        if not backup.exists():
            _atomic_json(backup, _backup_payload(payload))
    except Exception as exc:
        raise LLMRouterMigrationError(
            f"Cannot protect legacy service '{service_id}' before migration") from exc
    try:
        config = _new_config(payload.get("config", {}))
        migrated = dict(payload)
        migrated["service_type"] = "llmRouter"
        migrated["config"] = config
        outcome = "transformed"
    except Exception as exc:
        if scope == "global":
            raise LLMRouterMigrationError(
                f"Global service '{service_id}' cannot migrate to llmRouter: "
                f"{type(exc).__name__}") from exc
        migrated = dict(payload)
        migrated["service_type"] = "llmRouter"
        migrated["enabled"] = False
        migrated["config"] = {
            "candidates": [], "strategy": "ordered",
            "migration_quarantine": {
                "code": "invalid_legacy_llm_failover",
                "id": str(uuid.uuid4()), "timestamp": time.time(),
                "message": "Review and save at least two enabled LLM candidates.",
            },
        }
        outcome = "quarantined"
    _record(scope, scope_id, service_id, outcome)
    return migrated, outcome
