"""Container and private HTTP/SSE transport checks for OpenCode."""

from __future__ import annotations

import io
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from core._llm_types import LLMClientError
from core.opencode_pool import (
    CONTAINER_HOME, CONTAINER_RUNTIME, OpenCodeCancelled, OpenCodeHTTPError,
    OpenCodePool, OpenCodeRuntime,
)
from tools import opencode_bridge


@pytest.fixture
def runtime_root(tmp_path, monkeypatch):
    monkeypatch.setattr(OpenCodePool, "base_dir", staticmethod(lambda: tmp_path / "runtime"))
    return tmp_path / "runtime"


def runtime():
    return OpenCodeRuntime(("user", "conv", "agent", "service"), "revision", {})


def test_home_shared_only_for_same_user_service(runtime_root):
    first = OpenCodePool.home_dir("u", "s")
    assert first == OpenCodePool.home_dir("u", "s")
    assert first != OpenCodePool.home_dir("other", "s")
    assert first != OpenCodePool.home_dir("u", "other")
    assert json.loads((first / ".local/share/opencode/auth.json").read_text()) == {}


def test_paths_include_every_identity_component_and_config_revision(runtime_root):
    scope = ("u", "c", "a", "s")
    original = OpenCodePool.runtime_dir(scope, "revision")
    for index in range(4):
        changed = list(scope)
        changed[index] += "other"
        assert OpenCodePool.runtime_dir(tuple(changed), "revision") != original
    assert OpenCodePool.runtime_dir(scope, "changed") != original
    # Hashing also avoids lossy slash/underscore sanitization collisions.
    assert OpenCodePool.home_dir("u/a", "s") != OpenCodePool.home_dir("u_a", "s")
    with pytest.raises(ValueError):
        OpenCodePool.runtime_dir(("u", "", "a", "s"), "revision")


