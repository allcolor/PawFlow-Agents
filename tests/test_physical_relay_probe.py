"""Regression tests for evidence and protocol checks in real relay acceptance."""

import base64
import hashlib
import json
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pawflow_relay.ws_frame import ws_send
from tests import physical_relay_probe as probe


def test_relay_probe_refuses_non_disposable_execution(monkeypatch):
    monkeypatch.delenv("PAWFLOW_DISPOSABLE_ACCEPTANCE", raising=False)
    with pytest.raises(RuntimeError, match="explicitly disposable"):
        probe.run_acceptance()


def test_worker_exports_keep_logical_mounts_tokens_and_readonly():
    first, second = probe.exports("ws://192.0.2.1:9000")
    assert first["root_mount"] != second["root_mount"]
    assert first["home_mount"] != second["home_mount"]
    assert "--readonly" not in first["command"]
    assert "--readonly" in second["command"]
    assert "--token=synthetic-token-alpha" in first["command"]
    assert "--token=synthetic-token-beta" in second["command"]
    assert first["command"][2] == "/opt/pawflow/pawflow_relay_launcher.py"


def test_fixture_files_are_scoped_by_peer_and_backend():
    first, second = probe.FixtureFs("alpha"), probe.FixtureFs("beta")
    one = first.handle("sfs.open", {"path": "/sentinel-1", "flags": 0})["data"]["fh"]
    two = second.handle("ffs.open", {"path": "/sentinel-1", "flags": 0})["data"]["fh"]
    for fs, tag, handle, expected in (
        (first, "sfs", one, b"alpha:sfs:sentinel-1"),
        (second, "ffs", two, b"beta:ffs:sentinel-1"),
    ):
        reply = fs.handle(tag + ".read", {"fh": handle, "offset": 0, "size": 100})
        assert base64.b64decode(reply["data"]["data_b64"]) == expected
    assert first.handle("ffs.read", {"fh": one, "offset": 0, "size": 10})["error"] == "EBADF"
    assert first.handle("sfs.getattr", {"path": "/sibling-only"})["error"] == "ENOENT"


def test_fixture_refuses_write_and_released_handles():
    fs = probe.FixtureFs("alpha")
    assert fs.handle("sfs.open", {"path": "/sentinel-1", "flags": 1})["error"] == "EROFS"
    handle = fs.handle("sfs.open", {"path": "/sentinel-1", "flags": 0})["data"]["fh"]
    assert fs.handle("sfs.release", {"fh": handle}) == {"data": {}}
    assert fs.handle("sfs.read", {"fh": handle, "offset": 0, "size": 1})["error"] == "EBADF"


def test_wrong_workspace_evidence_is_rejected():
    peers = {"alpha": SimpleNamespace(command=lambda *a, **kw: {"sha256": "wrong"})}
    with pytest.raises(RuntimeError, match="Wrong logical workspace"):
        probe.exercise(peers, 1)


def test_fuse_bytes_without_actual_wire_reads_do_not_pass():
    class CachedPeer:
        fs = SimpleNamespace(calls=[])

        def command(self, action, **args):
            content = b"alpha" if args["path"].startswith("/workspace") else b"alpha:sfs:sentinel-1"
            return {"sha256": hashlib.sha256(content).hexdigest()}

    with pytest.raises(RuntimeError, match="No real FUSE read"):
        probe.exercise({"alpha": CachedPeer()}, 1)


def test_registration_wait_fails_if_supervisor_dies():
    with (probe.FixtureServer(("127.0.0.1", 0)) as server,
          pytest.raises(RuntimeError, match="before worker registration")):
        server.wait_peers(1, SimpleNamespace(poll=lambda: 1))


@contextmanager
def websocket_fixture():
    with probe.FixtureServer(("127.0.0.1", 0)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=3) as client:
                client.sendall(
                    b"GET /ws/relay/alpha HTTP/1.1\r\nHost: localhost\r\n"
                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
                header = bytearray()
                while not header.endswith(b"\r\n\r\n"):
                    header.extend(client.recv(1))
                assert b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in header
                yield server, client
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_real_socket_fence_registration_and_inverse_fuse_reply():
    with websocket_fixture() as (server, client):
        ws_send(client, json.dumps({"type": "register", "relay_id": "alpha",
                                    "token": "synthetic-token-alpha"}).encode())
        assert probe.receive(client)["type"] == "fence_snapshot"
        ws_send(client, b'{"type":"fence_ack"}')
        assert probe.receive(client) == {"type": "registered", "relay_id": "alpha"}
        ws_send(client, json.dumps({"type": "relay_request", "request_id": "lookup",
                                    "method": "sfs.getattr", "args": {"path": "/sentinel-1"}}).encode())
        reply = probe.receive(client)
        assert reply["request_id"] == "lookup"
        assert reply["data"]["st_size"] == len(b"alpha:sfs:sentinel-1")
        assert server.peers["alpha"][0].fs.calls == ["sfs.getattr"]
        assert server.peers["beta"] == []
        event = {"type": "desktop_ws_data", "session_id": "vnc-alpha",
                 "opcode": 2, "data": base64.b64encode(b"RFB 003.008\n").decode()}
        ws_send(client, json.dumps(event).encode())
        assert server.peers["alpha"][0].events.get(timeout=3) == event
        for kind in ("desktop_audio_data", "desktop_audio_close"):
            audio = {"type": kind, "session_id": "audio-alpha", "data": "YWJj"}
            ws_send(client, json.dumps(audio).encode())
            assert server.peers["alpha"][0].audio_events.get(timeout=3) == audio
        assert server.peers["alpha"][0].events.empty()


def test_invalid_registration_never_becomes_a_logical_peer():
    with websocket_fixture() as (server, client):
        ws_send(client, json.dumps({"type": "register", "relay_id": "alpha",
                                    "token": "synthetic-token-beta"}).encode())
        assert client.recv(1) == b""
        assert server.peers["alpha"] == []
        assert server.errors == ["Invalid synthetic registration"]
