"""Read-only bootstrap canaries for the two historically corrupted stores."""

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
