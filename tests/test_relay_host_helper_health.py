import inspect
import socket
import threading

import pytest

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


def test_windows_launcher_preserves_docker_desktop_host_alias():
    source = inspect.getsource(RelayThread._run_docker_relay)
    assert '"--network", "host"' in source
    assert '"pawflow_relay.host_bridge"' in source
    assert "PAWFLOW_HOST_HELPER=host.docker.internal:" in source


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
