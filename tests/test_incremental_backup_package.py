"""Security invariants for the bundled incremental-backup PFP."""

import importlib.util
from pathlib import Path
import sys
import types

import pytest


_ROOT = (
    Path(__file__).parents[1]
    / "lawassist"
    / "platform.incremental-backup.pfpdir"
    / "content"
    / "tools"
)


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("pawflow")
    sdk = types.ModuleType("pawflow")
    sdk.pfp = types.SimpleNamespace()
    sys.modules["pawflow"] = sdk
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("pawflow", None)
        else:
            sys.modules["pawflow"] = previous
    return module


def test_backup_envelopes_are_self_describing_and_restore_compatible():
    backup = _load("test_incremental_backup_tool", "incremental_backup/main.py")
    restore = _load("test_restore_backup_tool", "restore_from_backup/main.py")

    first = backup._encrypt("correct horse", b"payload")
    second = backup._encrypt("correct horse", b"payload")

    assert first.startswith(b"PFBK1")
    assert second.startswith(b"PFBK1")
    assert first != second
    assert backup._decrypt("correct horse", first) == b"payload"
    assert restore._decrypt("correct horse", second) == b"payload"


def test_backup_rejects_legacy_or_corrupt_envelopes():
    backup = _load("test_incremental_backup_invalid", "incremental_backup/main.py")

    with pytest.raises(ValueError, match="unsupported encrypted-backup envelope"):
        backup._decrypt("secret", b"old-global-salt-format")


def test_backup_has_no_mutable_global_salt():
    backup_source = (_ROOT / "incremental_backup/main.py").read_text(
        encoding="utf-8")
    restore_source = (_ROOT / "restore_from_backup/main.py").read_text(
        encoding="utf-8")

    assert "salt.bin" not in backup_source
    assert "salt.bin" not in restore_source
