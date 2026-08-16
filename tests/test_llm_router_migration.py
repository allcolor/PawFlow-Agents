import json

import pytest

import core.paths as paths
from core.llm_router_migration import (
    LLMRouterMigrationError,
    migrate_definition_payload,
)


def legacy(config=None):
    return {
        "service_id": "resilient",
        "service_type": "llmFailover",
        "scope": "user",
        "scope_id": "alice",
        "enabled": True,
        "description": "keep me",
        "created_at": 100,
        "config": config if config is not None else {
            "main_llm_service": "main",
            "fallback_llm_services": ["backup-1", "backup-2"],
        },
    }


def test_valid_definition_transforms_and_preserves_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    migrated, outcome = migrate_definition_payload(
        legacy(), scope="user", scope_id="alice", service_id="resilient")
    assert outcome == "transformed"
    assert migrated["service_type"] == "llmRouter"
    assert migrated["description"] == "keep me"
    assert migrated["created_at"] == 100
    assert migrated["config"]["strategy"] == "ordered"
    assert [item["service_id"] for item in migrated["config"]["candidates"]] == [
        "main", "backup-1", "backup-2"]
    report = json.loads((tmp_path / "migrations" / "llm-router-v1"
                         / "report.json").read_text())
    assert report["transformed"] == 1


def test_invalid_global_is_fail_fast_after_protected_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    with pytest.raises(LLMRouterMigrationError):
        migrate_definition_payload(
            legacy({"main_llm_service": ""}), scope="global",
            scope_id="__global__", service_id="resilient")
    backup = (tmp_path / "migrations" / "llm-router-v1" / "backups"
              / "global" / "__global__" / "resilient.json")
    assert json.loads(backup.read_text())["service_type"] == "llmFailover"


def test_invalid_user_is_quarantined_without_raw_config(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    original = legacy({"main_llm_service": "main", "api_key": "secret"})
    migrated, outcome = migrate_definition_payload(
        original, scope="user", scope_id="alice", service_id="resilient")
    assert outcome == "quarantined"
    assert migrated["service_type"] == "llmRouter"
    assert migrated["enabled"] is False
    assert "secret" not in json.dumps(migrated)
    assert migrated["config"]["migration_quarantine"]["code"] == (
        "invalid_legacy_llm_failover")


def test_backup_excludes_sensitive_named_config_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    migrate_definition_payload(
        legacy({"main_llm_service": "main",
                "fallback_llm_services": ["backup-1"],
                "api_key": "sk-leak", "Refresh-Token": "rt-leak"}),
        scope="user", scope_id="alice", service_id="resilient")
    backup = json.loads((tmp_path / "migrations" / "llm-router-v1" / "backups"
                         / "user" / "alice" / "resilient.json").read_text())
    assert backup["config"] == {
        "main_llm_service": "main", "fallback_llm_services": ["backup-1"]}
    assert "leak" not in json.dumps(backup)


def test_nonlegacy_payload_is_idempotently_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    payload = {"service_type": "llmRouter", "config": {"candidates": []}}
    assert migrate_definition_payload(
        payload, scope="conv", scope_id="c1", service_id="r") == (
            payload, "unchanged")