def test_container_mounts_only_own_scope_and_auth_home(runtime_root, monkeypatch):
    from core import docker_utils, apparmor
    from core.antigravity_observer_pool import AntigravityObserverPool
    import core.opencode_pool as module
    commands, copied = [], []
    monkeypatch.setattr(docker_utils, "docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(docker_utils, "get_server_id", lambda: "server")
    monkeypatch.setattr(docker_utils, "pawflow_container_labels", lambda kind: ["--label", "kind=" + kind])
    monkeypatch.setattr(docker_utils, "to_host_path", lambda p: p)
    monkeypatch.setattr(docker_utils, "translate_path", lambda p: p)
    monkeypatch.setattr(apparmor, "apparmor_security_opts", lambda image: [])
    monkeypatch.setattr(AntigravityObserverPool, "_copy_runtime_files",
                        staticmethod(lambda *args: copied.append(args)))
    monkeypatch.setattr(module.subprocess, "run",
                        lambda argv, **kw: commands.append((argv, kw)) or SimpleNamespace(returncode=0))
    process = SimpleNamespace(
        stdin=io.StringIO(), stdout=io.StringIO(), poll=lambda: None,
        kill=lambda: None, wait=lambda **kw: 0)
    monkeypatch.setattr(module.subprocess, "Popen",
                        lambda argv, **kw: commands.append((argv, kw)) or process)
    def ready(self):
        self.version = "1.14.19"
        self._ready.set()
    monkeypatch.setattr(OpenCodeRuntime, "_read_frames", ready)
    state = OpenCodeRuntime(("u", "c", "a", "s"), "r", {"ANTHROPIC_API_KEY": "secret"})
    state.start()
    run_args = commands[0][0]
    mounts = [run_args[i + 1] for i, value in enumerate(run_args) if value == "-v"]
    assert mounts == [f"{state.home}:{CONTAINER_HOME}", f"{state.directory}:{CONTAINER_RUNTIME}"]
    assert not any(value in run_args for value in ["-p", "--publish", "--privileged", "--network=host"])
    exec_args, options = commands[1]
    assert "ANTHROPIC_API_KEY" in exec_args
    assert not any("secret" in arg for arg in exec_args)
    assert options["env"]["ANTHROPIC_API_KEY"] == "secret"
    assert f"XDG_DATA_HOME={CONTAINER_RUNTIME}/data" in exec_args
    assert f"XDG_CONFIG_HOME={CONTAINER_RUNTIME}/config" in exec_args
    assert (state.directory / "data/opencode/auth.json").readlink() == Path(
        CONTAINER_HOME + "/.local/share/opencode/auth.json")
    assert {Path(src).name for src, _ in copied[0][1]} == {
        "opencode_bridge.py", "mcp_bridge.py", "tool_json.py", "pawflow.py"}
    state.close()
    assert commands[-1][0] == ["docker", "rm", "-f", state.name]


def test_request_status_is_checked_without_leaking_response_body(runtime_root):
    state = runtime()
    class Wire:
        def write(self, line):
            frame = json.loads(line)
            state._pending[frame["id"]].put({"status": 401, "body": "SECRET"})
        def flush(self):
            pass
    state.process = SimpleNamespace(stdin=Wire())
    with pytest.raises(OpenCodeHTTPError) as exc:
        state.request("GET", "/provider")
    assert exc.value.status == 401
    assert "SECRET" not in str(exc.value)
    assert not state._pending


def test_pending_request_unblocks_on_force_close(runtime_root, monkeypatch):
    state = runtime()
    received = threading.Event()
    class Wire:
        def write(self, line):
            received.set()
        def flush(self):
            pass
    state.process = SimpleNamespace(stdin=Wire())
    output = []
    def request():
        try:
            state.request("POST", "/session")
        except OpenCodeCancelled:
            output.append("cancelled")
    worker = threading.Thread(target=request)
    worker.start()
    assert received.wait(2)
    state._closed.set()
    worker.join(2)
    assert output == ["cancelled"]
    assert not state._pending


@pytest.mark.parametrize("line", [
    "{invalid JSON",
    json.dumps({"event": {"type": "one"}}) + "\n" + json.dumps({"event": {"type": "two"}}),
])
def test_broken_or_overflowed_transport_is_fatal(runtime_root, line):
    state = runtime()
    state.events = queue.Queue(maxsize=1)
    state.process = SimpleNamespace(stdout=io.StringIO(line))
    state._read_frames()
    with pytest.raises(LLMClientError):
        state.next_event()


def test_unclean_eof_is_fatal(runtime_root):
    state = runtime()
    state.process = SimpleNamespace(stdout=io.StringIO(""))
    state._read_frames()
    with pytest.raises(LLMClientError, match="exited"):
        state.next_event()


def test_bridge_sse_parses_comments_crlf_and_multiline_data():
    lines = [b": heartbeat\r\n", b"event: message\r\n",
             b'data: {"type":\r\n', b'data: "server.connected"}\r\n', b"\r\n",
             b'data: {"type":"session.status","properties":{}}\n', b"\n"]
    assert list(opencode_bridge.sse_events(lines)) == [
        {"type": "server.connected"}, {"type": "session.status", "properties": {}}]


def test_bridge_rejects_partial_sse_record():
    with pytest.raises(RuntimeError, match="within a record"):
        list(opencode_bridge.sse_events([b'data: {"type":"unfinished"}\n']))


def test_bridge_http_uses_real_method_json_and_status(monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            requests.append((self.path, json.loads(self.rfile.read(length))))
            self.send_response(204)
            self.end_headers()
        def do_GET(self):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"SECRET ERROR BODY")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(opencode_bridge, "BASE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        assert opencode_bridge.request("POST", "/session/ses_1/prompt_async", {
            "parts": [{"type": "text", "text": "hello"}]}) == (204, None)
        assert requests == [("/session/ses_1/prompt_async", {"parts": [{"type": "text", "text": "hello"}]})]
        assert opencode_bridge.request("GET", "/provider") == (403, None)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_bridge_disallows_absolute_external_url():
    with pytest.raises(ValueError):
        opencode_bridge.request("GET", "https://evil.example")
    with pytest.raises(ValueError):
        opencode_bridge.request("GET", "//evil.example")


def test_bridge_scope_lock_prevents_concurrent_database_owners(tmp_path):
    pytest.importorskip("fcntl")
    path = tmp_path / "server.lock"
    first = opencode_bridge.lock_scope(path)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            opencode_bridge.lock_scope(path)
    finally:
        first.close()
    opencode_bridge.lock_scope(path).close()


def test_runtime_rejects_symlinked_data_directory(runtime_root, tmp_path):
    state = runtime()
    state.directory.mkdir(parents=True)
    (state.directory / "data").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(LLMClientError, match="symlinks"):
        state.start()
