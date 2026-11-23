"""Detached MCP terminal mode: the user starts the client, a separate
process provides the return channel, the bridge registers it."""

from __future__ import annotations

import base64
import importlib.util
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

from pawflow_relay.mcp_stdio import RelayController

ROOT = Path(__file__).resolve().parents[1]


def _load_script(filename: str, module_name: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def launcher():
    return _load_script("mcp-session-launcher.py", "pawflow_mcp_detached_launcher")


class _Proc:
    def poll(self):
        return None


class _Bridge:
    def __init__(self):
        self.calls = []

    def control(self, operation, payload):
        self.calls.append((operation, dict(payload)))
        return {"ok": True, "terminal_ready": payload.get("operation") == "register"}


def _record(**overrides):
    record = {"session_id": "pawflow-mcp-detached-1", "kind": "winpty",
              "target": "127.0.0.1:5000", "secret": "s3cret",
              "state_path": "/tmp/markers.jsonl"}
    record.update(overrides)
    return record


def _controller(tmp_path, **kwargs):
    bridge = _Bridge()
    terminal_file = tmp_path / "detached-terminal.json"
    controller = RelayController(
        bridge, tmp_path, "agy", terminal_file=str(terminal_file), **kwargs)
    controller._process = _Proc()
    return controller, bridge, terminal_file


def _registrations(bridge):
    return [payload for operation, payload in bridge.calls
            if operation == "terminal"]


# --- bridge side -----------------------------------------------------------

def test_bridge_registers_a_detached_terminal_once(tmp_path):
    controller, bridge, terminal_file = _controller(tmp_path)
    controller.sync_detached_terminal()
    assert bridge.calls == []

    terminal_file.write_text(json.dumps(_record()), encoding="utf-8")
    controller.sync_detached_terminal()
    controller.sync_detached_terminal()

    calls = _registrations(bridge)
    assert len(calls) == 1
    assert calls[0]["operation"] == "register"
    assert calls[0]["client_id"] == controller.client_id
    assert calls[0]["kind"] == "winpty"
    assert calls[0]["target"] == "127.0.0.1:5000"
    assert calls[0]["secret"] == "s3cret"


def test_bridge_reregisters_when_the_record_changes_or_the_server_lost_it(tmp_path):
    controller, bridge, terminal_file = _controller(tmp_path)
    terminal_file.write_text(json.dumps(_record()), encoding="utf-8")
    controller.sync_detached_terminal()

    controller.sync_detached_terminal(
        {"client_id": controller.client_id, "terminal_ready": True})
    assert len(_registrations(bridge)) == 1

    controller.sync_detached_terminal(
        {"client_id": controller.client_id, "terminal_ready": False})
    assert len(_registrations(bridge)) == 2

    terminal_file.write_text(
        json.dumps(_record(target="127.0.0.1:6000")), encoding="utf-8")
    controller.sync_detached_terminal()
    calls = _registrations(bridge)
    assert len(calls) == 3 and calls[-1]["target"] == "127.0.0.1:6000"


def test_bridge_clears_the_terminal_when_the_attach_process_leaves(tmp_path):
    controller, bridge, terminal_file = _controller(tmp_path)
    terminal_file.write_text(json.dumps(_record()), encoding="utf-8")
    controller.sync_detached_terminal()
    terminal_file.unlink()

    controller.sync_detached_terminal()
    controller.sync_detached_terminal()

    operations = [call["operation"] for call in _registrations(bridge)]
    assert operations == ["register", "clear"]


def test_launcher_terminal_takes_precedence_over_the_detached_file(tmp_path):
    controller, bridge, terminal_file = _controller(
        tmp_path, terminal_session_id="launched", terminal_kind="tmux",
        terminal_target="t:0.0")
    terminal_file.write_text(json.dumps(_record()), encoding="utf-8")
    controller.sync_detached_terminal()
    assert bridge.calls == []


def test_invalid_records_and_foreign_leases_are_ignored(tmp_path):
    controller, bridge, terminal_file = _controller(tmp_path)
    terminal_file.write_text(json.dumps(_record(kind="ssh")), encoding="utf-8")
    controller.sync_detached_terminal()
    terminal_file.write_text("not json", encoding="utf-8")
    controller.sync_detached_terminal()
    terminal_file.write_text(json.dumps(_record()), encoding="utf-8")
    controller.sync_detached_terminal({"client_id": "cli_other"})
    assert bridge.calls == []


def test_client_launcher_points_the_bridge_at_the_session_terminal_file(
        tmp_path, monkeypatch):
    client_launcher = _load_script(
        "mcp-client-launcher.py", "pawflow_mcp_detached_client_launcher")
    profile = tmp_path / "session" / "profile.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({
        "url": "https://pawflow.example/mcp/srv_x", "api_key": "k",
        "relay_dir": str(tmp_path)}), encoding="utf-8")
    seen = []
    import pawflow_relay.mcp_stdio as stdio
    monkeypatch.setattr(stdio, "main", lambda argv: seen.append(argv) or 0)
    # main() writes these into the process environment and sys.path:
    # register them with monkeypatch so they are restored afterwards.
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("PAWFLOW_RELAY_RUNTIME_ROOT", "PAWFLOW_MCP_API_KEY",
                 "PAWFLOW_GATEWAY_KEY"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    for name in ("PAWFLOW_MCP_TERMINAL_SESSION_ID", "PAWFLOW_MCP_TERMINAL_KIND",
                 "PAWFLOW_MCP_TERMINAL_TARGET", "PAWFLOW_MCP_TERMINAL_STATE_PATH"):
        monkeypatch.delenv(name, raising=False)

    assert client_launcher.main(["--profile", str(profile), "--client-name", "agy"]) == 0
    argv = seen[-1]
    assert argv[argv.index("--terminal-file") + 1] == str(
        profile.parent / "detached-terminal.json")

    monkeypatch.setenv("PAWFLOW_MCP_TERMINAL_SESSION_ID", "launched")
    client_launcher.main(["--profile", str(profile), "--client-name", "agy"])
    assert "--terminal-file" not in seen[-1]


# --- attach side -----------------------------------------------------------

def _session(tmp_path) -> Path:
    session = tmp_path / "sessions" / "work" / "session.json"
    session.parent.mkdir(parents=True)
    session.write_text(json.dumps({
        "relay_dir": str(tmp_path), "entries": {"agy": {}},
        "terminal": {"marker_path": str(session.parent / "injected-prompts.jsonl")},
    }), encoding="utf-8")
    return session


def test_attach_tmux_publishes_and_removes_its_record(launcher, tmp_path):
    session = _session(tmp_path)
    record_path = launcher.detached_terminal_path(session)
    seen = []
    states = iter([True, True, False])

    def alive():
        if record_path.is_file():
            seen.append(json.loads(record_path.read_text(encoding="utf-8")))
        return next(states)

    assert launcher.attach_detached(
        session, tmux_target="work:0.0", poll_seconds=0, alive=alive) == 0
    assert seen and seen[0]["kind"] == "tmux"
    assert seen[0]["target"] == "work:0.0"
    assert seen[0]["state_path"].endswith("injected-prompts.jsonl")
    assert not record_path.exists()


def test_attach_does_not_remove_a_record_owned_by_a_newer_attach(launcher, tmp_path):
    session = _session(tmp_path)
    record_path = launcher.detached_terminal_path(session)
    states = iter([True, False])

    def alive():
        if record_path.is_file():
            record_path.write_text(json.dumps(_record(session_id="newer")),
                                   encoding="utf-8")
        return next(states)

    launcher.attach_detached(
        session, tmux_target="work:0.0", poll_seconds=0, alive=alive)
    assert json.loads(record_path.read_text(encoding="utf-8"))["session_id"] == "newer"


@pytest.mark.parametrize("kwargs", [
    {}, {"pid": 12, "tmux_target": "a:0"}, {"tmux_target": "bad target;"}])
def test_attach_rejects_invalid_requests(launcher, tmp_path, kwargs):
    with pytest.raises(ValueError):
        launcher.attach_detached(
            _session(tmp_path), alive=lambda: True, **kwargs)


def test_attach_rejects_a_target_that_is_not_running(launcher, tmp_path):
    with pytest.raises(ValueError, match="not running"):
        launcher.attach_detached(
            _session(tmp_path), tmux_target="gone:0", alive=lambda: False)


def test_attach_mode_does_not_need_a_client(launcher, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "attach_detached",
                        lambda *a, **k: calls.append(k) or 0)
    assert launcher.main(["--session", str(tmp_path / "s.json"),
                          "--attach-tmux", "work:0.0"]) == 0
    assert calls == [{"pid": 0, "tmux_target": "work:0.0"}]
    assert launcher.main(["--session", str(tmp_path / "s.json")]) == 2


def test_console_helper_mode_reads_the_prompt_from_stdin(launcher, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "_write_process_console",
                        lambda pid, text: calls.append((pid, text)))

    class _Stdin:
        buffer = type("B", (), {"read": staticmethod(lambda: "héllo".encode())})()

    monkeypatch.setattr(launcher.sys, "stdin", _Stdin())
    assert launcher.main(["--write-console-pid", "4242"]) == 0
    assert calls == [(4242, "héllo")]


def test_loopback_server_marks_then_writes_through_its_writer(launcher, tmp_path):
    marker = tmp_path / "markers.jsonl"
    written = []

    def writer(text):
        assert "m-1" in marker.read_text(encoding="utf-8")
        written.append(text)

    server = launcher._LoopbackServer("secret", marker, writer=writer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def send(secret):
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.sendall(json.dumps({
                    "secret": secret, "message_id": "m-1",
                    "content_b64": base64.b64encode(b"hello").decode(),
                }).encode() + b"\n")
                return json.loads(sock.makefile().readline())

        assert send("wrong")["ok"] is False
        assert written == []
        assert send("secret") == {"ok": True}
        assert written == ["hello"]
    finally:
        server.shutdown()
        server.server_close()
