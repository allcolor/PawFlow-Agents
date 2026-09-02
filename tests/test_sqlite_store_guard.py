"""Fail-closed behaviour of durable SQLite stores on a damaged main file."""
import logging
import sqlite3

import pytest

from core import FlowFile
from core.agent_inbox_store import AgentInboxStore
from core.sqlite_store_guard import (
    SqliteStoreGuard,
    SqliteStoreUnavailableError,
    is_corruption_error,
    preflight_main_file,
)
from core.ui_surface import make_ui_surface
from core.ui_surface_store import UiSurfaceStore
from tasks.ai.actions.ui_surfaces import _handle_ui_surfaces

# The production signature: one 24-byte TLS alert record written at byte 39
# of page 1 (header 17 03 03 00 13 followed by 19 encrypted bytes).
TLS_ALERT = bytes.fromhex("1703030013") + bytes(range(1, 20))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    UiSurfaceStore.reset()
    yield
    UiSurfaceStore.reset()


def _corrupt_page_one(path):
    with open(path, "r+b") as handle:
        handle.seek(39)
        handle.write(TLS_ALERT)


def _snapshot(path):
    return {
        child.name: child.read_bytes() for child in path.parent.iterdir()
    }


def _surface():
    return make_ui_surface(
        user_id="alice", conversation_id="conv", producer_kind="task",
        producer_id="custom-review", surface_id="uis_one", revision=1,
        semantic={"role": "review", "title": "Review", "fields": [],
                  "actions": []})


def test_preflight_accepts_missing_and_healthy_files(tmp_path):
    database = tmp_path / "missing.sqlite3"
    preflight_main_file(database, "Test")
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t(x)")
    connection.close()
    preflight_main_file(database, "Test")
    assert not (tmp_path / "missing.sqlite3-wal").exists()


def test_preflight_rejects_empty_and_corrupt_files(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with pytest.raises(sqlite3.DatabaseError, match="file is empty"):
        preflight_main_file(empty, "Test")
    corrupt = tmp_path / "corrupt.sqlite3"
    connection = sqlite3.connect(corrupt)
    connection.execute("CREATE TABLE t(x)")
    connection.close()
    _corrupt_page_one(corrupt)
    before = corrupt.read_bytes()
    with pytest.raises(sqlite3.DatabaseError) as info:
        preflight_main_file(corrupt, "Test")
    assert is_corruption_error(info.value)
    assert corrupt.read_bytes() == before
    assert not (tmp_path / "corrupt.sqlite3-wal").exists()


def test_guard_trips_once_and_fails_fast(tmp_path, caplog):
    guard = SqliteStoreGuard("Test")
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SqliteStoreUnavailableError, match="file is empty"):
            guard.preflight(empty)
        with pytest.raises(SqliteStoreUnavailableError):
            guard.require_available()
    assert guard.available is False
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "preserved" in critical[0].getMessage()


def test_ui_surface_store_fails_closed_and_preserves_evidence(caplog):
    store = UiSurfaceStore.instance()
    store.upsert(_surface(), user_id="alice", conversation_id="conv")
    path = store.database_path
    UiSurfaceStore.reset()
    for sidecar in ("-wal", "-shm"):
        candidate = path.with_name(path.name + sidecar)
        if candidate.exists():
            candidate.unlink()
    _corrupt_page_one(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        store = UiSurfaceStore.instance()
    assert store.available is False
    assert UiSurfaceStore.instance() is store
    with pytest.raises(SqliteStoreUnavailableError, match="UI surface"):
        store.list(user_id="alice", conversation_id="conv")
    with pytest.raises(SqliteStoreUnavailableError):
        store.upsert(_surface(), user_id="alice", conversation_id="conv")
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1

    flowfile = FlowFile()
    result = _handle_ui_surfaces(
        None, "ui_surface_list", {"conversation_id": "conv"}, None,
        "alice", flowfile)
    assert result[0].get_attribute("http.response.status") == "503"
    assert b"unavailable" in result[0].get_content()


def test_agent_inbox_store_fails_closed_and_preserves_evidence(tmp_path, caplog):
    path = tmp_path / "inbox.sqlite3"
    inbox = AgentInboxStore(path)
    inbox.enqueue("c1", "claude", {"role": "user", "content": "hi",
                                   "msg_id": "m1", "ts": "t"}, "web", now=1)
    del inbox
    for sidecar in ("-wal", "-shm"):
        candidate = path.with_name(path.name + sidecar)
        if candidate.exists():
            candidate.unlink()
    _corrupt_page_one(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        inbox = AgentInboxStore(path)
    assert inbox.available is False
    with pytest.raises(SqliteStoreUnavailableError, match="Agent inbox"):
        inbox.pending_count("c1", "claude")
    with pytest.raises(SqliteStoreUnavailableError):
        inbox.enqueue("c1", "claude", {"role": "user", "content": "x",
                                       "msg_id": "m2", "ts": "t"}, "web", now=2)
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1


def test_healthy_stores_keep_working_and_report_available(tmp_path):
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    assert inbox.available is True
    assert inbox.pending_count("c1", "claude") == 0
    store = UiSurfaceStore.instance()
    assert store.available is True
    assert store.list(user_id="alice", conversation_id="conv") == []
