"""Physical configuration and transparent single-directory migration."""

import copy
import json
import os

import pytest

from pawflow_relay import manager, physical_config
from pawflow_relay.physical_plan import plan_physical_relay


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(manager, "get_server", lambda name: {
        "name": name, "username": "alice", "url": "https://pawflow.invalid",
    })
    return tmp_path


def entry(config, name):
    directory = config / name
    directory.mkdir(exist_ok=True)
    return {"name": name, "path": str(directory)}


def test_legacy_share_migration_retains_every_original_field(config):
    original = {
        "name": "MyWorkspace", "relay_id": "fs_alice_1234", "server": "server",
        "path": str(config), "docker_image": "relay:stable", "mode": "ro",
        "allow_exec": False, "allow_local": True,
        "allow_remote_desktop": False, "allow_service_tunnels": True,
        "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-02-01T00:00:00Z",
    }
    manager._save_json(manager._WORKSPACES_FILE, {"MyWorkspace": original})
    physical = physical_config.list_physicals()[0]
    logical = physical["workspaces"][0]
    assert {key: logical[key] for key in original} == original
    assert physical["name"] == "MyWorkspace"
    assert physical["physical_id"] == original["relay_id"]
    assert manager.get_workspace("MyWorkspace")["relay_id"] == original["relay_id"]
    plan = plan_physical_relay(physical["physical_id"], physical["workspaces"])
    assert plan.exports[0].workspace_target == "/workspace"
    assert plan.exports[0].home_volume == "pawflow_home_fs_alice_1234"
    stored = manager._load_json(manager._WORKSPACES_FILE)
    assert stored == {"MyWorkspace": original}
    assert physical_config.list_physicals() == [physical]


@pytest.mark.parametrize("valid", [True, False])
def test_legacy_preflight_does_not_persist_migration(config, valid):
    original = {
        **entry(config, "Code"), "relay_id": "fs_alice_1234", "server": "server",
        "docker_image": "relay:stable", "mode": "rw",
    }
    manager._save_json(manager._WORKSPACES_FILE, {"Code": original})
    path = manager.relay_home() / manager._WORKSPACES_FILE
    before = path.read_bytes()
    proposed = {**original, "mode": "ro" if valid else "invalid"}
    if valid:
        result = physical_config.save_physical(
            "Code", "server", "relay:stable", [proposed], validate_only=True)
        assert result["workspaces"][0]["mode"] == "ro"
    else:
        with pytest.raises(ValueError, match="mode"):
            physical_config.save_physical(
                "Code", "server", "relay:stable", [proposed], validate_only=True)
    assert path.read_bytes() == before


def test_named_logicals_are_the_only_workspace_resources(config):
    first, second = entry(config, "Code"), entry(config, "Documents")
    physical = physical_config.save_physical("Laptop", "server", "relay:stable", [first, second])
    assert physical["name"] == "Laptop"
    assert {s["relay_id"] for s in physical["workspaces"]} == {"Code", "Documents"}
    assert {s["name"] for s in manager.list_workspaces()} == {"Code", "Documents"}
    with pytest.raises(ValueError, match="Unknown relay workspace"):
        manager.get_workspace("Laptop")
    assert all(s["physical_name"] == "Laptop" for s in manager.list_workspaces())
    assert {p["name"] for p in physical_config.list_physicals()} == {"Laptop"}


def test_explicit_name_reaches_service_registration(config):
    from pawflow_relay.thread import RelayThread

    share = manager.add_workspace("Local label", "server", str(config), relay_name="SharedCode")
    calls = []
    relay = RelayThread("https://pawflow.invalid", "session", "alice", str(config),
                        relay_id=share["relay_id"])
    relay.ws_token = "test-token"
    relay._api = lambda method, path, body: calls.append(body)
    relay._install_service()
    assert calls[0]["service_name"] == "SharedCode"
    assert share["relay_id"] == "SharedCode"


