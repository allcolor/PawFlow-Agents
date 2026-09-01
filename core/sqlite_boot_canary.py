"""Opt-in, read-only SQLite integrity canaries for server bootstrap."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from pathlib import Path

CANARY_ENV = "PAWFLOW_SQLITE_BOOT_CANARY"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv(CANARY_ENV, "").strip().lower() in _TRUE_VALUES


def _database_targets() -> tuple[tuple[str, Path], ...]:
    from core import paths

    return (
        ("mcp_servers", paths.SYSTEM_DIR / "mcp_servers.sqlite3"),
        ("scratchdirs", paths.SCRATCHDIRS_DIR / "scratchdirs.sqlite3"),
    )


def _sidecar_metadata(database: Path) -> dict[str, dict[str, int | bool]]:
    result = {}
    for suffix in ("-wal", "-shm"):
        artifact = Path(str(database) + suffix)
        try:
            stat = artifact.stat()
            result[suffix[1:]] = {
                "exists": True,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        except FileNotFoundError:
            result[suffix[1:]] = {"exists": False}
    return result


def _page_one_metadata(database: Path) -> dict[str, object]:
    with database.open("rb") as handle:
        first = handle.read(65536)
    if len(first) < 100:
        raise sqlite3.DatabaseError(
            f"SQLite main file is shorter than its 100-byte header ({len(first)})")
    if first[:16] != b"SQLite format 3\x00":
        raise sqlite3.DatabaseError("SQLite main file header magic is invalid")
    encoded_page_size = int.from_bytes(first[16:18], "big")
    page_size = 65536 if encoded_page_size == 1 else encoded_page_size
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise sqlite3.DatabaseError(
            f"SQLite main file page size is invalid ({page_size})")
    return {
        "page_size": page_size,
        "page1_sha256": hashlib.sha256(first[:page_size]).hexdigest(),
        "change_counter": int.from_bytes(first[24:28], "big"),
        "header_36_62": first[36:63].hex(),
        "version_valid_for": int.from_bytes(first[92:96], "big"),
        "sqlite_version": int.from_bytes(first[96:100], "big"),
    }


def _inspect_database(name: str, database: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "path": str(database),
        "sidecars": _sidecar_metadata(database),
    }
    try:
        stat = database.stat()
    except FileNotFoundError:
        result["status"] = "missing"
        return result

    result.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    connection: sqlite3.Connection | None = None
    try:
        result.update(_page_one_metadata(database))
        uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        integrity = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity != ["ok"]:
            raise sqlite3.DatabaseError(
                "integrity_check failed: " + "; ".join(integrity[:10]))
        result["status"] = "ok"
    except (OSError, sqlite3.DatabaseError) as exc:
        result["status"] = "corrupt"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            connection.close()
    return result


def run_sqlite_boot_canary(phase: str) -> list[dict[str, object]]:
    """Inspect both stores without WAL recovery and stop an opted-in bad boot."""
    if not _enabled():
        return []
    phase = str(phase or "").strip()
    if not phase:
        raise ValueError("SQLite bootstrap canary phase is required")

    results = [
        _inspect_database(name, database)
        for name, database in _database_targets()
    ]
    for result in results:
        log = logger.critical if result["status"] == "corrupt" else logger.info
        log(
            "SQLite bootstrap canary phase=%s database=%s status=%s "
            "path=%s size=%s mtime_ns=%s page1_sha256=%s "
            "change_counter=%s version_valid_for=%s header_36_62=%s "
            "sidecars=%s error=%s",
            phase, result["name"], result["status"], result["path"],
            result.get("size"), result.get("mtime_ns"),
            result.get("page1_sha256"), result.get("change_counter"),
            result.get("version_valid_for"), result.get("header_36_62"),
            result["sidecars"], result.get("error", ""),
        )

    failed = [result["name"] for result in results if result["status"] == "corrupt"]
    if failed:
        raise RuntimeError(
            f"SQLite bootstrap canary failed at {phase}: {', '.join(failed)}")
    return results
