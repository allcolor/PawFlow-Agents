"""Tests for pawflow_relay._relay_session (per-connection helpers)."""
import struct

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
