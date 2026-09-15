"""Host capability checks and physical group helper lifecycle regressions."""

import base64
import io
import json
import os
import socket
import subprocess
from types import SimpleNamespace

import pytest

from pawflow_relay import _thread_docker as docker
from pawflow_relay.auth import probe_host_helper
from pawflow_relay.physical_thread import PhysicalRelayThread
from pawflow_relay.thread import RelayThread


def make_relay(tmp_path, **permissions):
    return PhysicalRelayThread({
        "name": "Group", "physical_id": "group-owner",
        "workspaces": [
            {"name": name, "relay_id": name, "server": "server",
             "path": str(tmp_path / name), "docker_image": "relay:test",
             "allow_local": False, "allow_exec": False,
             "allow_remote_desktop": False, "allow_service_tunnels": False,
             **permissions}
            for name in ("one", "two")
        ],
    }, {"url": "https://pawflow.invalid", "session_token": "session", "username": "test"})


class Connection:
    def __init__(self, helper, action, **request):
        self.request = json.dumps({
            "action": action, "_host_helper_token": helper._host_helper_token,
            **request,
        }).encode() + b"\n"
        self.output = b""

    def recv(self, size):
        result, self.request = self.request, b""
        return result

    def sendall(self, data):
        self.output += data

    def result(self):
        return json.loads(self.output.splitlines()[-1])


HOST_ACTIONS = [
    "open_local_terminal", "write_terminal", "resize_terminal", "close_terminal",
    "start_local_code_server", "exec", "exec_stream", "read_file", "write_file", "http_fetch",
    "claude_auth_login", "codex_auth_login", "gemini_auth_login",
]
DESKTOP_ACTIONS = [
    "local_desktop_connect", "start_local_desktop", "stop_local_desktop",
    "local_screen_check", "screen_screenshot", "screen_click",
]


@pytest.mark.parametrize("action", HOST_ACTIONS + DESKTOP_ACTIONS + ["service_tunnel_apply"])
@pytest.mark.parametrize("helper_index", [0, 1])
def test_disabled_worker_cannot_dispatch_host_actions(tmp_path, monkeypatch, action, helper_index):
    relay = make_relay(tmp_path)
    helper = [relay, relay.members[1]][helper_index]
    helper._host_helper_token = "worker-capability"
    for name in ("_host_terminal_persistent", "_host_start_local_code_server",
                 "_handle_host_screen_action", "_host_desktop_connect"):
        monkeypatch.setattr(helper, name, lambda *args: pytest.fail("unauthorized dispatch"))
    conn = Connection(helper, action)
    helper._handle_host_helper_conn(conn)
    assert conn.result()["type"] == "error"
    assert "requires" in conn.result()["error"]


@pytest.mark.parametrize("permissions", [
    {"allow_exec": True}, {"allow_local": True},
    {"allow_remote_desktop": True}, {"allow_service_tunnels": True},
])
def test_host_shell_requires_both_local_and_exec(tmp_path, monkeypatch, permissions):
    relay = make_relay(tmp_path, **permissions)
    relay._host_helper_token = "capability"
    monkeypatch.setattr(relay, "_host_terminal_persistent",
                        lambda *args: pytest.fail("unauthorized shell"))
    conn = Connection(relay, "open_local_terminal")
    relay._handle_host_helper_conn(conn)
    assert conn.result()["type"] == "error"


def test_explicit_host_shell_grant_dispatches(tmp_path, monkeypatch):
    relay = make_relay(tmp_path, allow_local=True, allow_exec=True)
    relay._host_helper_token = "capability"
    calls = []
    monkeypatch.setattr(relay, "_host_terminal_persistent", lambda *args: calls.append(args))
    conn = Connection(relay, "open_local_terminal")
    assert relay._handle_host_helper_conn(conn) is False
    assert len(calls) == 1


