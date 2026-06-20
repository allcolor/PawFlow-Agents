"""Tests for pawflow_relay._relay_session (per-connection helpers)."""
import struct
import sys
from pathlib import Path

# tools/ on path for build_connection_params' lazy `from fs_actions import ...`.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay import _relay_session as rs


def test_close_frame_info_code_and_reason():
    payload = struct.pack("!H", 1000) + b"bye"
    assert rs.close_frame_info(payload) == "code=1000 reason='bye'"


def test_close_frame_info_empty():
    assert rs.close_frame_info(b"") == "code=none reason=''"


def test_close_frame_info_single_byte():
    out = rs.close_frame_info(b"x")
    assert out.startswith("code=none")


class _Swap:
    def __init__(self):
        self.inner = None
        self.cleared = False

    def set_inner(self, c):
        self.inner = c

    def clear_inner(self):
        self.cleared = True


class _FakeClient:
    def __init__(self, *a, **k):
        self.cancelled = None
        _FakeClient.last = self

    def cancel_all(self, reason):
        self.cancelled = reason


def test_attach_fuse_clients_binds_present_swaps(monkeypatch):
    made = []

    def _factory(*a, **k):
        c = _FakeClient()
        made.append(c)
        return c

    monkeypatch.setattr("pawflow_relay.server_fs_client.ServerFsClient", _factory)
    monkeypatch.setattr("pawflow_relay.ws_frame.ws_send", lambda s, b: None)

    s0, s2 = _Swap(), _Swap()
    swaps = (s0, None, s2)
    clients = rs.attach_fuse_clients(object(), object(), swaps)

    assert len(clients) == 3
    assert clients[1] is None
    assert clients[0] is s0.inner is made[0]
    assert clients[2] is s2.inner is made[1]


def test_detach_fuse_clients_clears_and_cancels():
    s0, s2 = _Swap(), _Swap()
    c0, c2 = _FakeClient(), _FakeClient()
    rs.detach_fuse_clients((s0, None, s2), (c0, None, c2), reason="gone")
    assert s0.cleared and s2.cleared
    assert c0.cancelled == "gone" and c2.cancelled == "gone"


def test_detach_tolerates_none_clients():
    s0 = _Swap()
    rs.detach_fuse_clients((s0,), None)
    assert s0.cleared


def test_build_connection_params_wss(monkeypatch):
    monkeypatch.setattr("fs_actions.detect_available_shells", lambda: {"bash": 1, "sh": 1})
    monkeypatch.delenv("PAWFLOW_HOST_WORKDIR", raising=False)
    monkeypatch.delenv("PAWFLOW_DOCKER_IMAGE", raising=False)
    cp = rs.build_connection_params(
        "wss://example.org:8443/ws/relay", "/root", False,
        True, False, False, True)
    assert (cp.host, cp.port, cp.path, cp.use_ssl) == ("example.org", 8443, "/ws/relay", True)
    assert cp.info["mode"] == "readwrite"
    assert cp.info["root"] == "/root"
    assert set(cp.info["shells"]) == {"bash", "sh"}
    assert cp.info["allow_exec"] is True and cp.info["allow_local"] is True


def test_build_connection_params_defaults_and_readonly(monkeypatch):
    monkeypatch.setattr("fs_actions.detect_available_shells", lambda: {})
    monkeypatch.delenv("PAWFLOW_HOST_WORKDIR", raising=False)
    monkeypatch.delenv("PAWFLOW_DOCKER_IMAGE", raising=False)
    # ws scheme (not ssl), no port -> default 80, empty path -> /ws/relay
    cp = rs.build_connection_params(
        "ws://host/", "/r", True, False, False, False, False)
    assert cp.use_ssl is False
    assert cp.port == 80
    assert cp.info["mode"] == "read"


def test_build_connection_params_host_root_from_env(monkeypatch):
    monkeypatch.setattr("fs_actions.detect_available_shells", lambda: {})
    monkeypatch.setenv("PAWFLOW_HOST_WORKDIR", r"C:\Users\me\proj")
    cp = rs.build_connection_params(
        "wss://h/ws/relay", "/root", False, False, False, False, False)
    # backslashes normalised to forward slashes for JSON display
    assert cp.info["host_root"] == "C:/Users/me/proj"
