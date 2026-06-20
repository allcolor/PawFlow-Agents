"""Tests for pawflow_relay._relay_conn.connect_and_handshake.

Focus: the post-handshake leftover re-buffering. When the server coalesces
the WS upgrade 101 response with the first frame bytes into one TCP segment,
those extra bytes must be replayed to the next recv() calls intact — even
when the caller reads them in chunks smaller than the leftover.
"""
import socket

from pawflow_relay import _relay_conn


class _FakeSock:
    """Minimal socket stand-in for the handshake path.

    Returns the whole (101 response + leftover frame bytes) in the first
    recv, then EOF. setsockopt/sendall are no-ops.
    """

    def __init__(self, first_chunk):
        self._chunks = [first_chunk]
        self.sent = b""

    def setsockopt(self, *a, **k):
        pass

    def settimeout(self, *a, **k):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, n, _flags=0):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


def _handshake_response(leftover):
    return (
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n\r\n"
    ) + leftover


def _connect(monkeypatch, leftover):
    fake = _FakeSock(_handshake_response(leftover))
    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: fake)
    sock = _relay_conn.connect_and_handshake(
        "localhost", 80, "/ws/relay", False, "", "", "")
    return sock


def test_leftover_larger_than_recv_is_not_truncated(monkeypatch):
    leftover = bytes(range(256)) * 4  # 1024 bytes of frame data
    sock = _connect(monkeypatch, leftover)
    # Read the leftover in small chunks; every byte must come back in order.
    got = b""
    for _ in range(20):
        chunk = sock.recv(64)
        if not chunk:
            break
        got += chunk
        if len(got) >= len(leftover):
            break
    assert got == leftover


def test_leftover_smaller_than_recv_returned_whole(monkeypatch):
    leftover = b"\x81\x05hello"
    sock = _connect(monkeypatch, leftover)
    assert sock.recv(4096) == leftover
    # Next read falls through to the underlying socket (EOF here).
    assert sock.recv(4096) == b""


def test_no_leftover_uses_real_recv(monkeypatch):
    sock = _connect(monkeypatch, b"")
    # No leftover -> recv is not patched; underlying fake returns EOF.
    assert sock.recv(4096) == b""
