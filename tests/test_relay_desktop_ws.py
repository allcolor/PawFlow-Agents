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
    return types.SimpleNamespace(
        desktop_ws_sessions={}, desktop_audio_sessions={})


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
                     b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
                     + getattr(self, "initial_frame", b""))
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


@pytest.fixture
def host_helper(backend, monkeypatch):
    from pawflow_relay._thread_host import _RelayHostHelperMixin

    helper = _RelayHostHelperMixin()
    helper._host_helper_token = "desktop-test-capability"
    helper._host_desktop_lifecycle_lock = threading.RLock()
    helper._local_desktop_procs = [types.SimpleNamespace(poll=lambda: None)]
    helper._local_desktop_novnc_port = backend.port
    helper.allow_remote_desktop = True
    helper._stop_event = threading.Event()
    helper._log = lambda _message: None
    original_connect = socket.create_connection
    addresses = []
    connections = []
    threads = []
    relay_thread = threading.get_ident()

    def connect(address, timeout):
        if threading.get_ident() != relay_thread:
            return original_connect(address, timeout=timeout)
        addresses.append(address)
        # Only the helper's WSL bridge port is reachable from Docker.
        if address != ("host.docker.internal", 48123):
            raise ConnectionRefusedError("Windows desktop port is not on WSL")
        client, server = socket.socketpair()
        client.settimeout(timeout)
        connections.extend((client, server))
        thread = threading.Thread(
            target=helper._handle_host_helper_conn_safe,
            args=(server,), daemon=True)
        threads.append(thread)
        thread.start()
        return client

    monkeypatch.setenv("PAWFLOW_HOST_HELPER", "host.docker.internal:48123")
    monkeypatch.setenv("PAWFLOW_HOST_HELPER_TOKEN", helper._host_helper_token)
    monkeypatch.setattr(socket, "create_connection", connect)
    yield helper, addresses
    helper._stop_event.set()
    for connection in connections:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive(), "host desktop tunnel did not close"


def test_desktop_ws_open_reaches_host_screen_via_host_helper(
        backend, host_helper):
    _, addresses = host_helper
    state = _state()
    frames = []
    greeting = b"RFB 003.008\n"
    backend.initial_frame = bytes([0x82, len(greeting)]) + greeting
    try:
        assert dt.desktop_ws_open(
            state,
            {"session_id": "d-host", "port": backend.port,
             "ws_path": "/websockify", "local_screen": True},
            lambda frame: frames.append(json.loads(frame)),
        ) == {"ok": True}
        backend.wait_connected()
        assert addresses == [("host.docker.internal", 48123)]
        assert f"Host: 127.0.0.1:{backend.port}".encode() in backend.handshake_request
        assert state.desktop_ws_sessions["d-host"]["sock"].gettimeout() is None
        assert _wait(lambda: any(
            frame.get("type") == "desktop_ws_data"
            and base64.b64decode(frame["data"]) == b"RFB 003.008\n"
            for frame in frames))
        assert dt.desktop_ws_send(state, {
            "session_id": "d-host",
            "data": base64.b64encode(b"client-vnc-data").decode(),
        }) == {"ok": True}
        assert _wait(lambda: b"client-vnc-data" in bytes(backend.received))
    finally:
        reader = state.desktop_ws_sessions.get("d-host", {}).get("reader")
        dt.desktop_ws_close(state, {"session_id": "d-host"})
        if reader:
            reader.join(timeout=2)
            assert not reader.is_alive()


@pytest.mark.parametrize("failure, error", [
    ("wrong_token", "Invalid host helper capability"),
    ("missing_token", "Host helper token is missing"),
    ("wrong_port", "Desktop port does not match the running host desktop"),
    ("stopped", "Host desktop is not running"),
    ("disabled", "Remote desktop is disabled"),
])
def test_host_desktop_tunnel_rejects_invalid_connection(
        backend, host_helper, monkeypatch, failure, error):
    helper, _ = host_helper
    port = backend.port
    if failure == "wrong_token":
        monkeypatch.setenv("PAWFLOW_HOST_HELPER_TOKEN", "wrong")
    elif failure == "missing_token":
        monkeypatch.delenv("PAWFLOW_HOST_HELPER_TOKEN")
    elif failure == "wrong_port":
        port += 1
    elif failure == "stopped":
        helper._local_desktop_procs[0].poll = lambda: 1
    elif failure == "disabled":
        helper.allow_remote_desktop = False
    state = _state()
    result = dt.desktop_ws_open(
        state,
        {"session_id": "d-rejected", "port": port,
         "local_screen": True}, lambda _frame: None)
    assert result["ok"] is False
    assert error in result["error"]
    assert not state.desktop_ws_sessions
    assert not backend.handshake_request


def test_desktop_commands_preserve_wire_order_under_pool_contention():
    from pawflow_relay._relay_msg_loop import ConnSession

    session = object.__new__(ConnSession)
    deferred = []
    executed = []
    session.pool = types.SimpleNamespace(
        submit=lambda fn, *args: deferred.append((fn, args)))
    session.inflight_lock = threading.Lock()
    session.inflight_cmds = {}
    session.send_lock = threading.Lock()
    session.socket_diag = {}
    session.sock = object()
    session.ws_frame_send = lambda *_args: None
    session._fence_refuses = lambda _msg: False
    session.execute_command = lambda msg, **_kwargs: (
        executed.append((msg["action"], msg.get("data"))) or {"ok": True})
    commands = [
        {"action": "desktop_ws_send", "data": "first"},
        {"action": "desktop_ws_send", "data": "second"},
        {"action": "desktop_ws_close"},
    ]
    for index, command in enumerate(commands):
        session._handle_command({"request_id": str(index), **command})
    for fn, args in reversed(deferred):
        fn(*args)

    assert executed == [("desktop_ws_send", "first"),
                        ("desktop_ws_send", "second"),
                        ("desktop_ws_close", None)]


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


def test_desktop_audio_stream_forwards_framed_opus_packet(monkeypatch):
    relay_sock, backend_sock = socket.socketpair()
    frames = []
    state = _state()
    monkeypatch.setattr(
        dt.socket, "create_connection",
        lambda address, timeout: relay_sock)

    assert dt.audio_stream_open(
        state, {"session_id": "audio-1", "port": 6180},
        lambda frame: frames.append(json.loads(frame.decode("utf-8")))) == {
            "ok": True}
    packet = b"opus-packet"
    backend_sock.sendall(len(packet).to_bytes(2, "big") + packet)

    assert _wait(lambda: any(
        frame.get("type") == "desktop_audio_data"
        and base64.b64decode(frame["data"]) == packet
        for frame in frames))
    assert dt.audio_stream_close(
        state, {"session_id": "audio-1"}) == {"ok": True}
    assert "audio-1" not in state.desktop_audio_sessions
    backend_sock.close()
