"""Behavioral tests for the relay desktop (VNC) WebSocket tunnel.

Exercises desktop_ws_open/send/close against a real localhost WS backend,
covering the behaviours that differ from the code-server tunnel: browser
headers ARE forwarded, pings are answered locally with pongs, and frames
carry their real opcode. Duck-typed state (SimpleNamespace) keeps the
test free of worker.py's relay-only imports.
"""
import base64
import json
import socket
import threading
import time
import types

import pytest

from pawflow_relay import _relay_desktop as dt


def _state():
    return types.SimpleNamespace(desktop_ws_sessions={})


def _wait(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _Backend:
    """Minimal VNC-like WS backend on localhost."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self.received = bytearray()
        self.handshake_request = b""
        self._conn = None
        self._ready = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        self._conn = conn
        req = b""
        while b"\r\n\r\n" not in req:
            c = conn.recv(4096)
            if not c:
                return
            req += c
        self.handshake_request = req
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                     b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        self._ready.set()
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                self.received += data
        except OSError:
            pass

    def wait_connected(self):
        assert self._ready.wait(5.0), "backend never received handshake"

    def send_frame(self, opcode: int, payload: bytes):
        # server->client, unmasked, short payload (<126)
        self._conn.sendall(bytes([0x80 | opcode, len(payload)]) + payload)

    def close(self):
        for s in (self._conn, self._srv):
            try:
                if s:
                    s.close()
            except OSError:
                pass

@pytest.fixture
def backend():
    b = _Backend()
    yield b
    b.close()


def test_desktop_ws_open_forwards_browser_headers(backend):
    st = _state()
    res = dt.desktop_ws_open(
        st,
        {"session_id": "d1", "port": backend.port, "ws_path": "/",
         "headers": {"Cookie": "sess=abc", "X-Custom": "yes"}},
        lambda _f: None,
    )
    assert res == {"ok": True}
    backend.wait_connected()
    # Unlike code-server, the desktop tunnel forwards browser headers.
    assert b"Cookie: sess=abc" in backend.handshake_request
    assert b"X-Custom: yes" in backend.handshake_request


def test_desktop_ws_open_streams_data_with_opcode(backend):
    st = _state()
    frames = []
    lock = threading.Lock()

    def send_frame(fb):
        with lock:
            frames.append(json.loads(fb.decode("utf-8")))

    assert dt.desktop_ws_open(
        st, {"session_id": "d1", "port": backend.port, "ws_path": "/"}, send_frame
    ) == {"ok": True}
    backend.wait_connected()
    backend.send_frame(0x02, b"\x01\x02\x03vncframe")  # binary

    def _got():
        with lock:
            return any(f.get("type") == "desktop_ws_data" and f.get("opcode") == 2
                       and base64.b64decode(f["data"]) == b"\x01\x02\x03vncframe"
                       for f in frames)
    assert _wait(_got), "backend binary frame not forwarded with its opcode"


def test_desktop_ws_reader_answers_ping_locally(backend):
    st = _state()
    forwarded = []
    lock = threading.Lock()

    def send_frame(fb):
        with lock:
            forwarded.append(json.loads(fb.decode("utf-8")))

    dt.desktop_ws_open(st, {"session_id": "d1", "port": backend.port, "ws_path": "/"}, send_frame)
    backend.wait_connected()
    backend.send_frame(0x09, b"pingpayload")  # ping

    # The reader answers the ping with a pong frame to the backend...
    def _pong_received():
        data = bytes(backend.received)
        return len(data) >= 2 and (data[0] & 0x0F) == 0x0A and b"pingpayload" in data
    assert _wait(_pong_received), "ping was not answered with a pong"
    # ...and does NOT forward the ping as desktop_ws_data.
    with lock:
        assert not any(f.get("type") == "desktop_ws_data" for f in forwarded)


def test_desktop_ws_send_writes_frame_to_backend(backend):
    st = _state()
    dt.desktop_ws_open(st, {"session_id": "d1", "port": backend.port, "ws_path": "/"}, lambda _f: None)
    backend.wait_connected()
    res = dt.desktop_ws_send(st, {"session_id": "d1", "data": base64.b64encode(b"clickdata").decode()})
    assert res == {"ok": True}
    assert _wait(lambda: b"clickdata" in bytes(backend.received))


def test_desktop_ws_send_unknown_session_errors():
    res = dt.desktop_ws_send(_state(), {"session_id": "nope", "data": ""})
    assert res["ok"] is False and "not found" in res["error"]


def test_desktop_ws_close_removes_session(backend):
    st = _state()
    dt.desktop_ws_open(st, {"session_id": "d1", "port": backend.port, "ws_path": "/"}, lambda _f: None)
    backend.wait_connected()
    assert dt.desktop_ws_close(st, {"session_id": "d1"}) == {"ok": True}
    assert "d1" not in st.desktop_ws_sessions
    assert dt.desktop_ws_close(st, {"session_id": "nope"}) == {"ok": True}
