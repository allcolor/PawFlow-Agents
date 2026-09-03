"""Relay lifecycle and server coordinator tests for ScratchDir."""

import base64
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import paths
from core.scratchdir_manager import ScratchDirManager
from core.scratchdir_models import ScratchDirError
from core.scratchdir_store import ScratchDirStore
from pawflow_relay import scratchdir as relay_scratchdir
from pawflow_relay._relay_session import build_connection_params


@pytest.fixture()
def relay_root(tmp_path, monkeypatch):
    value = tmp_path / "relay-runtime" / "scratchdirs"
    monkeypatch.setenv("PAWFLOW_SCRATCHDIR_ROOT", str(value))
    return value


def _message(**changes):
    value = {
        "scratch_id": "sd_" + "a" * 32,
        "scope_hash": "b" * 64,
        "operation_id": "op-1",
        "epoch": 1,
        "expires_at": time.time() + 3600,
        "quota_bytes": 1024,
        "quota_files": 10,
    }
    value.update(changes)
    return value


def test_relay_advertises_scratchdir_capability():
    params = build_connection_params(
        "ws://localhost/ws", ".", False, True, False, False, False)
    assert "scratchdir_v1" in params.info["capabilities"]


def test_relay_lifecycle_is_idempotent_and_outside_workspace(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    message = _message()
    created = relay_scratchdir.ensure(
        message, workspace_root=str(workspace))
    repeated = relay_scratchdir.ensure(
        message, workspace_root=str(workspace))
    assert repeated == created
    assert created["state"] == "active"
    assert created["locator"] == message["scratch_id"]
    physical = relay_root / message["scratch_id"]
    assert (physical / "files").is_dir()
    assert not physical.is_relative_to(workspace)

    payload = physical / "files" / "report.txt"
    payload.write_text("hello", encoding="utf-8")
    current = relay_scratchdir.status(
        message, workspace_root=str(workspace))
    assert current["observed_files"] == 1
    assert current["observed_bytes"] == 5

    renewal = _message(operation_id="renew-1")
    renewed = relay_scratchdir.renew(
        renewal, workspace_root=str(workspace))
    assert renewed["operation_id"] == "renew-1"

    clearing = _message(operation_id="clear-1", epoch=2)
    cleared = relay_scratchdir.clear(
        clearing, workspace_root=str(workspace))
    assert cleared["state"] == "cleared"
    assert not (physical / "files").exists()
    assert relay_scratchdir.clear(
        clearing, workspace_root=str(workspace)) == cleared


def test_relay_scope_epoch_and_runtime_root_are_fail_closed(
        relay_root, tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(_message(), workspace_root=str(workspace))

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.status(
            _message(scope_hash="c" * 64), workspace_root=str(workspace))
    assert exc.value.code == "scratchdir_owner_mismatch"

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.ensure(
            _message(operation_id="op-2", epoch=0),
            workspace_root=str(workspace))
    assert exc.value.code == "scratchdir_request_invalid"

    monkeypatch.setenv(
        "PAWFLOW_SCRATCHDIR_ROOT", str(workspace / ".scratchdirs"))
    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.status(_message(), workspace_root=str(workspace))
    assert exc.value.code == "scratchdir_root_unsafe"


def test_relay_over_quota_allows_status_reads_and_delete_recovery(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(
        _message(quota_bytes=4), workspace_root=str(workspace))
    files = relay_root / _message()["scratch_id"] / "files"
    (files / "large").write_bytes(b"12345")
    (files / "also-large").write_bytes(b"67890")

    current = relay_scratchdir.status(
        _message(), workspace_root=str(workspace))
    assert current["observed_bytes"] == 10
    assert current["observed_files"] == 2

    request = {
        "scratchdir": {
            "scratch_id": _message()["scratch_id"],
            "scope_hash": _message()["scope_hash"],
            "epoch": 1,
        },
        "path": "large",
    }
    for action in ("exists", "stat", "read_file", "list_dir",
                   "search", "grep", "delete_file"):
        root, target = relay_scratchdir.resolve_operation(
            action, request, workspace_root=str(workspace))
        assert root == str(files)
        assert target == str(files / "large")

    # A single deletion may leave the root above quota. Its post-check must
    # still succeed so repeated deletions can eventually restore compliance.
    (files / "large").unlink()
    relay_scratchdir.validate_operation(
        "delete_file", request, workspace_root=str(workspace))

    renewed = relay_scratchdir.renew(
        _message(operation_id="renew-over-quota"),
        workspace_root=str(workspace))
    assert renewed["observed_bytes"] == 5

    cleared = relay_scratchdir.clear(
        _message(operation_id="clear-over-quota", epoch=2),
        workspace_root=str(workspace))
    assert cleared["state"] == "cleared"


def test_relay_over_quota_still_blocks_growing_operations(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(
        _message(quota_bytes=4), workspace_root=str(workspace))
    files = relay_root / _message()["scratch_id"] / "files"
    (files / "large").write_bytes(b"12345")
    request = {
        "scratchdir": {
            "scratch_id": _message()["scratch_id"],
            "scope_hash": _message()["scope_hash"],
            "epoch": 1,
        },
        "path": "new",
    }

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.resolve_operation(
            "write_file", request, workspace_root=str(workspace))
    assert exc.value.code == "scratchdir_quota_bytes"
    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.validate_operation(
            "write_file", request, workspace_root=str(workspace))
    assert exc.value.code == "scratchdir_quota_bytes"


def test_relay_usage_allows_in_tree_symlinks_without_double_counting(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(
        _message(), workspace_root=str(workspace))
    files = relay_root / _message()["scratch_id"] / "files"
    library = files / ".venv" / "lib"
    library.mkdir(parents=True)
    (library / "module.py").write_text("value", encoding="utf-8")
    (files / ".venv" / "lib64").symlink_to("lib", target_is_directory=True)

    current = relay_scratchdir.status(
        _message(), workspace_root=str(workspace))

    assert current["observed_files"] == 1
    assert current["observed_bytes"] == 5


def test_relay_usage_accepts_a_copied_python_virtual_environment(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    message = _message(quota_bytes=50 * 1024 * 1024, quota_files=1000)
    relay_scratchdir.ensure(message, workspace_root=str(workspace))
    files = relay_root / message["scratch_id"] / "files"
    subprocess.run(
        [
            sys.executable, "-m", "venv", "--copies", "--without-pip",
            str(files / ".venv"),
        ],
        check=True,
    )

    current = relay_scratchdir.status(
        message, workspace_root=str(workspace))

    assert current["observed_files"] > 0
    assert current["observed_bytes"] > 0


def test_relay_usage_explains_default_python_venv_symlink_recovery(
        relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    message = _message(quota_bytes=50 * 1024 * 1024, quota_files=1000)
    relay_scratchdir.ensure(message, workspace_root=str(workspace))
    files = relay_root / message["scratch_id"] / "files"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(files / ".venv")],
        check=True,
    )

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.status(message, workspace_root=str(workspace))

    assert exc.value.code == "scratchdir_unsafe_entry"
    assert "python -m venv --copies" in str(exc.value)
    assert "clear the ScratchDir" in str(exc.value)


@pytest.mark.parametrize("target", ("outside", "broken", "loop"))
def test_relay_usage_rejects_unsafe_symlinks(
        relay_root, tmp_path, target):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(
        _message(), workspace_root=str(workspace))
    files = relay_root / _message()["scratch_id"] / "files"
    link = files / "unsafe"
    if target == "outside":
        outside = relay_root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link.symlink_to(outside)
    elif target == "broken":
        link.symlink_to("missing")
    else:
        link.symlink_to("unsafe")

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.status(
            _message(), workspace_root=str(workspace))

    assert exc.value.code == "scratchdir_unsafe_entry"


def test_relay_usage_rejects_a_symlinked_files_root(relay_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relay_scratchdir.ensure(
        _message(), workspace_root=str(workspace))
    files = relay_root / _message()["scratch_id"] / "files"
    files.rmdir()
    outside = relay_root / "outside"
    outside.mkdir()
    files.symlink_to(outside, target_is_directory=True)

    with pytest.raises(relay_scratchdir.ScratchDirRelayError) as exc:
        relay_scratchdir.status(
            _message(), workspace_root=str(workspace))

    assert exc.value.code == "scratchdir_unsafe_entry"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", tmp_path / "metadata")
    ScratchDirStore._instance = None
    value = ScratchDirStore.instance()
    yield value
    ScratchDirStore._instance = None


class _FakeRelay:
    _service_id = "relay-1"

    def __init__(self, *, capable=True):
        self.capable = capable
        self.records = {}
        self.calls = []

    def supports_capability(self, name):
        return self.capable and name == "scratchdir_v1"

    def scratchdir_ensure(self, **kwargs):
        self.calls.append(("ensure", kwargs))
        record = {
            **kwargs,
            "locator": kwargs["scratch_id"],
            "observed_bytes": 0,
            "observed_files": 0,
        }
        self.records[kwargs["scratch_id"]] = record
        return record

    def scratchdir_status(self, **kwargs):
        self.calls.append(("status", kwargs))
        return {
            **self.records[kwargs["scratch_id"]],
            "observed_bytes": 12,
            "observed_files": 2,
        }

    def scratchdir_renew(self, **kwargs):
        self.calls.append(("renew", kwargs))
        self.records[kwargs["scratch_id"]].update(kwargs)
        return self.records[kwargs["scratch_id"]]

    def scratchdir_clear(self, **kwargs):
        self.calls.append(("clear", kwargs))
        return {**kwargs, "state": "cleared"}

    def _request(self, action, path, **kwargs):
        self.calls.append((action, {"path": path, **kwargs}))
        if action == "list_dir":
            return [
                {"name": "build", "kind": "directory", "size": 0,
                 "modified": ""},
                {"name": "build/report.txt", "kind": "file", "size": 5,
                 "modified": "2026-08-21T00:00:00Z"},
            ]
        if action == "read_file_chunked":
            return {"data": base64.b64encode(b"hello").decode(),
                    "total_chunks": 1, "chunk_size": 1024}
        raise AssertionError(action)


def test_manager_coordinates_relay_before_metadata(store):
    relay = _FakeRelay()
    manager = ScratchDirManager(relay, store)
    created = manager.execute(
        action="ensure", user_id="u", conversation_id="c",
        agent_name="a", ttl_hours=24)
    assert created["status"] == "active"
    assert created["url"] == "fs://scratchdir/"
    record = store.get("u", "c", "a", "relay-1")
    assert record.id == relay.calls[0][1]["scratch_id"]
    assert record.locator == record.id

    status = manager.execute(
        action="status", user_id="u", conversation_id="c",
        agent_name="a")
    assert status["observed_bytes"] == 12
    assert status["observed_files"] == 2

    manager.execute(
        action="renew", user_id="u", conversation_id="c",
        agent_name="a", ttl_hours=12)
    cleared = manager.execute(
        action="clear", user_id="u", conversation_id="c",
        agent_name="a")
    assert cleared["status"] == "cleared"
    assert [name for name, _args in relay.calls] == [
        "ensure", "status", "renew", "clear"]


def test_manager_never_activates_metadata_when_relay_fails(store):
    class BrokenRelay(_FakeRelay):
        def scratchdir_ensure(self, **kwargs):
            raise RuntimeError("offline")

    manager = ScratchDirManager(BrokenRelay(), store)
    with pytest.raises(ScratchDirError) as exc:
        manager.execute(
            action="ensure", user_id="u", conversation_id="c",
            agent_name="a")
    assert exc.value.code == "scratchdir_unavailable"
    assert store.get("u", "c", "a", "relay-1") is None


def test_manager_requires_advertised_capability(store):
    manager = ScratchDirManager(_FakeRelay(capable=False), store)
    with pytest.raises(ScratchDirError) as exc:
        manager.execute(
            action="ensure", user_id="u", conversation_id="c",
            agent_name="a")
    assert exc.value.code == "scratchdir_capability_missing"


def test_manager_tree_is_bounded_and_contains_only_logical_paths(store):
    relay = _FakeRelay()
    manager = ScratchDirManager(relay, store)
    manager.ensure("u", "c", "a", "relay-1")

    result = manager.tree("u", "c", "a", "relay-1", max_entries=1)

    assert result["truncated"] is True
    assert result["entries"] == [{
        "path": "build", "kind": "directory", "size": 0, "modified": ""}]
    assert "locator" not in result


def test_manager_promotes_file_with_bounded_relay_copy(store, monkeypatch):
    relay = _FakeRelay()
    manager = ScratchDirManager(relay, store)
    manager.ensure("u", "c", "a", "relay-1")
    stored = {}

    class _Files:
        def store_file(self, filename, source_path, content_type, **kwargs):
            stored.update(filename=filename, content=Path(source_path).read_bytes(),
                          content_type=content_type, **kwargs)
            return "abc123def456"

    from core.file_store import FileStore
    monkeypatch.setattr(FileStore, "instance", classmethod(lambda cls: _Files()))

    result = manager.promote(
        "u", "c", "a", "relay-1", path="build/report.txt")

    assert result["url"] == "fs://filestore/abc123def456/report.txt"
    assert stored["content"] == b"hello"
    assert stored["conversation_id"] == "c"
    assert stored["agent_name"] == "a"


def test_manager_cleanup_expired_uses_fenced_clear(store):
    relay = _FakeRelay()
    manager = ScratchDirManager(relay, store)
    manager.ensure("u", "c", "a", "relay-1", ttl_hours=1)
    record = store.get("u", "c", "a", "relay-1")
    store.get("u", "c", "a", "relay-1", now=record.expires_at)

    assert manager.cleanup_expired("u", "c", "a", "relay-1") is True
    assert store.get("u", "c", "a", "relay-1").state == "cleared"
    assert relay.calls[-1][0] == "clear"
