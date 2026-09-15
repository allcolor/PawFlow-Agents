"""Protocol and launch-guard checks for the native Windows acceptance fixture."""

import hashlib
import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawflow_relay.utils import api_call
from pawflow_relay.ws_frame import ws_recv, ws_send
from tests import physical_native_windows_probe as native


@contextmanager
def fixture():
    server = native.FixtureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        for peer in tuple(server.live):
            peer.sock.shutdown(socket.SHUT_RDWR)
            peer.sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def install(server, name, token):
    return api_call(
        "http://127.0.0.1:" + str(server.server_port), "POST", "/api/ui",
        body={"action": "service_install", "service_name": name,
              "config_str": "port=0,token=" + token},
        session_token="synthetic-session")


def connect(server, name, token):
    sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
    sock.sendall((
        "GET /ws/relay/" + name + " HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n").encode())
    header = bytearray()
    while not header.endswith(b"\r\n\r\n"):
        data = sock.recv(1)
        assert data
        header.extend(data)
    assert b"101 Switching Protocols" in header
    ws_send(sock, json.dumps({
        "type": "register", "relay_id": name, "token": token,
        "info": {"root": "/workspace"},
    }).encode())
    return sock


def test_real_api_registration_and_websocket_fence():
    with fixture() as server:
        assert install(server, "alpha", "test-alpha") == {"ok": True}
        assert install(server, "beta", "test-beta") == {"ok": True}
        with connect(server, "alpha", "test-alpha") as sock:
            opcode, payload = ws_recv(sock)
            assert opcode == 1
            frame = json.loads(payload)
            assert frame["type"] == "fence_snapshot"
            assert frame["message_id"] and frame["timestamp"]
            ws_send(sock, json.dumps({"type": "fence_ack"}).encode())
            assert json.loads(ws_recv(sock)[1])["type"] == "registered"
            with server.changed:
                assert server.changed.wait_for(lambda: bool(server.live), timeout=5)
                assert server.peers["alpha"][0].info["root"] == "/workspace"
        server.wait_disconnected()
        assert not server.errors
        assert server.actions == [["service_install", "alpha"], ["service_install", "beta"]]


def test_websocket_rejects_sibling_registration_token():
    with fixture() as server:
        install(server, "alpha", "test-alpha")
        install(server, "beta", "test-beta")
        with connect(server, "alpha", "test-beta") as sock:
            assert sock.recv(1) == b""
        with server.changed:
            assert server.changed.wait_for(lambda: bool(server.errors), timeout=5)
        assert server.errors == ["Logical WebSocket authentication failed"]
        assert not server.live
        assert server.peers["alpha"] == []


@pytest.mark.parametrize("name,environ", [("posix", {}), ("nt", {})])
def test_native_acceptance_requires_explicit_disposable_windows(tmp_path, monkeypatch, name, environ):
    monkeypatch.setattr(native, "os", SimpleNamespace(name=name, environ=environ))
    with pytest.raises(RuntimeError, match="explicitly disposable Windows"):
        native.run_acceptance(tmp_path / "unused", "unused-image")
    assert not (tmp_path / "unused").exists()


@pytest.fixture
def exercise_fixture(tmp_path, monkeypatch):
    peers = {}
    exports = []
    for name in ("alpha", "beta"):
        home = tmp_path / name
        home.mkdir()
        exports.append(SimpleNamespace(relay_id=name, home_mount=str(home)))
        peer = SimpleNamespace(
            name=name, fs=SimpleNamespace(calls=[]),
            info={"root": "/workspace", "host_root": "C:/native " + name},
            exec_reply=({"returncode": 0, "stdout": "win32"} if name == "alpha" else {
                "ok": False, "error": "Host action requires allow_local and allow_exec"}))

        def command(action, *, peer=peer, path, **args):
            if action == "exec":
                assert args["local"] is True
                return peer.exec_reply
            if path.startswith("/home/"):
                return {"ok": False, "error": "Path traversal blocked"}
            assert action == "hash_file"
            if path in ("sentinel", "/workspace/sentinel"):
                value = peer.name
            else:
                tag = next(tag for tag, mount in native.MOUNTS.items()
                           if path == mount + "/sentinel-1")
                peer.fs.calls.append(tag + ".read")
                value = peer.name + ":" + tag + ":sentinel-1"
            return {"sha256": hashlib.sha256(value.encode()).hexdigest()}

        peer.command = command
        peers[name] = peer
    relay = SimpleNamespace(plan=SimpleNamespace(exports=exports))
    monkeypatch.setattr(native, "snapshot", lambda _relay: {
        "container": "owned-test-container", "owners": []})

    def docker(*args):
        assert args[:7] == ("exec", "--user", "1000:1000", "owned-test-container",
                            "python3", "-I", "-c")
        assert args[8] in {export.home_mount for export in exports}
        # Execute the profile probe against real isolated files, without Docker.
        return subprocess.check_output(
            [sys.executable, "-I", "-c", *args[7:]], text=True, timeout=5).strip()

    monkeypatch.setattr(native, "docker", docker)
    return peers, relay, exports


def test_exercise_uses_private_home_mounts_and_preserves_profiles(exercise_fixture):
    peers, relay, exports = exercise_fixture
    first, _ = native.exercise(peers, relay, 1)
    second, _ = native.exercise(peers, relay, 2)
    assert first == second
    assert [item["profile"] for item in second.values()] == ["profile-alpha", "profile-beta"]
    for export in exports:
        assert (Path(export.home_mount) / ".chromium-profile/sentinel").read_text() == (
            "profile-" + export.relay_id)


@pytest.mark.parametrize("error", [
    "Cannot reach host helper at example: connection refused",
    "Host helper closed connection",
    "Invalid host helper capability",
])
def test_exercise_does_not_accept_transport_failure_as_permission_denial(exercise_fixture, error):
    peers, relay, _exports = exercise_fixture
    peers["beta"].exec_reply = {"ok": False, "error": error}
    with pytest.raises(RuntimeError, match="Expected host execution permission denial"):
        native.exercise({"beta": peers["beta"]}, relay, 1)


def test_exercise_rejects_profile_corruption_after_restart(exercise_fixture):
    peers, relay, exports = exercise_fixture
    native.exercise(peers, relay, 1)
    (Path(exports[1].home_mount) / ".chromium-profile/sentinel").write_text("profile-alpha")
    with pytest.raises(RuntimeError, match="Profile was lost across physical restart"):
        native.exercise(peers, relay, 2)
