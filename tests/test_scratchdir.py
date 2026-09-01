"""Unit tests for ScratchDir contracts and durable metadata."""

import json
import sqlite3

import pytest

import core.scratchdir_manager as scratchdir_manager_module
from core import paths
from core.handlers.scratchdir import ScratchDirHandler
from core.scratchdir_models import (
    MAX_TTL_HOURS,
    ScratchDirError,
    ScratchDirState,
    context_hint,
    require_scope,
    validate_quotas,
    validate_ttl,
)
from core.scratchdir_store import ScratchDirStore
from core.tool_registry import create_default_registry


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", tmp_path / "scratchdirs")
    ScratchDirStore._instance = None
    value = ScratchDirStore.instance()
    yield value
    ScratchDirStore._instance = None


def test_existing_delete_journal_store_migrates_to_wal(
        tmp_path, monkeypatch):
    metadata_dir = tmp_path / "scratchdirs"
    database = metadata_dir / "scratchdirs.sqlite3"
    metadata_dir.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        connection.execute("CREATE TABLE legacy_probe (value TEXT)")

    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", metadata_dir)
    ScratchDirStore._instance = None
    try:
        migrated = ScratchDirStore.instance()
        with migrated._connect() as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert connection.execute("PRAGMA cell_size_check").fetchone()[0] == 1
    finally:
        ScratchDirStore._instance = None


def test_scope_ttl_and_quota_validation():
    assert require_scope("u", "c", "a", "r") == ("u", "c", "a", "r")
    with pytest.raises(ScratchDirError) as exc:
        require_scope("u", "", "a", "r")
    assert exc.value.code == "scratchdir_context_missing"
    assert validate_ttl(None) == 168
    assert validate_ttl(MAX_TTL_HOURS) == MAX_TTL_HOURS
    with pytest.raises(ScratchDirError):
        validate_ttl(0)
    assert validate_quotas() == (1024 * 1024 * 1024, 10_000)
    with pytest.raises(ScratchDirError):
        validate_quotas(0, 1)


def test_activate_is_scoped_idempotent_and_public_shape_hides_locator(store):
    first = store.activate(
        "u", "c", "a", "r", locator="opaque-1", operation_id="op-1",
        now=100.0)
    same = store.activate(
        "u", "c", "a", "r", locator="opaque-other",
        operation_id="op-1", now=101.0)
    other_agent = store.activate(
        "u", "c", "b", "r", locator="opaque-2",
        operation_id="op-2", now=102.0)

    assert same == first
    assert other_agent.id != first.id
    assert first.state == ScratchDirState.ACTIVE.value
    assert first.epoch == first.revision == 1
    public = first.public_dict()
    assert public["format"] == "pawflow.scratchdir.v1"
    assert public["url"] == "fs://scratchdir/"
    assert public["mount_path"] == "/scratch"
    assert "locator" not in public
    assert "user_id" not in public
    assert "conversation_id" not in public
    assert "agent_name" not in public


def test_expiry_and_renewal_are_explicit(store):
    store.activate(
        "u", "c", "a", "r", locator="opaque", operation_id="op",
        ttl_hours=1, now=100.0)
    assert store.get("u", "c", "a", "r", now=3699.0).state == "active"
    assert store.get("u", "c", "a", "r", now=3700.0).state == "expired"
    with pytest.raises(ScratchDirError) as exc:
        store.renew("u", "c", "a", "r", now=3700.0)
    assert exc.value.code == "scratchdir_not_active"

    store.activate(
        "u", "c", "a", "r", locator="opaque-new",
        operation_id="op-new", ttl_hours=1, now=3800.0)
    renewed = store.renew(
        "u", "c", "a", "r", ttl_hours=2, now=3900.0)
    assert renewed.epoch == 2
    assert renewed.expires_at == 3900.0 + 7200
    assert renewed.revision == 4


def test_usage_clear_and_operation_fencing(store):
    store.activate(
        "u", "c", "a", "r", locator="opaque", operation_id="create",
        now=100.0)
    used = store.update_usage(
        "u", "c", "a", "r", observed_bytes=42, observed_files=3,
        reconciled_at=110.0)
    assert (used.observed_bytes, used.observed_files) == (42, 3)

    clearing = store.begin_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=120.0)
    assert clearing.state == "clearing"
    assert clearing.epoch == 2
    same = store.begin_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=121.0)
    assert same == clearing
    with pytest.raises(ScratchDirError) as exc:
        store.finish_clear(
            "u", "c", "a", "r", operation_id="wrong", now=122.0)
    assert exc.value.code == "scratchdir_state_conflict"
    cleared = store.finish_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=123.0)
    assert cleared.state == "cleared"
    assert cleared.locator == ""
    assert cleared.observed_bytes == cleared.observed_files == 0
    assert store.finish_clear(
        "u", "c", "a", "r", operation_id="clear-1", now=124.0) == cleared