@pytest.mark.parametrize("allow_exec", [False, True])
@pytest.mark.parametrize("helper_index", [0, 1])
def test_explicit_host_grant_can_access_absolute_host_path(tmp_path, allow_exec, helper_index):
    relay = make_relay(tmp_path, allow_local=True, allow_exec=allow_exec)
    helper = [relay, relay.members[1]][helper_index]
    helper._host_helper_token = "capability"
    outside = tmp_path / "host-file"
    outside.write_text("authorized host file")
    conn = Connection(helper, "read_file", path=str(outside))
    helper._handle_host_helper_conn(conn)
    assert conn.result()["type"] == "result"
    assert base64.b64decode(conn.result()["data"]["content"]) == b"authorized host file"


@pytest.mark.parametrize("action", ["exec", "exec_stream"])
@pytest.mark.parametrize("allow_exec", [False, True])
def test_local_command_dispatch_requires_and_forwards_exec_grant(tmp_path, monkeypatch, action, allow_exec):
    from fs_actions import ACTIONS

    relay = make_relay(tmp_path, allow_local=True, allow_exec=allow_exec)
    relay._host_helper_token = "capability"
    calls = []

    def execute(root, path, request, *, allow_exec=False):
        calls.append((root, path, allow_exec))
        return {"ok": allow_exec}

    monkeypatch.setitem(ACTIONS, action, execute)
    conn = Connection(relay, action)
    relay._handle_host_helper_conn(conn)
    if allow_exec:
        assert conn.result() == {"type": "result", "data": {"ok": True}}
        assert calls == [(relay.directory, relay.directory, True)]
    else:
        assert conn.result()["type"] == "error"
        assert calls == []


@pytest.mark.parametrize("action", DESKTOP_ACTIONS)
def test_desktop_grant_does_not_require_local_shell_grant(tmp_path, monkeypatch, action):
    relay = make_relay(tmp_path, allow_remote_desktop=True)
    relay._host_helper_token = "capability"
    calls = []
    monkeypatch.setattr(relay, "_handle_host_screen_action", lambda *args: calls.append(args))
    monkeypatch.setattr(relay, "_host_desktop_connect", lambda *args: calls.append(args))
    relay._handle_host_helper_conn(Connection(relay, action))
    assert len(calls) == 1


def test_tunnel_grant_does_not_require_local_shell_grant(tmp_path, monkeypatch):
    from pawflow_relay import service_tunnels

    relay = make_relay(tmp_path, allow_service_tunnels=True)
    relay._host_helper_token = "capability"
    monkeypatch.setattr(service_tunnels, "handle_action", lambda *args: {"allowed": True})
    conn = Connection(relay, "service_tunnel_status")
    relay._handle_host_helper_conn(conn)
    assert conn.result() == {"type": "result", "data": {"allowed": True}}


class HelperThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args
        self.alive = False

    def start(self):
        self.alive = True
        self.target(*self.args)

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.alive = False


class Bridge:
    stdin = None

    def __init__(self):
        self.dead = False

    def poll(self):
        return 1 if self.dead else None

    def wait(self, timeout):
        self.dead = True


