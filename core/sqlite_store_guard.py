"""Shared fail-closed guard for durable SQLite stores.

A durable store must never delete, replace, checkpoint, or repair a damaged
database on its own: the file is evidence. The guard checks the main file
read-only before the store opens it for schema work and turns a corrupt or
unreadable file into one CRITICAL log line plus a typed error that callers
map to an "unavailable" response, instead of a traceback on every request.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

CORRUPTION_MARKERS = (
    "unsupported file format",
    "file is not a database",
    "database disk image is malformed",
    "malformed database schema",
    "store file is empty",
    "quick_check failed",
)

logger = logging.getLogger(__name__)


class SqliteStoreUnavailableError(RuntimeError):
    """Raised when a durable store is disabled after a database failure."""


def is_corruption_error(exc: BaseException) -> bool:
    """Return whether ``exc`` carries a known SQLite corruption signature."""
    lowered = str(exc).lower()
    return any(marker in lowered for marker in CORRUPTION_MARKERS)


def is_store_failure(exc: sqlite3.DatabaseError) -> bool:
    """Distinguish storage/open failures from ordinary SQL rejections."""
    return (
        type(exc) is sqlite3.DatabaseError
        or isinstance(exc, (sqlite3.OperationalError, sqlite3.InternalError))
    )


def describe_artifacts(database: Path) -> str:
    """Describe the preserved database, WAL and SHM without changing them."""
    artifacts = (
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
    )
    result = []
    for artifact in artifacts:
        try:
            stat = artifact.stat()
            digest = hashlib.sha256()
            with artifact.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result.append(
                f"path={artifact},size={stat.st_size},"
                f"mtime_ns={stat.st_mtime_ns},sha256={digest.hexdigest()}")
        except FileNotFoundError:
            result.append(f"path={artifact},missing=true")
        except OSError as exc:
            result.append(
                f"path={artifact},metadata_error={type(exc).__name__}")
    return " [" + "] [".join(result) + "]"


def preflight_main_file(database: Path, label: str) -> None:
    """Check an existing main file without opening or touching WAL/SHM.

    Raises ``sqlite3.DatabaseError`` for an empty file or a failed
    ``quick_check``. A missing file is valid: the store has not been created.
    """
    if not database.exists():
        return
    if database.stat().st_size == 0:
        raise sqlite3.DatabaseError(f"{label} store file is empty")
    uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        results = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
    finally:
        connection.close()
    if results != ["ok"]:
        raise sqlite3.DatabaseError(
            f"{label} store quick_check failed: " + "; ".join(results[:10]))


class SqliteStoreGuard:
    """Per-store circuit breaker: check once, trip permanently on failure.

    A tripped guard keeps the owning singleton alive so unrelated code keeps
    running, and every later access fails fast with
    ``SqliteStoreUnavailableError`` until the process restarts with a
    repaired database.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.unavailable_error = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_error

    def require_available(self) -> None:
        if self.unavailable_error:
            raise SqliteStoreUnavailableError(
                f"{self.label} store is unavailable: {self.unavailable_error}")

    def trip(self, exc: BaseException, database: Path) -> None:
        """Disable the store and emit one forensic CRITICAL line."""
        if self.unavailable_error:
            return
        self.unavailable_error = f"{type(exc).__name__}: {exc}"
        logger.critical(
            "%s store unavailable/corrupt. Database preserved at %s; nothing "
            "was deleted, replaced, or checkpointed. reason=%s "
            "corrupt_signature=%s artifacts=%s",
            self.label, database, self.unavailable_error,
            is_corruption_error(exc), describe_artifacts(database),
        )

    def preflight(self, database: Path) -> None:
        """Run the read-only check; trip and raise on a store failure."""
        try:
            preflight_main_file(database, self.label)
        except sqlite3.DatabaseError as exc:
            if not is_store_failure(exc):
                raise
            self.trip(exc, database)
        except OSError as exc:
            self.trip(sqlite3.OperationalError(str(exc)), database)
        self.require_available()

    def initialize(self, database: Path, open_schema) -> None:
        """Preflight, then run ``open_schema()``; corruption trips the guard.

        ``open_schema`` opens the real connection and creates the schema. A
        corruption signature raised there (for example ``unsupported file
        format`` from ``PRAGMA journal_mode``) disables the store instead of
        escaping as a bare ``sqlite3`` error.
        """
        self.preflight(database)
        try:
            open_schema()
        except sqlite3.DatabaseError as exc:
            if not is_corruption_error(exc):
                raise
            self.trip(exc, database)
            self.require_available()

    @contextmanager
    def runtime(self, database: Path):
        """Trip on a storage failure raised by one complete DB operation."""
        self.require_available()
        try:
            yield
        except sqlite3.DatabaseError as exc:
            if not is_store_failure(exc):
                raise
            self.trip(exc, database)
            self.require_available()


__all__ = [
    "CORRUPTION_MARKERS",
    "SqliteStoreGuard",
    "SqliteStoreUnavailableError",
    "describe_artifacts",
    "is_corruption_error",
    "is_store_failure",
    "preflight_main_file",
]
