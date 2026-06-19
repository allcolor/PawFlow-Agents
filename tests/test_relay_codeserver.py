"""Behavioral tests for the relay code-server manager (_relay_codeserver).

cs_ws_open/send/close are exercised against a real localhost WS backend
(socket server that completes the 101 handshake and exchanges frames),
giving the WS-tunnel proxy genuine runtime coverage. start_code_server
is tested with subprocess + http mocked (the code-server binary is not
available in CI) to lock the process-arg invariants.

The manager functions take a duck-typed state object (the worker passes
its RelayWorkerState); tests use a SimpleNamespace so they need none of
worker.py's relay-only imports.
"""
import base64
import http.client
import json
import socket
import threading
import time
import types

import pytest

from pawflow_relay import _relay_codeserver as cs


def _state():
    return types.SimpleNamespace(
        code_server_proc=None, code_server_port=None,
        code_server_base_path="", cs_ws_sessions={})


def _wait(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _Backend:
    """Minimal code-server-like WS backend on localhost."""

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

    def send_text_frame(self, payload: bytes):
        # server->client text frame, unmasked, short payload (<126)
        self._conn.sendall(bytes([0x81, len(payload)]) + payload)

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


def test_cs_ws_open_streams_backend_frames(backend):
    st = _state()
    frames = []
    lock = threading.Lock()

    def send_frame(fb):
        with lock:
            frames.append(json.loads(fb.decode("utf-8")))

    res = cs.cs_ws_open(st, {"session_id": "s1", "port": backend.port, "ws_path": "/"}, send_frame)
    assert res == {"ok": True}
    assert "s1" in st.cs_ws_sessions
    backend.wait_connected()
    # Browser headers must not be forwarded to the backend handshake.
    assert b"Origin:" not in backend.handshake_request
    assert b"Cookie:" not in backend.handshake_request

    backend.send_text_frame(b"hello-from-backend")

    def _got():
        with lock:
            return any(f.get("type") == "cs_ws_data"
                       and base64.b64decode(f["data"]) == b"hello-from-backend"
                       for f in frames)
    import base64
    assert _wait(_got), "backend frame was not forwarded via send_frame"


def test_cs_ws_send_writes_masked_frame_to_backend(backend):
    st = _state()
    res = cs.cs_ws_open(st, {"session_id": "s1", "port": backend.port, "ws_path": "/"}, lambda _f: None)
    assert res == {"ok": True}
    backend.wait_connected()

    import base64
    ok = cs.cs_ws_send(st, {"session_id": "s1", "data": base64.b64encode(b"ping").decode(), "opcode": 1})
    assert ok == {"ok": True}
    # zero-masked client frame -> payload bytes are unchanged on the wire
    assert _wait(lambda: b"ping" in bytes(backend.received))


def test_cs_ws_send_unknown_session_errors():
    st = _state()
    res = cs.cs_ws_send(st, {"session_id": "nope", "data": ""})
    assert res["ok"] is False
    assert "not found" in res["error"]


def test_cs_ws_close_removes_session(backend):
    st = _state()
    cs.cs_ws_open(st, {"session_id": "s1", "port": backend.port, "ws_path": "/"}, lambda _f: None)
    backend.wait_connected()
    assert cs.cs_ws_close(st, {"session_id": "s1"}) == {"ok": True}
    assert "s1" not in st.cs_ws_sessions


def test_cs_ws_close_unknown_is_ok():
    assert cs.cs_ws_close(_state(), {"session_id": "nope"}) == {"ok": True}


def test_start_code_server_never_passes_public_base_path_to_process(monkeypatch, tmp_path):
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            pass

    def fake_popen(args, **kwargs):
        captured["args"] = list(args)
        captured["env"] = kwargs.get("env")
        return FakeProc()

    class FakeResp:
        status = 200

        def read(self, _n):
            return b""

    class FakeConn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(cs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(http.client, "HTTPConnection", FakeConn)

    st = _state()
    res = cs.start_code_server(st, {"base_path": "/proxy/abc/", "port": 0}, str(tmp_path))
    assert res["ok"] is True
    assert st.code_server_proc is not None and st.code_server_base_path == "/proxy/abc/"

    args = captured["args"]
    # The public base path is NEVER a positional arg; only the stripped
    # --abs-proxy-base-path carries it.
    assert "/proxy/abc/" not in args
    assert args[args.index("--abs-proxy-base-path") + 1] == "/proxy/abc"
    # Isolated profile + updates disabled.
    assert "--user-data-dir" in args and "--extensions-dir" in args
    assert "--disable-telemetry" in args
    assert captured["env"]["EXTENSIONS_GALLERY"] == "{}"


def test_stop_code_server_when_not_running():
    assert cs.stop_code_server(_state()) == {"ok": True, "data": {"was_running": False}}
