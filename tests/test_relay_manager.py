import inspect
import json
import os
from pathlib import Path

import pytest

from pawflow_relay import manager
from pawflow_relay.manager_cli import main as relay_cli_main
from pawflow_relay.thread import RelayThread, _host_abs_path, _relay_tools_dir


def test_relay_manager_stores_servers_and_workspaces(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    server = manager.add_server(
        "prod", "https://pawflow.example:9090/", gateway_key="RoyBetty")
    assert server["name"] == "prod"
    assert server["url"] == "https://pawflow.example:9090"
    assert server["gateway_key"] == "RoyBetty"

    share = manager.add_workspace(
        "repo", "prod", str(workspace), mode="ro", docker_image="relay:python")
    assert share["server"] == "prod"
    assert share["path"] == str(workspace.resolve())
    assert share["mode"] == "ro"
    assert share["docker_image"] == "relay:python"
    assert share["allow_exec"] is True
    assert share["allow_remote_desktop"] is True
    assert share["allow_local"] is False
    assert share["relay_id"].startswith("fs_client_")

    assert manager.get_server("prod")["name"] == "prod"
    assert manager.get_workspace("repo")["name"] == "repo"


def test_relay_manager_cli_add_and_list(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    assert relay_cli_main([
        "server", "add", "local", "https://pawflow.example", "--gateway-key", "k",
    ]) == 0
    assert relay_cli_main([
        "workspace", "add", "repo", "--server", "local", "--path", str(workspace),
        "--mode", "rw", "--allow-local",
    ]) == 0
    assert relay_cli_main(["status"]) == 0

    out = capsys.readouterr().out
    assert "local\thttps://pawflow.example" in out
    assert "repo\tserver=local" in out
    assert "exec" in out
    assert "desktop" in out
    assert "servers=1 workspaces=1" in out


def test_relay_manager_cli_json_contract(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    assert relay_cli_main([
        "--json", "server", "add", "local", "https://pawflow.example",
    ]) == 0
    assert relay_cli_main([
        "--json", "workspace", "add", "repo", "--server", "local",
        "--path", str(workspace), "--no-exec", "--no-remote-desktop",
    ]) == 0
    assert relay_cli_main(["--json", "status"]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert '"name": "local"' in lines[0]
    assert '"allow_exec": false' in lines[1]
    assert '"servers"' in lines[2]
    assert '"workspaces"' in lines[2]


def test_relay_manager_workspace_permission_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager.add_server("prod", "https://pawflow.example")

    share = manager.add_workspace(
        "repo", "prod", str(workspace), allow_exec=False,
        allow_remote_desktop=False, allow_local=True)

    assert share["allow_exec"] is False
    assert share["allow_remote_desktop"] is False
    assert share["allow_local"] is True


def test_relay_runtime_root_env_points_tools_to_packaged_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "tools").mkdir(parents=True)

    monkeypatch.setenv("PAWFLOW_RELAY_RUNTIME_ROOT", str(runtime))

    assert _relay_tools_dir() == str(runtime.resolve() / "tools")


def test_relay_manager_stop_workspace_runtime_uninstalls_and_cleans_docker(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager.add_server("prod", "https://pawflow.example", gateway_key="k")
    manager.update_server_auth(
        "prod", gateway_cookie="gw", session_token="session", username="quentin")
    share = manager.add_workspace("repo", "prod", str(workspace))
    lock_path = manager._workspace_runtime_lock_path(share["relay_id"])
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
    calls = []

    def fake_api_call(server_url, method, path, body=None, **kwargs):
        calls.append((server_url, method, path, body, kwargs))
        return {"ok": True}

    monkeypatch.setattr(manager, "api_call", fake_api_call)
    monkeypatch.setattr(
        "pawflow_relay.thread.cleanup_relay_containers",
        lambda relay_id: calls.append(("cleanup", relay_id)) or 2,
    )

    result = manager.stop_workspace_runtime("repo")

    assert result["relay_id"] == share["relay_id"]
    assert result["service_uninstalled"] is True
    assert result["containers_removed"] == 2
    assert result["runtime_lock_removed"] is True
    assert not lock_path.exists()
    assert calls[0][0:4] == (
        "https://pawflow.example", "POST", "/api/ui",
        {"action": "service_uninstall", "service_id": share["relay_id"]},
    )
    assert calls[0][4]["session_token"] == "session"
    assert calls[0][4]["gateway_cookie"] == "gw"
    assert calls[1] == ("cleanup", share["relay_id"])


def test_host_helper_relative_paths_remain_workspace_scoped(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    resolved = _host_abs_path("subdir", str(root))
    assert Path(resolved) == root / "subdir"


def test_host_helper_relative_traversal_is_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError, match="Path traversal blocked"):
        _host_abs_path("../outside", str(root))


def test_host_helper_accepts_windows_drive_absolute_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    resolved = _host_abs_path(r"C:\\", str(root))
    assert resolved.startswith("C:")


def test_relay_thread_full_reconnect_reinstalls_with_new_token(monkeypatch, tmp_path):
    relay = RelayThread(
        server_url="https://pawflow.example:9090",
        session_token="session-token",
        username="user",
        directory=str(tmp_path),
        docker_image="relay:latest",
        relay_id="fs_user_repo",
    )
    relay.ws_token = "old-token"
    calls = []

    def fake_api(method, path, body=None):
        calls.append(("api", method, path, body))
        return {}

    def fake_api_retry(method, path, body=None, attempts=5):
        calls.append(("retry", method, path, body))
        return {}

    monkeypatch.setattr("pawflow_relay.thread.secrets.token_urlsafe", lambda n: "new-token")
    relay._api = fake_api
    relay._api_retry = fake_api_retry

    relay._restart_service_registration()

    assert calls[0] == (
        "api", "POST", "/api/ui",
        {"action": "service_uninstall", "service_id": "fs_user_repo"},
    )
    assert calls[1][0:3] == ("retry", "POST", "/api/ui")
    assert calls[1][3]["action"] == "service_install"
    assert calls[1][3]["service_name"] == "fs_user_repo"
    assert "token=new-token" in calls[1][3]["config_str"]
    assert relay.ws_token == "new-token"
    assert relay._registered is True


def test_relay_docker_loop_reregisters_service_without_killing_container():
    source = Path("pawflow_relay/thread.py").read_text(encoding="utf-8")

    assert 'if "HTTP/1.1 400 Bad Request" in msg:' in source
    bad_request_branch = source.split('if "HTTP/1.1 400 Bad Request" in msg:', 1)[1]
    bad_request_branch = bad_request_branch.split("except Exception:", 1)[0]
    assert "_service_reregister_requested.set()" in bad_request_branch
    assert "_full_reconnect_requested.set()" not in bad_request_branch

    health_branch = source.split("if _consecutive_fails >= 3:", 1)[1]
    health_branch = health_branch.split("\n\n                if self._stop_event.is_set():", 1)[0]
    assert "self._reregister_service()" in health_branch
    assert "self._docker_proc.kill()" not in health_branch
    assert "_full_reconnect_requested.set()" not in health_branch


def test_relay_docker_launcher_passes_token_as_equals_arg():
    source = inspect.getsource(RelayThread._run_docker_relay)

    assert 'f"--token={self.ws_token}"' in source
    assert '"--token", self.ws_token' not in source


def test_relay_manager_start_requires_logged_in_server(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager.add_server("prod", "https://pawflow.example")
    manager.add_workspace("repo", "prod", str(workspace))

    try:
        manager.start_workspace("repo")
    except ValueError as exc:
        assert "server login prod" in str(exc)
    else:
        raise AssertionError("start_workspace should require a logged-in server")


def test_relay_manager_start_rejects_duplicate_live_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager.add_server("prod", "https://pawflow.example")
    manager.update_server_auth(
        "prod", gateway_cookie="gw", session_token="session", username="quentin")
    share = manager.add_workspace("repo", "prod", str(workspace))
    lock_path = manager._workspace_runtime_lock_path(share["relay_id"])
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="already running"):
        manager.start_workspace("repo")


def test_relay_manager_start_cleans_runtime_lock_after_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager.add_server("prod", "https://pawflow.example")
    manager.update_server_auth(
        "prod", gateway_cookie="gw", session_token="session", username="quentin")
    share = manager.add_workspace("repo", "prod", str(workspace))
    events = []

    class FakeRelay:
        def __init__(self, *args, **kwargs):
            self.relay_id = kwargs["relay_id"]

        def start(self):
            events.append("start")

        def wait(self):
            events.append("wait")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr("pawflow_relay.thread.RelayThread", FakeRelay)

    manager.start_workspace("repo")

    assert events == ["start", "wait", "stop"]
    assert not manager._workspace_runtime_lock_path(share["relay_id"]).exists()


def test_relay_manager_delete_server_cascades_workspaces(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()

    manager.add_server("prod", "https://pawflow.example")
    manager.add_server("dev", "https://dev.pawflow.example")
    manager.add_workspace("repo", "prod", str(repo))
    manager.add_workspace("other", "dev", str(other))

    removed = manager.delete_server("prod")
    assert removed["server"]["name"] == "prod"
    assert removed["workspaces"] == ["repo"]

    with pytest.raises(ValueError):
        manager.get_server("prod")
    with pytest.raises(ValueError):
        manager.get_workspace("repo")
    assert manager.get_server("dev")["name"] == "dev"
    assert manager.get_workspace("other")["name"] == "other"


def test_relay_manager_delete_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay-home"))
    repo = tmp_path / "repo"
    repo.mkdir()

    manager.add_server("prod", "https://pawflow.example")
    manager.add_workspace("repo", "prod", str(repo))

    removed = manager.delete_workspace("repo")
    assert removed["name"] == "repo"
    with pytest.raises(ValueError):
        manager.get_workspace("repo")
