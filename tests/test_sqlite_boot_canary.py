"""Read-only SQLite canaries for every PawFlow-owned database."""

import sqlite3
from pathlib import Path

import pytest

from core import sqlite_boot_canary as canary


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('healthy')")


def test_disabled_canary_does_not_resolve_or_open_targets(monkeypatch):
    monkeypatch.delenv(canary.CANARY_ENV, raising=False)
    monkeypatch.setattr(
        canary, "_database_targets",
        lambda: (_ for _ in ()).throw(AssertionError("targets were resolved")),
    )

    assert canary.run_sqlite_boot_canary("disabled") == []


def test_healthy_canary_is_read_only_and_reports_page_one(tmp_path, monkeypatch):
    mcp = tmp_path / "mcp.sqlite3"
    scratch = tmp_path / "scratch.sqlite3"
    _database(mcp)
    _database(scratch)
    before = {path: path.read_bytes() for path in (mcp, scratch)}
    monkeypatch.setenv(canary.CANARY_ENV, "1")
    monkeypatch.setattr(
        canary, "_database_targets",
        lambda: (("mcp_servers", mcp), ("scratchdirs", scratch)),
    )

    results = canary.run_sqlite_boot_canary("after_task_registration")

    assert [result["status"] for result in results] == ["ok", "ok"]
    assert all(len(result["page1_sha256"]) == 64 for result in results)
    assert all(len(result["header_36_62"]) == 54 for result in results)
    assert {path: path.read_bytes() for path in (mcp, scratch)} == before
    assert not list(tmp_path.glob("*-wal"))
    assert not list(tmp_path.glob("*-shm"))


def test_production_targets_cover_every_internal_store(tmp_path, monkeypatch):
    from core import paths

    data = tmp_path / "data"
    runtime = data / "runtime"
    system = data / "system"
    indexes = runtime / "conversation_index"
    indexes.mkdir(parents=True)
    (indexes / "alice.db").touch()
    (indexes / "bob.db").touch()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(paths, "SYSTEM_DIR", system)
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", runtime / "scratchdirs")
    monkeypatch.setattr(paths, "TODOLISTS_DIR", runtime / "todolists")
    monkeypatch.setattr(paths, "SCRATCHPADS_DIR", runtime / "scratchpads")
    monkeypatch.setattr(paths, "CONVERSATION_INDEX_DIR", indexes)
    monkeypatch.setattr(paths, "USAGE_DB_FILE", system / "usage.db")
    monkeypatch.setattr(paths, "LLM_ROUTING_DB_FILE", system / "llm_routing.db")

    targets = dict(canary._database_targets())

    assert set(targets) == {
        "mcp_servers", "scratchdirs", "ui_surfaces", "agent_inbox", "a2a",
        "confirmations", "flow_runs", "workflow_runs", "workflow_proposals",
        "workflow_parent_invocations", "media_projects", "todos", "scratchpads",
        "usage", "llm_routing", "conversation_index:alice.db",
        "conversation_index:bob.db",
    }
    assert targets["confirmations"] == data / "confirmations.db"
    assert targets["usage"] == system / "usage.db"
    assert targets["todos"] == runtime / "todolists" / "todos.sqlite3"


def test_corrupt_canary_fails_at_named_phase_without_touching_evidence(
        tmp_path, monkeypatch, caplog):
    database = tmp_path / "mcp.sqlite3"
    _database(database)
    corrupted = bytearray(database.read_bytes())
    corrupted[44:48] = (0x7E6F4E6D).to_bytes(4, "big")
    database.write_bytes(corrupted)
    monkeypatch.setenv(canary.CANARY_ENV, "true")
    monkeypatch.setattr(
        canary, "_database_targets", lambda: (("mcp_servers", database),),
    )

    with pytest.raises(RuntimeError, match="before_flow_restore"):
        canary.run_sqlite_boot_canary("before_flow_restore")

    assert database.read_bytes() == bytes(corrupted)
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert "status=corrupt" in caplog.text
    assert "header_36_62=" in caplog.text


def test_abort_canary_reports_corruption_without_raising_or_opening_sqlite(
        tmp_path, monkeypatch, caplog):
    database = tmp_path / "usage.db"
    _database(database)
    corrupted = bytearray(database.read_bytes())
    corrupted[39:63] = bytes.fromhex("1703030013") + bytes(range(1, 20))
    database.write_bytes(corrupted)
    before = database.read_bytes()
    monkeypatch.setenv(canary.CANARY_ENV, "1")
    monkeypatch.setattr(
        canary, "_database_targets", lambda: (("usage", database),))
    monkeypatch.setattr(
        canary.sqlite3, "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("abort canary opened SQLite")))

    results = canary.run_sqlite_abort_canary()

    assert results[0]["status"] == "corrupt"
    assert database.read_bytes() == before
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert "SQLite abort canary" in caplog.text


def test_llm_abort_runs_opt_in_header_canary(monkeypatch):
    from core.llm_client import LLMClient

    calls = []
    monkeypatch.setattr(canary, "run_sqlite_abort_canary", lambda: calls.append(1))

    LLMClient("openai").abort()

    assert calls == [1]


def test_cli_places_canaries_around_native_registration_and_flow_restore():
    source = Path("cli.py").read_text(encoding="utf-8")
    start = source[source.index("def cmd_start("):]

    before_tasks = start.index(
        'run_sqlite_boot_canary("before_task_registration")')
    register = start.index("register_all_tasks()")
    after_tasks = start.index(
        'run_sqlite_boot_canary("after_task_registration")')
    before_restore = start.index(
        'run_sqlite_boot_canary("before_flow_restore")')
    restore = start.index("restore_from_disk(")
    after_restore = start.index(
        'run_sqlite_boot_canary("after_flow_restore")')

    assert before_tasks < register < after_tasks
    assert after_tasks < before_restore < restore < after_restore