def test_singleton_update_preserves_identity_home_and_other_migration(config):
    first = manager.add_workspace("One", "server", str(config), relay_name="ExistingOne")
    second_dir = config / "two"
    second_dir.mkdir()
    legacy = copy.deepcopy(first)
    legacy.update(name="Two", path=str(second_dir), relay_id="fs_alice_two")
    legacy.pop("physical_name")
    legacy.pop("physical_id")
    records = manager._load_json(manager._WORKSPACES_FILE)
    records["Two"] = legacy
    manager._save_json(manager._WORKSPACES_FILE, records)
    result = manager.add_workspace("One", "server", str(config), allow_exec=False)
    assert result["relay_id"] == "ExistingOne"
    assert result["physical_id"] == first["physical_id"]
    assert manager._load_json(manager._WORKSPACES_FILE)["Two"]["physical_id"] == "fs_alice_two"


def test_membership_replacement_retains_existing_ids_and_modes(config):
    first, second = entry(config, "One"), entry(config, "Two")
    first.update(relay_id="fs_alice_old", mode="ro", allow_exec=False)
    old = physical_config.save_physical("Machine", "server", "relay:stable", [first])
    new = physical_config.save_physical("Machine", "server", "relay:stable", [first, second])
    assert new["physical_id"] == old["physical_id"]
    assert new["revision"] != old["revision"]
    assert new["workspaces"][0]["relay_id"] == "fs_alice_old"
    assert new["workspaces"][0]["mode"] == "ro"
    assert new["workspaces"][0]["allow_exec"] is False
    reordered = physical_config.save_physical("Machine", "server", "relay:stable", [second, first])
    assert reordered["revision"] == new["revision"]


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "foreign", "identity", "mode", "permission"])
def test_invalid_group_updates_do_not_replace_saved_configuration(config, mutation):
    first, second = entry(config, "One"), entry(config, "Two")
    physical_config.save_physical("Machine", "server", "relay:stable", [first])
    physical_config.save_physical("Other", "server", "relay:stable", [second])
    before = manager._load_json(manager._WORKSPACES_FILE)
    values = [copy.deepcopy(first)]
    if mutation == "empty":
        values = []
    elif mutation == "duplicate":
        values *= 2
    elif mutation == "foreign":
        values.append(second)
    elif mutation == "identity":
        values[0]["relay_id"] = "Changed"
    elif mutation == "mode":
        values[0]["mode"] = "anything"
    else:
        values[0]["allow_exec"] = "false"
    with pytest.raises(ValueError):
        physical_config.save_physical("Machine", "server", "relay:stable", values)
    assert manager._load_json(manager._WORKSPACES_FILE) == before


def test_running_group_cannot_change_membership_or_be_deleted(config):
    first = entry(config, "One")
    physical = physical_config.save_physical("Machine", "server", "relay:stable", [first])
    lock = manager._workspace_runtime_lock_path(physical["physical_id"])
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    with pytest.raises(ValueError, match="Stop physical relay"):
        physical_config.save_physical("Machine", "server", "relay:stable", [first, entry(config, "Two")])
    with pytest.raises(ValueError, match="Stop physical relay"):
        physical_config.delete_physical("Machine")
    assert len(physical_config.get_physical("Machine")["workspaces"]) == 1


def test_grouped_logical_configuration_cannot_be_changed_independently(config):
    first, second = entry(config, "One"), entry(config, "Two")
    physical_config.save_physical("Machine", "server", "relay:stable", [first, second])
    with pytest.raises(ValueError, match="complete physical"):
        manager.add_workspace("One", "server", first["path"])
    with pytest.raises(ValueError, match="complete physical"):
        manager.delete_workspace("One")


def test_deleting_group_retains_directory_and_profile_sentinel(config):
    first = entry(config, "One")
    sentinel = config / "One" / "profile-sentinel"
    sentinel.write_text("preserved", encoding="utf-8")
    physical_config.save_physical("Machine", "server", "relay:stable", [first])
    physical_config.delete_physical("Machine")
    assert manager.list_workspaces() == []
    assert physical_config.list_physicals() == []
    assert sentinel.read_text(encoding="utf-8") == "preserved"


def test_validate_running_group_without_mutation(config):
    first = entry(config, "Code")
    physical = physical_config.save_physical("Machine", "server", "relay:stable", [first])
    lock = manager._workspace_runtime_lock_path(physical["physical_id"])
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    before = manager._load_json(manager._WORKSPACES_FILE)
    result = physical_config.save_physical(
        "Machine", "server", "relay:stable", [first, entry(config, "Docs")], validate_only=True)
    assert len(result["workspaces"]) == 2
    assert manager._load_json(manager._WORKSPACES_FILE) == before
    assert json.loads(lock.read_text())["pid"] == os.getpid()


