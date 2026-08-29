import hashlib
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path("scripts/check-sqlite-runtime.py")
SPEC = importlib.util.spec_from_file_location("check_sqlite_runtime", SCRIPT_PATH)
assert SPEC and SPEC.loader
sqlite_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sqlite_check)


def test_sqlite_version_floor_and_exact_pin():
    sqlite_check.require_version("3.51.3")
    sqlite_check.require_version("3.53.4", exact="3.53.4")

    with pytest.raises(RuntimeError, match="requires >= 3.51.3"):
        sqlite_check.require_version("3.51.2")
    with pytest.raises(RuntimeError, match="does not match"):
        sqlite_check.require_version("3.53.3", exact="3.53.4")
    with pytest.raises(ValueError, match="invalid SQLite version"):
        sqlite_check.require_version("3.53")


def test_sqlite_source_archive_requires_the_official_sha3(tmp_path):
    archive = tmp_path / "sqlite.tar.gz"
    archive.write_bytes(b"official source placeholder")
    digest = hashlib.sha3_256(archive.read_bytes()).hexdigest()

    sqlite_check.verify_archive(archive, digest)

    with pytest.raises(RuntimeError, match="SHA3-256 mismatch"):
        sqlite_check.verify_archive(archive, "0" * 64)
    with pytest.raises(ValueError, match="64 lowercase hex"):
        sqlite_check.verify_archive(archive, "not-a-digest")