def retry_harness(tmp_path, monkeypatch):
    relay = make_relay(tmp_path)
    relay.ws_token = "websocket-token"
    relay._log = lambda message: None
    relay._log_out = lambda: io.StringIO()
    events, helpers, processes = [], [], []
    monkeypatch.setattr(docker.threading, "Thread", HelperThread)
    monkeypatch.setattr(docker, "get_host_ip", lambda: "192.0.2.1")
    monkeypatch.setattr(docker, "translate_path", lambda path: path)
    monkeypatch.setattr(docker, "to_host_path", lambda path: path)
    monkeypatch.setattr(docker, "_relay_apparmor_security_opts", lambda image: [])
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "192.0.2.1")
    monkeypatch.setattr(docker, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(RelayThread, "_kill_docker",
                        lambda self: events.append("container-cleanup"))
    for helper in [relay, relay.members[1]]:
        def start(port, helper=helper):
            helpers.append(helper._host_helper_thread)
            events.append(("helper-start", helper.relay_id))
            helper._host_helper_ready.set()
        monkeypatch.setattr(helper, "_run_host_helper", start)

    class Process:
        stdout = ()
        stderr = None

        def __init__(self):
            self.dead = False

        def wait(self, timeout):
            relay._stop_event.set()
            raise subprocess.TimeoutExpired("docker", timeout)

        def poll(self):
            return 1 if self.dead else None

        def kill(self):
            self.dead = True
            events.append("process-kill")

    def spawn(command):
        events.append("spawn")
        proc = Process()
        processes.append(proc)
        return proc

    monkeypatch.setattr(relay, "_spawn_docker_process", spawn)
    delays = []
    def backoff(delay):
        delays.append(delay)
        assert len(delays) < 10, "unexpected repeated launch failure"
    monkeypatch.setattr(relay._stop_event, "wait", backoff)
    return relay, events, helpers, processes, delays


@pytest.mark.parametrize("index", [0, 1])
def test_helper_startup_failure_retries_and_cleans_before_next_attempt(tmp_path, monkeypatch, index):
    relay, events, helpers, processes, delays = retry_harness(tmp_path, monkeypatch)
    helper = [relay, relay.members[1]][index]
    original = helper._run_host_helper
    starts = []
    failed_threads = []

    def start(port):
        if not starts:
            starts.append("failed")
            failed_threads.extend([*helpers, helper._host_helper_thread])
            helper._host_helper_error = OSError("injected bind failure")
            helper._host_helper_ready.set()
        else:
            assert all(not thread.is_alive() for thread in failed_threads)
            original(port)

    monkeypatch.setattr(helper, "_run_host_helper", start)
    relay._run_docker_relay("/workspace/tools")
    assert delays == [1]
    assert len(processes) == 1
    assert all(not thread.is_alive() for thread in helpers)
    assert events.index("container-cleanup") < events.index("spawn")
    assert relay._stop_event.is_set()


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failure", ["helper", "bridge"])
def test_later_helper_death_kills_and_retries_whole_group(tmp_path, monkeypatch, index, failure):
    relay, _events, helpers, processes, delays = retry_harness(tmp_path, monkeypatch)
    original = relay._spawn_docker_process
    bridges = []

    def spawn(command):
        if processes:
            assert processes[0].dead
            assert all(not thread.is_alive() for thread in helpers[:-2])
            assert all(bridge.dead for bridge in bridges)
        proc = original(command)
        if len(processes) == 1:
            helper = [relay, relay.members[1]][index]
            bridge = Bridge()
            bridges.append(bridge)
            helper._host_bridge_proc = bridge

            def fail(timeout):
                if failure == "helper":
                    helper._host_helper_thread.alive = False
                else:
                    bridge.dead = True
                raise subprocess.TimeoutExpired("docker", timeout)

            proc.wait = fail
        return proc

    monkeypatch.setattr(relay, "_spawn_docker_process", spawn)
    relay._run_docker_relay("/workspace/tools")
    assert len(processes) == 2
    assert delays == [1]
    assert all(proc.dead for proc in processes)
    assert all(not thread.is_alive() for thread in helpers)
    assert all(helper._host_bridge_proc is None for helper in [relay, relay.members[1]])


def test_primary_wsl_bridge_startup_failure_is_retried(tmp_path, monkeypatch):
    relay, _events, _helpers, processes, delays = retry_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(docker, "os", SimpleNamespace(
        name="nt", path=os.path, environ=os.environ, fdopen=os.fdopen,
        chmod=os.chmod, unlink=os.unlink, close=os.close))
    bridges = []

    def start(*args):
        bridge = Bridge()
        relay._host_bridge_proc = bridge
        bridges.append(bridge)
        if len(bridges) == 1:
            raise RuntimeError("injected bridge failure")
        assert bridges[0].dead

    monkeypatch.setattr(relay, "_start_windows_host_bridge", start)
    relay._run_docker_relay("/workspace/tools")
    assert delays == [1]
    assert len(processes) == 1
    assert all(bridge.dead for bridge in bridges)


def test_backoff_is_bounded_and_stop_during_backoff_stays_stopped(tmp_path, monkeypatch):
    relay, _events, _helpers, processes, delays = retry_harness(tmp_path, monkeypatch)

    def fail(port):
        relay._host_helper_error = OSError("persistent failure")
        relay._host_helper_ready.set()

    def backoff(delay):
        delays.append(delay)
        if len(delays) == 8:
            relay.stop()

    monkeypatch.setattr(relay, "_run_host_helper", fail)
    monkeypatch.setattr(relay._stop_event, "wait", backoff)
    monkeypatch.setattr(docker.time, "time", lambda: 1)
    relay._run_docker_relay("/workspace/tools")
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60]
    assert processes == []
    assert relay._stop_event.is_set()


