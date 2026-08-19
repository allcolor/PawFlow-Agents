import inspect
import socket
import threading
from types import SimpleNamespace

import pytest

from pawflow_relay import host_bridge
from pawflow_relay._thread_host import _RelayHostHelperMixin
from pawflow_relay.auth import probe_host_helper
from pawflow_relay.host_bridge import serve
from pawflow_relay.thread import RelayThread
from pawflow_relay.utils import find_free_port


class _HostHelper(_RelayHostHelperMixin):
    def __init__(self, token="test-token"):
        self._host_helper_token = token
        self._host_helper_error = None
        self._host_helper_ready = threading.Event()
        self._stop_event = threading.Event()
        self.logs = []

    def _log(self, message):
        self.logs.append(message)


def _start_helper(helper):
    port = find_free_port()
    thread = threading.Thread(
        target=helper._run_host_helper, args=(port,), daemon=True)
    thread.start()
    assert helper._host_helper_ready.wait(timeout=2)
    assert helper._host_helper_error is None
    return port, thread


def test_authenticated_host_helper_probe_round_trip():
    helper = _HostHelper()
    port, thread = _start_helper(helper)
    try:
        assert probe_host_helper(
            f"127.0.0.1:{port}", "test-token", timeout=1) is True
    finally:
        helper._stop_event.set()
        thread.join(timeout=3)


def test_host_helper_probe_rejects_wrong_capability_token():
    helper = _HostHelper()
    port, thread = _start_helper(helper)
    try:
        with pytest.raises(RuntimeError, match="Invalid host helper capability"):
            probe_host_helper(f"127.0.0.1:{port}", "wrong-token", timeout=1)
    finally:
        helper._stop_event.set()
        thread.join(timeout=3)


def test_host_helper_bind_failure_is_reported(monkeypatch):
    helper = _HostHelper()

    class _BrokenSocket:
        def setsockopt(self, *args):
            pass

        def bind(self, address):
            raise OSError("bind failed")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: _BrokenSocket())
    helper._run_host_helper(48123)

    assert helper._host_helper_ready.is_set()
    assert isinstance(helper._host_helper_error, OSError)
    assert any("failed to listen" in line for line in helper.logs)


def test_windows_launcher_uses_tracked_wsl_bridge():
    start_source = inspect.getsource(
        RelayThread._start_windows_host_bridge)
    run_source = inspect.getsource(RelayThread._run_docker_relay)

    assert '"wsl", "env"' in start_source
    assert '"PYTHONPATH=' in start_source
    assert '"PAWFLOW_HOST_HELPER_TOKEN"' in start_source
    assert '"--exit-on-stdin-eof"' in start_source
    assert '"--network", "host"' not in run_source
    assert "PAWFLOW_HOST_HELPER=host.docker.internal:" in run_source


def test_windows_bridge_stop_closes_stdin_before_termination():
    events = []

    class _Stdin:
        def close(self):
            events.append("stdin-close")

    class _Process:
        stdin = _Stdin()

        def wait(self, timeout):
            events.append(("wait", timeout))

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

    relay = object.__new__(RelayThread)
    relay._host_bridge_proc = _Process()

    relay._stop_windows_host_bridge()

    assert events == ["stdin-close", ("wait", 3)]
    assert relay._host_bridge_proc is None


def test_windows_bridge_token_is_forwarded_outside_command_line():
    captured = {}

    class _Stdin:
        def close(self):
            pass

    class _Process:
        def __init__(self):
            self.stdin = _Stdin()
            self.stdout = [b"[HostBridge] listening on 48123\n"]

        def wait(self, timeout):
            pass

        def poll(self):
            return None

    def _popen(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return _Process()

    subprocess_module = SimpleNamespace(
        PIPE=object(), STDOUT=object(), Popen=_popen)
    relay = object.__new__(RelayThread)
    relay._host_bridge_proc = None
    relay._host_helper_token = "secret-capability"
    relay._log = lambda _message: None

    relay._start_windows_host_bridge(
        "/workspace", 48123, subprocess_module)
    relay._stop_windows_host_bridge()

    assert "secret-capability" not in " ".join(captured["command"])
    assert captured["environment"]["PAWFLOW_HOST_HELPER_TOKEN"] == (
        "secret-capability")
    assert "PAWFLOW_HOST_HELPER_TOKEN/w" in (
        captured["environment"]["WSLENV"].split(":"))


def test_wsl_bridge_forwards_authenticated_helper_ping():
    helper = _HostHelper()
    target_port, helper_thread = _start_helper(helper)
    listen_port = find_free_port()
    stop_event = threading.Event()
    ready_event = threading.Event()
    bridge_thread = threading.Thread(
        target=serve,
        args=(listen_port, target_port, "test-token", ""),
        kwargs={"stop_event": stop_event, "ready_event": ready_event},
        daemon=True,
    )
    bridge_thread.start()
    assert ready_event.wait(timeout=2)
    try:
        assert probe_host_helper(
            f"127.0.0.1:{listen_port}", "test-token", timeout=1) is True
    finally:
        stop_event.set()
        bridge_thread.join(timeout=2)
        helper._stop_event.set()
        helper_thread.join(timeout=3)


def test_wsl_bridge_resolves_the_windows_route_for_each_connection(monkeypatch):
    helper = _HostHelper()
    target_port, helper_thread = _start_helper(helper)
    listen_port = find_free_port()
    stop_event = threading.Event()
    ready_event = threading.Event()
    selected = []

    def _select_target(port, token, extra_target="", timeout=2):
        selected.append((port, token, extra_target))
        return "127.0.0.1"

    monkeypatch.setattr(host_bridge, "select_target", _select_target)
    bridge_thread = threading.Thread(
        target=host_bridge.serve,
        args=(listen_port, target_port, "test-token", ""),
        kwargs={"stop_event": stop_event, "ready_event": ready_event},
        daemon=True,
    )
    bridge_thread.start()
    assert ready_event.wait(timeout=2)
    try:
        endpoint = f"127.0.0.1:{listen_port}"
        assert probe_host_helper(endpoint, "test-token", timeout=1) is True
        assert probe_host_helper(endpoint, "test-token", timeout=1) is True
        assert selected == [
            (target_port, "test-token", ""),
            (target_port, "test-token", ""),
        ]
    finally:
        stop_event.set()
        bridge_thread.join(timeout=2)
        helper._stop_event.set()
        helper_thread.join(timeout=3)


def test_worker_keeps_connection_during_transient_host_helper_loss():
    source = inspect.getsource(__import__(
        "pawflow_relay.worker", fromlist=["_ws_connect"])._ws_connect)
    health_block = source.split(
        "Host helper health check failed:", 1)[1].split(
        "idle = time.time()", 1)[0]

    assert "keeping relay connection active" in health_block
    assert "sock.close()" not in health_block
