"""Grouped Docker launch and logical-only service registration."""

import json
import threading
from types import SimpleNamespace

import pytest

from pawflow_relay import _physical_runtime as runtime
from pawflow_relay import manager
from pawflow_relay.physical_thread import PhysicalRelayThread


def group(tmp_path):
    return {
        "name": "Physical", "physical_id": "physical-owner",
        "workspaces": [
            {"name": "One", "relay_id": "One", "server": "server", "path": str(tmp_path / "one"),
             "docker_image": "relay:stable", "mode": "rw"},
            {"name": "Two", "relay_id": "Two", "server": "server", "path": str(tmp_path / "two"),
             "docker_image": "relay:stable", "mode": "ro", "allow_exec": False},
        ],
    }


def thread(tmp_path):
    return PhysicalRelayThread(group(tmp_path), {
        "url": "https://pawflow.invalid", "username": "alice",
        "session_token": "session-test", "gateway_key": "gateway-test",
    })


def test_physical_registers_only_logical_services(tmp_path, monkeypatch):
    relay = thread(tmp_path)
    calls = []
    for member in relay.members:
        member._api = lambda method, path, body: calls.append(body)
        member._api_retry = member._api
    monkeypatch.setattr(relay, "_kill_docker", lambda: None)
    relay._restart_service_registration()
    installed = [call["service_name"] for call in calls if call["action"] == "service_install"]
    assert installed == ["One", "Two"]
    assert all("physical-owner" not in str(call) for call in calls)
    assert relay.ws_token == relay.members[0].ws_token
    relay.stop()
    assert all(not member._registered for member in relay.members)
    assert [call["service_id"] for call in calls[-2:]] == ["One", "Two"]


def test_group_health_requires_every_logical_connection(tmp_path):
    relay = thread(tmp_path)
    relay._api = lambda *args: {"relays": [{"relay_id": "One", "connected": True}]}
    assert relay._check_relay_connected() is False
    relay._api = lambda *args: {"relays": [
        {"relay_id": "One", "connected": True}, {"relay_id": "Two", "connected": True}]}
    assert relay._check_relay_connected() is True


@pytest.mark.parametrize("profile,expected", [
    (str(runtime.SECCOMP_PROFILE), str(runtime.SECCOMP_PROFILE)),
    ("C:/PawFlow/runtime/pawflow_relay/physical-seccomp.json",
     "/mnt/c/PawFlow/runtime/pawflow_relay/physical-seccomp.json"),
])
def test_group_command_has_one_container_and_private_mounts(tmp_path, monkeypatch, profile, expected):
    relay = thread(tmp_path)
    relay._host_helper_token = "helper-one"
    relay.members[0].ws_token = "token-one"
    relay.members[1].ws_token = "token-two"
    relay.members[1]._helper_port = 12345
    relay.members[1]._host_helper_token = "helper-two"
    monkeypatch.setattr("pawflow_relay.physical_thread.to_host_path", lambda path: path)
    monkeypatch.setattr("pawflow_relay.physical_thread.SECCOMP_PROFILE", profile)
    if profile.startswith("C:/"):
        monkeypatch.setattr("pawflow_relay.utils.os", SimpleNamespace(name="nt"))
    monkeypatch.setattr("pawflow_relay.physical_thread.get_host_ip", lambda: "192.0.2.1")
    command, config = relay._group_launch([
        "docker", "run", "--rm", "--name", "physical-container",
        "-v", f"{tmp_path / 'one'}:/workspace", "-v", "pawflow_home_One:/home/pawflow",
        "-v", "/runtime:/opt/pawflow:ro", "--env-file", "old-secret-file",
        "-e", "PAWFLOW_SESSION_TOKEN=not-in-container-env",
        "-e", "PAWFLOW_HOST_HELPER=host.docker.internal:12344",
        "-e", "HOME=/home/pawflow", "--publish", "2222:6080",
        "--security-opt", "apparmor=old-profile", "relay:stable",
        "python3", "-u", "/opt/pawflow/pawflow_relay_launcher.py",
        "--server", "wss://pawflow.invalid/ws/relay/One", "--token=token-one",
    ])
    assert command.count("run") == 1
    assert command.count("relay:stable") == 1
    assert "old-secret-file" not in command
    assert "--publish" not in command
    assert "not-in-container-env" not in " ".join(command)
    assert not any(value.endswith(":/workspace") for value in command)
    assert "apparmor=old-profile" not in command
    assert "seccomp=" + expected in command
    assert "seccomp=unconfined" not in command
    assert command[-5:] == ["--", "python3", "-I", "-u", runtime.SCRIPT]
    assert len(config["exports"]) == 2
    first, second = config["exports"]
    assert first["root_mount"] != second["root_mount"]
    assert first["home_mount"] != second["home_mount"]
    assert second["mode"] == "ro"
    for export in config["exports"]:
        assert export["command"][export["command"].index("--dir") + 1] == "/workspace"
        assert export["environment"]["PAWFLOW_SESSION_TOKEN"] == "session-test"
    assert "--allow-exec" in first["command"]
    assert "--allow-exec" not in second["command"]
    assert "--token=token-two" in second["command"]
    assert first["environment"]["PAWFLOW_HOST_HELPER_TOKEN"] == "helper-one"
    assert second["environment"]["PAWFLOW_HOST_HELPER_TOKEN"] == "helper-two"


def test_namespace_command_isolates_worker_and_loads_trusted_script():
    command = runtime.namespace_command(15)
    for option in ("--mount", "--pid", "--fork", "--kill-child=SIGKILL", "--ipc", "--uts", "--net", "-I"):
        assert option in command
    assert command[-3:] == [runtime.SCRIPT, "--worker-fd", "15"]


def test_empty_group_never_creates_processes(monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected launch"))
    with pytest.raises(ValueError, match="At least one"):
        runtime.supervise([], threading.Event())


def test_worker_creation_failure_cleans_already_owned_process(tmp_path, monkeypatch):
    events = []
    class Control:
        def settimeout(self, _value): pass
        def close(self): events.append("close")
        def fileno(self): return 99
        def sendall(self, _value): pass

    class Process:
        pid = 123
        def poll(self): return None
        def wait(self, timeout): events.append(("wait", timeout))

    calls = []
    def launch(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise OSError("injected launch failure")
        return Process()
    monkeypatch.setattr(runtime.socket, "socketpair", lambda *args: (Control(), Control()))
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: events.append(("kill", pid, sig)))
    with pytest.raises(OSError, match="injected"):
        runtime.supervise([{"relay_id": "One"}, {"relay_id": "Two"}], threading.Event())
    assert ("kill", 123, runtime.signal.SIGKILL) in events
    assert ("wait", 10) in events
    assert events.count("close") == 4


def test_manager_does_not_start_a_group_member_individually(tmp_path, monkeypatch):
    from pawflow_relay.physical_config import save_physical

    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(manager, "get_server", lambda name: {
        "name": name, "url": "https://pawflow.invalid", "username": "alice", "session_token": "session"})
    for directory in ("one", "two"):
        (tmp_path / directory).mkdir()
    physical = group(tmp_path)
    save_physical(physical["name"], "server", "relay:stable", physical["workspaces"])
    with pytest.raises(ValueError, match="Unknown physical relay"):
        manager.start_workspace("One")
    with pytest.raises(ValueError, match="Unknown physical relay"):
        manager.stop_workspace_runtime("Two")
    assert json.loads((tmp_path / "config/workspaces.json").read_text())["One"]["relay_id"] == "One"