@pytest.mark.parametrize("same_group", [True, False])
def test_case_insensitive_published_names_are_rejected_atomically(config, same_group):
    first, second = entry(config, "Code"), entry(config, "code")
    physical_config.save_physical("Machine", "server", "relay:stable", [first])
    before = manager._load_json(manager._WORKSPACES_FILE)
    with pytest.raises(ValueError, match="distinct|unique"):
        physical_config.save_physical(
            "Machine" if same_group else "Other", "server", "relay:stable",
            [first, second] if same_group else [second])
    assert manager._load_json(manager._WORKSPACES_FILE) == before


def test_cli_saves_complete_group_and_status_keeps_logical_inventory(config, capsys, monkeypatch):
    from pawflow_relay import manager_cli

    first, second = entry(config, "Code"), entry(config, "Docs")
    assert manager_cli.main([
        "--json", "physical", "save", "Laptop", "--server", "server",
        "--workspace", "Code", first["path"], "--workspace", "Docs", second["path"],
        "--read-only", "Docs",
    ]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["name"] == "Laptop"
    assert {w["name"]: w["mode"] for w in saved["workspaces"]} == {"Code": "rw", "Docs": "ro"}
    monkeypatch.setattr(manager_cli, "list_servers", list)
    assert manager_cli.main(["--json", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert {w["relay_id"] for w in status["workspaces"]} == {"Code", "Docs"}
    assert [p["name"] for p in status["physicals"]] == ["Laptop"]


@pytest.mark.parametrize("validate_only", [False, True])
def test_implicit_rename_reusing_removed_path_is_rejected(config, validate_only):
    first = entry(config, "Code")
    first.update(relay_id="ExistingCode", mode="ro", allow_exec=False)
    physical_config.save_physical("Laptop", "server", "relay:test", [first])
    path = manager.relay_home() / manager._WORKSPACES_FILE
    before = path.read_bytes()
    with pytest.raises(ValueError, match="relay_id"):
        physical_config.save_physical("Laptop", "server", "relay:test", [
            {"name": "Renamed", "path": first["path"]},
        ], validate_only=validate_only)
    assert path.read_bytes() == before


def test_explicit_rename_preserves_identity_permissions_and_home(config):
    first = entry(config, "Code")
    first.update(relay_id="ExistingCode", mode="ro", allow_exec=False)
    saved = physical_config.save_physical("Laptop", "server", "relay:test", [first])
    renamed = physical_config.save_physical("Laptop", "server", "relay:test", [
        {"name": "Renamed", "relay_id": "ExistingCode", "path": first["path"]},
    ])
    assert renamed["physical_id"] == saved["physical_id"]
    logical = renamed["workspaces"][0]
    assert logical["mode"] == "ro"
    assert logical["allow_exec"] is False
    assert logical["created_at"] == saved["workspaces"][0]["created_at"]
    assert plan_physical_relay(renamed["physical_id"], [logical]).exports[0].home_volume == (
        "pawflow_home_ExistingCode")


@pytest.mark.parametrize("explicit_identity", [False, True])
def test_explicit_replacement_or_later_add_can_reuse_removed_path(config, explicit_identity):
    first, retained = entry(config, "Code"), entry(config, "Docs")
    physical_config.save_physical("Laptop", "server", "relay:test", [first, retained])
    replacement = {"name": "Replacement", "path": first["path"]}
    if explicit_identity:
        replacement["relay_id"] = "NewIdentity"
    else:
        physical_config.save_physical("Laptop", "server", "relay:test", [retained])
    saved = physical_config.save_physical(
        "Laptop", "server", "relay:test", [retained, replacement])
    assert {w["relay_id"] for w in saved["workspaces"]} == {
        "Docs", "NewIdentity" if explicit_identity else "Replacement",
    }


def test_read_commands_leave_legacy_configuration_unchanged(config, capsys, monkeypatch):
    from pawflow_relay import manager_cli

    legacy = {**entry(config, "Code"), "relay_id": "ExistingCode",
              "server": "server", "docker_image": "relay:test", "mode": "ro"}
    manager._save_json(manager._WORKSPACES_FILE, {"Code": legacy})
    path = manager.relay_home() / manager._WORKSPACES_FILE
    before = path.read_bytes()
    monkeypatch.setattr(manager_cli, "list_servers", list)
    for args in (["physical", "list"], ["workspace", "list"], ["status"]):
        assert manager_cli.main(["--json", *args]) == 0
        capsys.readouterr()
        assert path.read_bytes() == before
    assert not (manager.relay_home() / "workspaces.lock").exists()


def test_cli_requires_server_without_config_stdin(config, capsys):
    from pawflow_relay import manager_cli

    with pytest.raises(SystemExit) as exc:
        manager_cli.main(["physical", "save", "Laptop", "--workspace", "Code", str(config)])
    assert exc.value.code == 2
    assert "requires --server unless --config-stdin" in capsys.readouterr().err


def test_cli_omitted_mode_preserves_readonly(config, capsys):
    from pawflow_relay import manager_cli

    first = entry(config, "Code")
    first.update(mode="ro", allow_exec=False)
    physical_config.save_physical("Laptop", "server", "relay:test", [first])
    assert manager_cli.main([
        "--json", "physical", "save", "Laptop", "--server", "server",
        "--docker-image", "relay:test", "--workspace", "Code", first["path"],
    ]) == 0
    saved = json.loads(capsys.readouterr().out)["workspaces"][0]
    assert saved["mode"] == "ro"
    assert saved["allow_exec"] is False


def test_cli_json_preflight_preserves_permissions_and_saved_config(config, capsys, monkeypatch):
    import io

    from pawflow_relay import manager_cli

    first = entry(config, "Code")
    first.update(relay_id="ExistingCode", mode="ro", allow_exec=False,
                 allow_local=True, allow_remote_desktop=False, allow_service_tunnels=True)
    definition = {"server": "server", "docker_image": "relay:test", "workspaces": [first]}
    monkeypatch.setattr(manager_cli.sys, "stdin", io.StringIO(json.dumps(definition)))
    assert manager_cli.main([
        "--json", "physical", "save", "Laptop", "--config-stdin", "--validate-only"]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert manager.list_workspaces() == []
    logical = validated["workspaces"][0]
    assert {key: logical[key] for key in first} == first


@pytest.mark.parametrize("validate_only", [False, True])
def test_parent_rename_preserves_group_and_logical_identities(config, validate_only):
    first, second = entry(config, "Code"), entry(config, "Docs")
    first.update(relay_id="ExistingCode", mode="ro", allow_exec=False)
    original = physical_config.save_physical("Laptop", "server", "relay:test", [first, second])
    before = manager._load_json(manager._WORKSPACES_FILE)
    renamed = physical_config.save_physical(
        "Renamed laptop", "server", "relay:test", [first, second],
        physical_id=original["physical_id"], validate_only=validate_only)
    assert renamed["physical_id"] == original["physical_id"]
    assert renamed["revision"] == original["revision"]
    assert [e.home_volume for e in plan_physical_relay(
        renamed["physical_id"], renamed["workspaces"]).exports] == [
        e.home_volume for e in plan_physical_relay(
            original["physical_id"], original["workspaces"]).exports]
    assert {w["physical_name"] for w in renamed["workspaces"]} == {"Renamed laptop"}
    assert renamed["workspaces"][0]["mode"] == "ro"
    if validate_only:
        assert manager._load_json(manager._WORKSPACES_FILE) == before
    else:
        assert [p["name"] for p in physical_config.list_physicals()] == ["Renamed laptop"]
        assert {w["relay_id"] for w in manager.list_workspaces()} == {"ExistingCode", "Docs"}


@pytest.mark.parametrize("target,id_value", [
    ("Other", "ExistingCode"), ("Renamed", "missing"), ("Renamed", ""),
])
def test_invalid_parent_rename_is_atomic(config, target, id_value):
    first = {**entry(config, "Code"), "relay_id": "ExistingCode"}
    physical_config.save_physical("Laptop", "server", "relay:test", [first])
    physical_config.save_physical("Other", "server", "relay:test", [entry(config, "Docs")])
    before = manager._load_json(manager._WORKSPACES_FILE)
    with pytest.raises(ValueError):
        physical_config.save_physical(
            target, "server", "relay:test", [first], physical_id=id_value)
    assert manager._load_json(manager._WORKSPACES_FILE) == before


def test_cli_can_rename_parent_with_stable_physical_id(config, capsys):
    from pawflow_relay import manager_cli

    first = {**entry(config, "Code"), "relay_id": "ExistingCode"}
    original = physical_config.save_physical("Laptop", "server", "relay:test", [first])
    assert manager_cli.main([
        "--json", "physical", "save", "Renamed", "--physical-id", original["physical_id"],
        "--server", "server", "--docker-image", "relay:test", "--workspace", "Code", first["path"],
    ]) == 0
    renamed = json.loads(capsys.readouterr().out)
    assert renamed["physical_id"] == original["physical_id"]
    assert [p["name"] for p in physical_config.list_physicals()] == ["Renamed"]


def test_status_reports_external_runtime_and_rename_requires_stop(config, capsys, monkeypatch):
    from pawflow_relay import manager_cli

    first = entry(config, "Code")
    physical = physical_config.save_physical("Laptop", "server", "relay:test", [first])
    with manager._workspace_runtime_lock("Laptop", physical["physical_id"]):
        monkeypatch.setattr(manager_cli, "list_servers", list)
        assert manager_cli.main(["--json", "status"]) == 0
        assert json.loads(capsys.readouterr().out)["physicals"][0]["running"] is True
        with pytest.raises(ValueError, match="Stop physical relay"):
            physical_config.save_physical(
                "Renamed", "server", "relay:test", [first], physical_id=physical["physical_id"])
    assert physical_config.get_physical("Laptop")["running"] is False


@pytest.mark.parametrize("containers", [0, 2])
def test_idle_cleanup_uninstalls_only_when_owned_runtime_exists(config, monkeypatch, containers):
    physical = physical_config.save_physical(
        "Laptop", "server", "relay:test", [entry(config, "Code")])
    monkeypatch.setattr(manager, "get_server", lambda _name: {
        "url": "https://fixture.invalid", "session_token": "fixture-session",
    })
    calls = []
    monkeypatch.setattr(manager, "api_call", lambda *a, **k: calls.append(k["body"]) or {})
    monkeypatch.setattr("pawflow_relay.thread.cleanup_relay_containers", lambda _id: containers)
    result = manager.stop_workspace_runtime("Laptop")
    assert result["already_stopped"] is (containers == 0)
    assert result["service_uninstalled"] is (containers > 0)
    assert calls == ([{"action": "service_uninstall", "service_id": physical["workspaces"][0]["relay_id"]}]
                     if containers else [])


def test_cleanup_failure_leaves_server_registration_intact(config, monkeypatch):
    physical_config.save_physical("Laptop", "server", "relay:test", [entry(config, "Code")])
    monkeypatch.setattr(manager, "get_server", lambda _name: {
        "url": "https://fixture.invalid", "session_token": "fixture-session",
    })
    calls = []
    monkeypatch.setattr(manager, "api_call", lambda *a, **k: calls.append(k))

    def fail_cleanup(_id):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr("pawflow_relay.thread.cleanup_relay_containers", fail_cleanup)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        manager.stop_workspace_runtime("Laptop")
    assert calls == []


def test_cli_verifies_all_children_and_rejects_a_logical_lifecycle_target(config, capsys, monkeypatch):
    from pawflow_relay import manager_cli

    physical_config.save_physical(
        "Laptop", "server", "relay:test", [entry(config, "Code"), entry(config, "Docs")])
    checked = []

    def verify(name):
        checked.append(name)
        return {"relay_id": name, "connected": name == "Code"}

    monkeypatch.setattr(manager_cli, "verify_workspace_connected", verify)
    assert manager_cli.main(["--json", "verify", "Laptop"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert checked == ["Code", "Docs"]
    assert result["connected"] is False
    assert result["physical"] == "Laptop"
    for action in ("verify", "start", "cleanup"):
        assert manager_cli.main(["--json", action, "Code"]) == 1
        assert "Unknown physical relay" in capsys.readouterr().err
