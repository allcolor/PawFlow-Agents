"""Canonical physical configuration and legacy logical identity preservation."""

import copy
import json

import pytest

from core import server_physical_config as physical
from core._service_defs import ServiceDef
from core.service_registry import ServiceRegistry


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr(physical, "_root", lambda: tmp_path / "physicals")
    monkeypatch.setattr(
        physical._ServiceRegistryIOMixin, "_sensitive_keys", lambda _kind: {"token"})
    monkeypatch.setattr(physical._ServiceRegistryIOMixin, "_encrypt_config",
                        lambda cfg, _keys: {**cfg, "token": "enc:" + cfg["token"]})
    monkeypatch.setattr(physical._ServiceRegistryIOMixin, "_decrypt_config",
                        lambda cfg, _keys: {**cfg, "token": cfg["token"].removeprefix("enc:")})
    result = ServiceRegistry()
    result._loaded.add("alice")
    result._definitions["alice"] = {
        "MyWorkspace": ServiceDef(
            "MyWorkspace", "relay", scope="user", scope_id="alice",
            config={
                "server_managed": True, "server_scope": "user", "server_scope_id": "alice",
                "server_user_id": "alice", "token": "test-private-token",
                "server_workspace_dir": str(tmp_path / "original"),
                "server_workspace_host_dir": "/host/original",
                "server_home_volume": "original_home", "server_container_name": "original_container",
                "server_local_exec": True, "allow_service_tunnels": True, "mode": "readonly",
            }, created_at=123.0),
    }
    return result


def test_physical_records_follow_the_configured_runtime_directory(monkeypatch, tmp_path):
    from core import paths

    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(tmp_path / "environment-data"))
    first_runtime = tmp_path / "first-runtime"
    monkeypatch.setattr(paths, "RUNTIME_DIR", first_runtime)
    record = physical.legacy_group(ServiceDef(
        "Workspace", "relay", scope="user", scope_id="alice",
        config={"server_managed": True, "token": "test-token"}))
    physical.save_group(record)
    assert physical.load_groups("user", "alice") == [record]

    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "second-runtime")
    assert physical.load_groups("user", "alice") == []
    assert list(physical.stored_records()) == []

    monkeypatch.setattr(paths, "RUNTIME_DIR", first_runtime)
    assert physical.load_groups("user", "alice") == [record]


def test_legacy_migration_preserves_logical_identity_and_home(registry):
    original = copy.deepcopy(registry._definitions["alice"]["MyWorkspace"])
    group = physical.adopt_scope(registry, "user", "alice")[0]
    assert group["name"] == "MyWorkspace (physical)"
    assert group["container_name"] == "original_container"
    logical = registry._definitions["alice"]["MyWorkspace"]
    assert logical.created_at == original.created_at
    assert {key: logical.config[key] for key in original.config} == original.config
    assert set(registry._definitions["alice"]) == {"MyWorkspace"}
    assert registry._live_instances == {}
    assert physical.adopt_scope(registry, "user", "alice") == [group]


def test_disk_credentials_are_encrypted_and_complete_group_is_loaded(registry):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    filename = physical.record_path("user", "alice", group["physical_id"])
    saved = json.loads(filename.read_text())
    assert saved["members"][0]["config"]["token"] == "enc:test-private-token"
    assert filename.stat().st_mode & 0o777 == 0o600
    assert physical.load_groups("user", "alice") == [group]


def test_atomic_replace_failure_keeps_previous_group(registry, monkeypatch):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    filename = physical.record_path("user", "alice", group["physical_id"])
    before = filename.read_bytes()
    changed = copy.deepcopy(group)
    changed["name"] = "New label"

    def fail_replace(*_args):
        raise OSError("disk replacement failed")

    monkeypatch.setattr(physical.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        physical.save_group(changed)
    assert filename.read_bytes() == before
    assert list(filename.parent.glob(".physical-*")) == []


def test_prepare_rejects_stale_or_empty_edits_without_writing(registry):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    for body in (
        {"name": "Machine", "revision": 0, "workspaces": [{"service_id": "MyWorkspace"}]},
        {"name": "Machine", "revision": 1, "workspaces": []},
    ):
        with pytest.raises(ValueError):
            physical.prepare_group(registry, "user", "alice", "alice", body, group)
    assert physical.load_groups("user", "alice") == [group]


def test_prepare_preserves_permissions_paths_and_allocates_separate_new_root(registry, monkeypatch):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    # Admission is separately exercised against the real registry once the
    # explicit physical-group exception is installed.
    monkeypatch.setattr(registry, "_check_relay_conflict", lambda *_a, **_k: None)
    changed = physical.prepare_group(registry, "user", "alice", "alice", {
        "name": "Machine", "revision": group["revision"],
        "workspaces": [{"service_id": "MyWorkspace"}, {"service_id": "Documents"}],
    }, group)
    original, added = changed["members"]
    assert original["service_id"] == "MyWorkspace"
    assert original["config"]["server_home_volume"] == "original_home"
    assert original["config"]["mode"] == "readonly"
    assert original["config"]["server_local_exec"] is True
    assert added["config"]["server_workspace_dir"] != original["config"]["server_workspace_dir"]
    assert not added["config"]["server_workspace_dir"].startswith(
        original["config"]["server_workspace_dir"] + "/")
    assert added["config"]["server_home_volume"] == "pawflow_home_Documents"
    assert changed["physical_id"] == group["physical_id"]
    assert changed["revision"] == group["revision"] + 1
    assert physical.load_groups("user", "alice") == [group]


def test_projection_removes_retired_logical_but_preserves_data(registry, tmp_path):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    sentinel = tmp_path / "profile"
    sentinel.write_text("Chromium profile sentinel")
    group["deleted"] = True
    physical.save_group(group)
    physical.project_scope(registry, "user", "alice", [group])
    assert registry._definitions["alice"] == {}
    # Simulate a stale pre-migration service file after an interrupted save.
    registry._definitions["alice"]["MyWorkspace"] = ServiceDef.from_dict(group["members"][0])
    physical.adopt_scope(registry, "user", "alice")
    assert registry._definitions["alice"] == {}
    assert sentinel.read_text() == "Chromium profile sentinel"


def test_projection_rejects_cross_scope_or_foreign_service(registry):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    with pytest.raises(ValueError, match="scope mismatch"):
        physical.project_scope(registry, "user", "bob", [group])
    registry._definitions["alice"]["MyWorkspace"] = ServiceDef("MyWorkspace", "httpListener")
    with pytest.raises(ValueError, match="different owner"):
        physical.project_scope(registry, "user", "alice", [group])


@pytest.mark.parametrize("service_id", ["../unsafe", "", "Store"])
def test_prepare_rejects_invalid_or_reserved_logical_names(registry, service_id):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    with pytest.raises(ValueError, match="logical relay name"):
        physical.prepare_group(registry, "user", "alice", "alice", {
            "name": "Machine", "revision": 1, "workspaces": [{"service_id": service_id}],
        }, group)


def test_prepare_rejects_case_collisions_and_non_boolean_permissions(registry):
    group = physical.adopt_scope(registry, "user", "alice")[0]
    for workspaces in (
        [{"service_id": "MyWorkspace"}, {"service_id": "myworkspace"}],
        [{"service_id": "MyWorkspace", "server_local_exec": "false"}],
    ):
        with pytest.raises(ValueError):
            physical.prepare_group(registry, "user", "alice", "alice", {
                "name": "Machine", "revision": 1, "workspaces": workspaces,
            }, group)
    assert physical.load_groups("user", "alice") == [group]