def test_stopped_group_never_starts_helpers_or_container(tmp_path, monkeypatch):
    relay, _events, helpers, processes, delays = retry_harness(tmp_path, monkeypatch)
    relay.stop()
    relay._run_docker_relay("/workspace/tools")
    assert helpers == processes == delays == []


def test_thread_creation_failure_retries_without_joining_unstarted_thread(tmp_path, monkeypatch):
    relay, _events, _helpers, processes, delays = retry_harness(tmp_path, monkeypatch)
    attempts = []

    class FailingThread(HelperThread):
        def start(self):
            attempts.append(self)
            if len(attempts) == 1:
                raise RuntimeError("cannot start new thread")
            super().start()

        def join(self, timeout):
            assert self.alive, "cannot join an unstarted thread"
            super().join(timeout)

    monkeypatch.setattr(docker.threading, "Thread", FailingThread)
    relay._run_docker_relay("/workspace/tools")
    assert delays == [1]
    assert len(processes) == 1


def test_singleton_keeps_healthy_helper_between_container_attempts(tmp_path, monkeypatch):
    relay = RelayThread("https://pawflow.invalid", "session", "test", str(tmp_path))
    calls = []
    monkeypatch.setattr(docker.threading, "Thread", HelperThread)

    def start(port):
        calls.append(port)
        relay._host_helper_ready.set()

    monkeypatch.setattr(relay, "_run_host_helper", start)
    relay._prepare_docker_helpers(12345)
    relay._cleanup_docker_helpers()
    relay._prepare_docker_helpers(12345)
    assert calls == [12345]
    assert relay._host_helper_thread.is_alive()


def test_bridge_cleanup_reaps_process_after_forced_kill(tmp_path):
    relay = make_relay(tmp_path)
    events = []

    class HungBridge:
        stdin = None

        def wait(self, timeout):
            events.append(("wait", timeout))
            if "kill" not in events:
                raise subprocess.TimeoutExpired("bridge", timeout)

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

    relay._host_bridge_proc = HungBridge()
    relay._stop_windows_host_bridge()
    assert events == [("wait", 3), "terminate", ("wait", 2), "kill", ("wait", 2)]
    assert relay._host_bridge_proc is None


def test_real_listeners_and_pending_connections_close_between_attempts(tmp_path, monkeypatch):
    relay = make_relay(tmp_path)
    relay._host_helper_token = "real-test-token"
    monkeypatch.setattr(RelayThread, "_kill_docker", lambda self: None)
    from pawflow_relay.utils import find_free_port

    port = find_free_port()
    pending = None
    try:
        relay._prepare_docker_helpers(port)
        threads = [relay._host_helper_thread, relay.members[1]._host_helper_thread]
        assert probe_host_helper(f"127.0.0.1:{port}", "real-test-token", timeout=1)
        pending = socket.create_connection(("127.0.0.1", port), timeout=1)
        pending.sendall(b'{"action":')
        relay._cleanup_docker_helpers()
        assert all(not thread.is_alive() for thread in threads)
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)
        assert pending.recv(1) == b""
        assert not relay._stop_event.is_set()
        relay._prepare_docker_helpers(port)
        assert probe_host_helper(f"127.0.0.1:{port}", "real-test-token", timeout=1)
    finally:
        relay.stop()
        if pending is not None:
            pending.close()
