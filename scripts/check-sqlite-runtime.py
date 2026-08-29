#!/usr/bin/env python3
"""Verify the SQLite source archive and server-image runtime."""

from __future__ import annotations

import argparse
import hashlib
import hmac
from pathlib import Path
import sqlite3
from typing import Optional, Sequence, Tuple


MINIMUM_SQLITE_VERSION = "3.51.3"


def parse_version(value: str) -> Tuple[int, int, int]:
    """Parse one exact three-component SQLite version."""
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid SQLite version: {value!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def require_version(
    actual: str,
    minimum: str = MINIMUM_SQLITE_VERSION,
    exact: Optional[str] = None,
) -> None:
    """Raise when a runtime is below the supported floor or not the image pin."""
    actual_tuple = parse_version(actual)
    minimum_tuple = parse_version(minimum)
    if actual_tuple < minimum_tuple:
        raise RuntimeError(
            f"SQLite {actual} is unsupported; PawFlow requires >= {minimum}"
        )
    if exact is not None and actual_tuple != parse_version(exact):
        raise RuntimeError(
            f"SQLite {actual} does not match the server-image pin {exact}"
        )


def sha3_256(path: Path) -> str:
    """Return a bounded-memory SHA3-256 digest for one source archive."""
    digest = hashlib.sha3_256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, expected_sha3: str) -> None:
    """Reject a source archive whose official SHA3-256 does not match."""
    if len(expected_sha3) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha3
    ):
        raise ValueError("expected SHA3-256 must be 64 lowercase hex characters")
    actual = sha3_256(path)
    if not hmac.compare_digest(actual, expected_sha3):
        raise RuntimeError(
            f"SQLite source SHA3-256 mismatch: expected {expected_sha3}, got {actual}"
        )


def require_fts5() -> None:
    """Verify the full-text extension required by conversation search."""
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE VIRTUAL TABLE pawflow_fts5_check USING fts5(value)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    archive = commands.add_parser("archive", help="verify an official source archive")
    archive.add_argument("path", type=Path)
    archive.add_argument("--sha3", required=True)

    runtime = commands.add_parser("runtime", help="verify the linked SQLite runtime")
    runtime.add_argument("--minimum", default=MINIMUM_SQLITE_VERSION)
    runtime.add_argument("--exact")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "archive":
        verify_archive(args.path, args.sha3)
        print(f"SQLite source archive verified: {args.sha3}")
        return 0

    require_version(sqlite3.sqlite_version, args.minimum, args.exact)
    require_fts5()
    print(
        f"SQLite runtime verified: {sqlite3.sqlite_version} "
        f"(minimum {args.minimum}, FTS5 enabled)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