def test_context_hint_is_metadata_only(store):
    assert store.context_hint("u", "c", "a", "r") == ""
    store.activate(
        "u", "c", "a", "r", locator="secret-physical-path",
        operation_id="op")
    hint = store.context_hint("u", "c", "a", "r")
    assert "ScratchDir: active" in hint
    assert "fs://scratchdir/" in hint
    assert "secret-physical-path" not in hint


def test_context_hint_requires_copied_virtual_environments():
    hint = context_hint()

    assert "python -m venv --copies" in hint
    assert "symlinks escape" in hint


def test_handler_schema_and_fail_closed_before_lifecycle_wiring():
    handler = ScratchDirHandler()
    assert handler.name == "scratchdir"
    assert handler.parameters_schema["required"] == ["action"]
    assert set(handler.parameters_schema["properties"]["action"]["enum"]) == {
        "status", "ensure", "renew", "clear"}
    assert "python -m venv --copies" in handler.description
    assert "scratchdir_context_missing" in handler.execute({"action": "status"})
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    assert "scratchdir_unavailable" in handler.execute({"action": "ensure"})


def test_handler_delegates_authenticated_scope_to_manager():
    calls = []

    class Manager:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {"format": "pawflow.scratchdir.v1", "status": "active"}

    handler = ScratchDirHandler()
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    handler.set_scratchdir_manager(Manager())
    payload = json.loads(handler.execute({"action": "ensure", "ttl_hours": 12}))
    assert payload["status"] == "active"
    assert calls == [{
        "action": "ensure",
        "user_id": "u",
        "conversation_id": "c",
        "agent_name": "a",
        "ttl_hours": 12,
    }]


def test_default_registry_exposes_scratchdir():
    assert create_default_registry().get("scratchdir") is not None


def test_corrupt_ephemeral_metadata_is_quarantined_and_recreated(
        tmp_path, monkeypatch, caplog):
    metadata_dir = tmp_path / "scratchdirs"
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", metadata_dir)
    ScratchDirStore._instance = None
    try:
        store = ScratchDirStore.instance()
        store.activate(
            "u", "c", "a", "r", locator="opaque", operation_id="op")
        database = metadata_dir / "scratchdirs.sqlite3"
        corrupted = bytearray(database.read_bytes())
        assert corrupted[:16] == b"SQLite format 3\x00"
        corrupted[44:48] = (0x7E6D322D).to_bytes(4, "big")
        database.write_bytes(corrupted)
        wal = database.with_name(database.name + "-wal")
        shm = database.with_name(database.name + "-shm")
        wal.write_bytes(b"preserved WAL evidence")
        shm.write_bytes(b"preserved SHM evidence")

        ScratchDirStore._instance = None
        recovered = ScratchDirStore.instance()

        assert recovered.get("u", "c", "a", "r") is None
        quarantined = list(metadata_dir.glob("scratchdirs.sqlite3.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == bytes(corrupted)
        quarantined_wal = list(
            metadata_dir.glob("scratchdirs.sqlite3-wal.corrupt-*"))
        quarantined_shm = list(
            metadata_dir.glob("scratchdirs.sqlite3-shm.corrupt-*"))
        assert [item.read_bytes() for item in quarantined_wal] == [
            b"preserved WAL evidence"]
        assert [item.read_bytes() for item in quarantined_shm] == [
            b"preserved SHM evidence"]
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert "quarantined corrupt ephemeral ScratchDir metadata" in caplog.text
    finally:
        ScratchDirStore._instance = None


def test_handler_opens_scratchdir_store_only_when_executed(monkeypatch):
    attempts = []

    class BrokenManager:
        def __init__(self, service):
            attempts.append(service)
            raise sqlite3.DatabaseError("unsupported file format")

    monkeypatch.setattr(
        scratchdir_manager_module, "ScratchDirManager", BrokenManager)
    service = object()
    handler = ScratchDirHandler()

    handler.set_fs_service(service)

    assert attempts == []
    assert handler.parameters_schema["required"] == ["action"]
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    result = handler.execute({"action": "status"})
    assert attempts == [service]
    assert "scratchdir_unavailable" in result
    assert "unsupported file format" in result
