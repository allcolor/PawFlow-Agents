"""Server physical lifecycle regression tests without Docker or live services."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from core import server_physical_config as config
from core.server_physical_relay import ServerPhysicalRelayManager
from tests import test_server_physical_config

registry = test_server_physical_config.registry


@pytest.fixture
def manager(registry, monkeypatch):
    config.adopt_scope(registry, "user", "alice")
    monkeypatch.setattr(registry, "_save", lambda *_a: None)
    return ServerPhysicalRelayManager(registry)


def body(manager):
    record = manager._get("user", "alice", "MyWorkspace")
    return {
        "name": "Shared machine", "revision": record["revision"],
        "workspaces": [{"service_id": "MyWorkspace"}, {"service_id": "Documents"}],
    }


@pytest.mark.parametrize("connected", [True, False])
def test_service_install_uses_canonical_group_and_retains_it_on_timeout(
        manager, monkeypatch, connected):
    from unittest.mock import MagicMock
    from core import FlowFile
    from core.service_registry import ServiceRegistry
    from core.server_relay_manager import ServerRelayManager
    from tasks.ai.actions import _sf_k1

    registry = manager.registry
    definition = registry.get_definition("user", "alice", "MyWorkspace")
    original = copy.deepcopy(manager._get("user", "alice", "MyWorkspace"))
    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: registry)
    monkeypatch.setattr(registry, "install", lambda *_a, **_kw: definition)
    monkeypatch.setattr(registry, "is_connected", lambda *_a: connected)
    uninstall = MagicMock()
    monkeypatch.setattr(registry, "uninstall", uninstall)
    spawn = MagicMock()
    singleton = SimpleNamespace(
        service_relay_config=lambda *_a, **_kw: {"server_container_name": "raw-container"},
        spawn_service_relay=spawn)
    monkeypatch.setattr(ServerRelayManager, "get_instance", lambda: singleton)
    monkeypatch.setattr("core.server_physical_relay.physical_manager", lambda reg: manager)
    submit = MagicMock()
    monkeypatch.setattr(manager, "submit", submit)
    monkeypatch.setattr(_sf_k1, "_load_package_service_types", lambda *_a: set())
    monkeypatch.setattr(_sf_k1, "_visible_service_class", lambda *_a: SimpleNamespace())
    monkeypatch.setattr(_sf_k1, "_validate_required_service_config", lambda *_a: None)
    monkeypatch.setattr(_sf_k1, "_wait_for_service_connected", lambda *_a: connected)
    flowfile = FlowFile(content=b"{}")
    response = _sf_k1._handle_sf_k1(None, "service_install", {
        "service_name": "MyWorkspace", "service_type": "relay", "scope": "user",
    }, None, "alice", flowfile, (None,) * 6)
    payload = json.loads(response[0].get_content())
    if connected:
        assert payload["installed"] is True
    else:
        assert "did not connect" in payload["error"]
    submit.assert_called_once_with("autostart", "user", "alice", original["physical_id"])
    spawn.assert_not_called()
    uninstall.assert_not_called()
    assert manager._get("user", "alice", "MyWorkspace") == original
    assert registry.get_definition("user", "alice", "MyWorkspace") is definition


def test_invalid_save_never_interrupts_running_group(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(manager, "_stop", lambda _r: calls.append("stop"))
    with pytest.raises(ValueError, match="at least one"):
        manager.execute("save", "user", "alice", "MyWorkspace",
                        body={"name": "Bad", "revision": 1, "workspaces": []}, user_id="alice")
    assert calls == []


def test_saved_members_restart_whole_group_once_and_remain_logical(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(manager, "_stop", lambda r: calls.append(("stop", len(r["members"]))))
    monkeypatch.setattr(manager, "_start", lambda r: calls.append(("start", len(r["members"]))))
    result = manager.execute("save", "user", "alice", "MyWorkspace", body=body(manager), user_id="alice")
    assert calls == [("stop", 1), ("start", 2)]
    assert result["name"] == "Shared machine"
    assert set(manager.registry._definitions["alice"]) == {"MyWorkspace", "Documents"}
    assert "token" not in json.dumps(result)


def test_atomic_save_failure_restores_previous_running_group(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(manager, "_stop", lambda r: calls.append(("stop", len(r["members"]))))
    monkeypatch.setattr(manager, "_start", lambda r: calls.append(("start", len(r["members"]))))

    def fail(_record):
        raise OSError("disk full")

    monkeypatch.setattr(config, "save_group", fail)
    with pytest.raises(OSError, match="disk full"):
        manager.execute("save", "user", "alice", "MyWorkspace", body=body(manager), user_id="alice")
    assert calls == [("stop", 1), ("start", 1)]
    assert config.load_groups("user", "alice")[0]["revision"] == 1


def test_failed_stop_prevents_save(manager, monkeypatch):
    def fail(_record):
        raise RuntimeError("wrong container owner")

    monkeypatch.setattr(manager, "_stop", fail)
    with pytest.raises(RuntimeError, match="wrong container"):
        manager.execute("save", "user", "alice", "MyWorkspace", body=body(manager), user_id="alice")
    assert config.load_groups("user", "alice")[0]["revision"] == 1


def test_stop_intent_survives_failed_cleanup_and_ensure_cannot_respawn(manager, monkeypatch):
    def fail(_record):
        raise RuntimeError("Docker unavailable")

    monkeypatch.setattr(manager, "_stop", fail)
    with pytest.raises(RuntimeError):
        manager.execute("stop", "user", "alice", "MyWorkspace")
    monkeypatch.setattr(manager, "_start", lambda *_a, **_k: pytest.fail("respawn after explicit stop"))
    result = manager.execute("ensure", "user", "alice", "MyWorkspace")
    assert result["enabled"] is False
    assert manager.registry._definitions["alice"]["MyWorkspace"].enabled is False


def test_failed_delete_retains_disabled_group_for_cleanup_retry(manager, monkeypatch):
    attempts = []

    def stop(record):
        attempts.append(record["physical_id"])
        if len(attempts) == 1:
            raise RuntimeError("Docker unavailable")

    monkeypatch.setattr(manager, "_stop", stop)
    with pytest.raises(RuntimeError, match="Docker unavailable"):
        manager.execute("delete", "user", "alice", "MyWorkspace")
    record = manager.describe("user", "alice", "MyWorkspace")
    assert record["enabled"] is False
    assert record["deleted"] is False
    assert "MyWorkspace" in manager.registry._definitions["alice"]
    monkeypatch.setattr(manager, "_start", lambda *_a, **_k: pytest.fail("respawn"))
    manager.execute("ensure", "user", "alice", "MyWorkspace")
    result = manager.execute("delete", "user", "alice", "MyWorkspace")
    assert result["deleted"] is True
    assert attempts == ["MyWorkspace", "MyWorkspace"]
    assert manager.registry._definitions["alice"] == {}


def test_stopped_save_stays_stopped(manager, monkeypatch):
    monkeypatch.setattr(manager, "_stop", lambda _r: None)
    manager.execute("stop", "user", "alice", "MyWorkspace")
    monkeypatch.setattr(manager, "_start", lambda *_a, **_k: pytest.fail("started stopped group"))
    result = manager.execute("save", "user", "alice", "MyWorkspace", body=body(manager), user_id="alice")
    assert result["enabled"] is False


def test_start_registers_all_children_before_one_spawn(manager, monkeypatch):
    from core.server_relay_manager import ServerRelayManager

    record = config.prepare_group(manager.registry, "user", "alice", "alice",
                                  body(manager), manager._get("user", "alice", "MyWorkspace"))
    calls = []
    monkeypatch.setattr(manager.registry, "_connect_one", lambda _s, name: calls.append(name))
    backend = SimpleNamespace(spawn_service_relay=lambda *_a, **kw: calls.append(kw))
    monkeypatch.setattr(ServerRelayManager, "get_instance", lambda: backend)
    manager._start(record)
    assert calls[:2] == ["MyWorkspace", "Documents"]
    assert len(calls) == 3
    assert calls[2]["physical"] == record


def test_group_retry_uses_shared_grace_and_cooldown(manager, monkeypatch):
    now = [100.0]
    calls = []
    monkeypatch.setattr("core.server_physical_relay.time.monotonic", lambda: now[0])
    monkeypatch.setattr(manager, "_running", lambda _r: True)
    monkeypatch.setattr(manager, "_connected", lambda _r: False)
    monkeypatch.setattr(manager, "_start", lambda _r, **kw: calls.append(kw))
    record = manager._get("user", "alice", "MyWorkspace")
    manager._ensure(record)
    now[0] = 114
    manager._ensure(record)
    assert calls == []
    now[0] = 115
    manager._ensure(record)
    manager._ensure(record)
    assert calls == [{"replace": True}]


def test_permission_is_canonical_and_revision_invalidates_old_form(manager):
    previous = body(manager)
    manager.set_permission("user", "alice", "MyWorkspace", "MyWorkspace", "server_local_exec", False)
    persisted = config.load_groups("user", "alice")[0]
    assert persisted["members"][0]["config"]["server_local_exec"] is False
    with pytest.raises(ValueError, match="reload"):
        config.prepare_group(manager.registry, "user", "alice", "alice", previous, persisted)


def test_removed_member_can_be_readded_without_losing_home_or_token(manager, monkeypatch):
    monkeypatch.setattr(manager, "_stop", lambda _r: None)
    monkeypatch.setattr(manager, "_start", lambda _r: None)
    original = copy.deepcopy(manager._get("user", "alice", "MyWorkspace")["members"][0])
    edit = body(manager)
    edit["workspaces"] = [{"service_id": "Documents"}]
    manager.execute("save", "user", "alice", "MyWorkspace", body=edit, user_id="alice")
    manager.execute("save", "user", "alice", "MyWorkspace", body=body(manager), user_id="alice")
    restored = manager._get("user", "alice", "MyWorkspace")["members"][0]
    for key in ("token", "server_workspace_dir", "server_workspace_host_dir", "server_home_volume"):
        assert restored["config"][key] == original["config"][key]


@pytest.mark.parametrize("retired", [False, True])
def test_case_variant_cannot_replace_retained_logical_identity(manager, monkeypatch, retired):
    monkeypatch.setattr(manager, "_stop", lambda _r: None)
    monkeypatch.setattr(manager, "_start", lambda _r: None)
    if retired:
        edit = body(manager)
        edit["workspaces"] = [{"service_id": "Documents"}]
        manager.execute("save", "user", "alice", "MyWorkspace", body=edit, user_id="alice")
    original = copy.deepcopy(manager._get("user", "alice", "MyWorkspace"))
    edit = body(manager)
    edit["workspaces"] = [{"service_id": "myworkspace"}]
    with pytest.raises(ValueError, match="case"):
        manager.execute("save", "user", "alice", "MyWorkspace", body=edit, user_id="alice")
    assert manager._get("user", "alice", "MyWorkspace") == original


@pytest.mark.parametrize("method", ["enable", "disable", "uninstall", "rename", "update_config"])
def test_logical_mutations_cannot_bypass_parent(manager, method):
    args = ["user", "alice", "MyWorkspace"]
    if method == "rename":
        args.append("Renamed")
    elif method == "update_config":
        args.append({"mode": "readwrite"})
    with pytest.raises(ValueError, match="physical"):
        getattr(manager.registry, method)(*args)
    assert set(manager.registry._definitions["alice"]) == {"MyWorkspace"}


def test_submit_returns_before_docker_finishes_and_rejects_overlapping_action(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def execute(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        return manager.public_record(manager._get("user", "alice", "MyWorkspace"))

    monkeypatch.setattr(manager, "execute", execute)
    accepted = manager.submit("restart", "user", "alice", "MyWorkspace")
    try:
        assert accepted["accepted"] is True
        assert entered.wait(1)
        assert manager.operation(accepted["operation_id"])["status"] == "running"
        assert manager.submit("ensure", "user", "alice", "MyWorkspace")["accepted"] is False
        with pytest.raises(ValueError, match="already running"):
            manager.submit("stop", "user", "alice", "MyWorkspace")
    finally:
        release.set()


def test_hydration_overlays_stale_definitions_before_connection(manager):
    record = manager._get("user", "alice", "MyWorkspace")
    record["enabled"] = False
    config.save_group(record)
    hydrated = config.hydrate_scope("user", "alice", manager.registry._definitions["alice"])
    assert hydrated["MyWorkspace"].enabled is False
    assert hydrated["MyWorkspace"].config["server_physical_id"] == "MyWorkspace"


def test_cross_scope_logical_name_cannot_claim_retained_group(manager):
    registry = manager.registry
    registry._loaded.add("bob")
    registry._definitions["bob"] = {}
    with pytest.raises(ValueError, match="already exists|physical configuration"):
        config.prepare_group(registry, "user", "bob", "bob",
                             {"name": "Other", "workspaces": [{"service_id": "MyWorkspace"}]}, None)

def test_group_brief_connection_does_not_reset_outage_grace(manager, monkeypatch):
    now, connected, stable = [100.0], [False], [False]
    calls = []
    monkeypatch.setattr("core.server_physical_relay.time.monotonic", lambda: now[0])
    monkeypatch.setattr(manager, "_connected", lambda _r: connected[0])
    monkeypatch.setattr(manager, "_stable", lambda _r: stable[0])
    monkeypatch.setattr(manager, "_running", lambda _r: True)
    monkeypatch.setattr(manager, "_start", lambda _r, **kw: calls.append(kw))
    record = manager._get("user", "alice", "MyWorkspace")
    manager._ensure(record)
    now[0], connected[0] = 110, True
    manager._ensure(record)
    now[0], connected[0] = 116, False
    manager._ensure(record)
    assert calls == [{"replace": True}]


def test_stop_removes_exact_container_and_routes_without_deleting_storage(manager, monkeypatch):
    calls = []
    record = manager._get("user", "alice", "MyWorkspace")
    root = config.scope_directory("user", "alice")
    profile = root / "chromium-sentinel"
    profile.write_text("keep")
    monkeypatch.setattr("core._server_relay_container.stop_managed_relay_container",
                        lambda name: calls.append(("container", name)))
    monkeypatch.setattr(manager.registry, "_disconnect_one",
                        lambda scope, name: calls.append(("route", scope, name)))
    manager._stop(record)
    assert calls == [("container", "original_container"), ("route", "alice", "MyWorkspace")]
    assert profile.read_text() == "keep"


def test_launch_plan_separates_mounts_and_private_credentials(manager, monkeypatch):
    from core import _server_physical_launch as launch

    monkeypatch.setattr("core._relay_naming._chown_for_host_runner", lambda _p: None)
    monkeypatch.setattr(launch, "_relay_runtime_host_dir", str)
    record = config.prepare_group(manager.registry, "user", "alice", "alice",
                                  body(manager), manager._get("user", "alice", "MyWorkspace"))
    original = [
        "docker", "run", "--name", "original_container",
        "--volume", "/host/original:/workspace",
        "--volume", "original_home:/home/pawflow",
        "--volume", "/code:/opt/pawflow:ro",
        "--env", "PAWFLOW_RELAY_DIR=/workspace",
        "--env", "PAWFLOW_RELAY_SERVER=ws://host:9000/ws/relay/MyWorkspace",
        "--env", "PAWFLOW_RELAY_TOKEN=test-private-token",
        "--env", "PAWFLOW_INTERNAL_TOKEN=test-internal-token",
        "--env", "HOME=/home/pawflow", "test-image", "python3", "launcher.py",
    ]
    command = launch.group_command(original, "test-image", record)
    from pawflow_relay._physical_runtime import SECCOMP_PROFILE
    assert "seccomp=" + str(SECCOMP_PROFILE) in command
    assert "seccomp=unconfined" not in command
    assert "test-private-token" not in " ".join(command)
    assert "test-internal-token" not in " ".join(command)
    assert "/host/original:/workspace" not in command
    assert "original_home:/home/pawflow" not in command
    assert "/code:/opt/pawflow:ro" in command
    assert "--config-file" in command
    filename = launch.launch_file(record)
    assert filename.stat().st_mode & 0o777 == 0o600
    exports = json.loads(filename.read_text())["exports"]
    assert len(exports) == 2
    assert len({e["root_mount"] for e in exports}) == 2
    assert len({e["home_mount"] for e in exports}) == 2
    assert [e["mode"] for e in exports] == ["ro", "rw"]
    assert all(e["environment"]["PAWFLOW_RELAY_DIR"] == "/workspace" for e in exports)
    assert exports[0]["environment"]["PAWFLOW_RELAY_TOKEN"] == "test-private-token"
    assert exports[1]["environment"]["PAWFLOW_RELAY_TOKEN"] != "test-private-token"
    assert exports[1]["environment"]["PAWFLOW_RELAY_SERVER"].endswith("/Documents")


@pytest.mark.parametrize("action", ["get", "save", "start", "stop", "restart", "delete", "operation"])
def test_physical_admin_actions_require_admin(action):
    from core import FlowFile
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    flowfile = FlowFile(attributes={"http.auth.roles": "user"})
    result = _handle_admin_settings(
        None, "admin_server_physical_" + action, {}, None, "alice", flowfile)
    assert result[0].get_attribute("http.response.status") == "403"


def test_physical_admin_save_submits_group_with_record_owner_and_returns_202(manager, monkeypatch):
    from core import FlowFile
    from core.service_registry import ServiceRegistry
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    calls = []
    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: manager.registry)
    manager.registry._physical_relay_manager = manager
    monkeypatch.setattr(manager, "submit", lambda *args, **kw:
                        calls.append((args, kw)) or {"accepted": True, "operation_id": "op"})
    payload = {**body(manager), "scope": "user", "scope_id": "alice",
               "physical_id": "MyWorkspace", "user_id": "wrong-owner"}
    flowfile = FlowFile(attributes={"http.auth.roles": "admin"})
    result = _handle_admin_settings(
        None, "admin_server_physical_save", payload, None, "admin", flowfile)
    assert result[0].get_attribute("http.response.status") == "202"
    assert calls[0][0] == ("save", "user", "alice", "MyWorkspace")
    assert calls[0][1]["user_id"] == "alice"
    assert json.loads(result[0].get_content())["operation_id"] == "op"


def test_physical_admin_get_is_redacted_and_scope_is_required(manager, monkeypatch):
    from core import FlowFile
    from core.service_registry import ServiceRegistry
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: manager.registry)
    manager.registry._physical_relay_manager = manager
    request = {"scope": "user", "scope_id": "alice", "physical_id": "MyWorkspace"}
    result = _handle_admin_settings(None, "admin_server_physical_get", request, None, "admin",
                                    FlowFile(attributes={"http.auth.roles": "admin"}))
    payload = json.loads(result[0].get_content())
    assert payload["workspaces"][0]["service_id"] == "MyWorkspace"
    assert "test-private-token" not in json.dumps(payload)
    result = _handle_admin_settings(None, "admin_server_physical_get", {}, None, "admin",
                                    FlowFile(attributes={"http.auth.roles": "admin"}))
    assert result[0].get_attribute("http.response.status") == "400"


def test_physical_operation_status_rejects_another_scope(manager, monkeypatch):
    from core import FlowFile
    from core.service_registry import ServiceRegistry
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: manager.registry)
    manager.registry._physical_relay_manager = manager
    manager._operations["op"] = {"scope": "user", "scope_id": "bob"}
    result = _handle_admin_settings(
        None, "admin_server_physical_operation",
        {"scope": "user", "scope_id": "alice", "operation_id": "op"}, None, "admin",
        FlowFile(attributes={"http.auth.roles": "admin"}))
    assert result[0].get_attribute("http.response.status") == "400"
    assert "scope mismatch" in json.loads(result[0].get_content())["error"]

def test_service_connection_and_recovery_route_only_to_physical_manager(manager):
    from services.filesystem_service import RelayService

    calls = []
    record = manager._get("user", "alice", "MyWorkspace")
    definition = config.projected_definitions(record)["MyWorkspace"]
    service = RelayService({"_service_id": "MyWorkspace", **definition.config})
    service._physical_relay_manager = SimpleNamespace(submit=lambda *args:
                                                     calls.append(args) or {"accepted": True})
    assert service._start_managed_server_relay() is True
    assert service.ensure_managed_relay_alive() is True
    assert calls == [
        ("autostart", "user", "alice", "MyWorkspace"),
        ("ensure", "user", "alice", "MyWorkspace"),
    ]
    with pytest.raises(ValueError, match="physical"):
        service.restart_managed_relay()
    assert "_physical_relay_manager" not in service.config


def test_admin_inventory_recovers_physical_scope_without_service_projection(manager, monkeypatch):
    monkeypatch.setattr("core.service_registry._user_services_dir",
                        lambda: config._root() / "absent-services")
    scopes = manager.registry.iter_all_scopes(conv_pairs=[])
    assert ("user", "alice", "alice", "") in scopes


def test_retained_names_are_reserved_when_their_scope_is_not_loaded(manager):
    manager.registry._definitions.clear()
    with pytest.raises(ValueError, match="physical configuration"):
        manager.registry._check_relay_conflict(
            "myworkspace", "user", "bob", {"token": "unrelated-peer-token"})
