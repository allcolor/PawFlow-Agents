from pathlib import Path

import pytest

from pawflow_relay import manager
from pawflow_relay.manager_cli import main as relay_cli_main


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
