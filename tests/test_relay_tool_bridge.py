"""Scripts on a relay call PawFlow tools through the relay tool bridge.

Chain under test: SDK (docker/pawflow_sdk/pawflow.py) -> loopback bridge in
the relay worker (pawflow_relay/tool_bridge.py) -> `tool.*` relay request ->
server handler (services/relay_tool_bridge.py) -> ToolRelayService. The
script presents a capability (core/script_capability.py); the identity is
never taken from the script.

Before this, execute_script injected a tool relay URL built from a `port`
the relay service no longer has, with no identity, so `from pawflow import
tools` always failed (2026-11-16).
"""
import importlib.util
import time
from pathlib import Path

import pytest

from core import script_capability
from services import relay_tool_bridge

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_caps():
    script_capability._caps.clear()
    yield
    script_capability._caps.clear()


def _mint(**overrides):
    fields = {"relay_service_id": "relayA", "user_id": "alice",
              "conversation_id": "conv1", "agent_name": "agentA",
              "run_identity": ("run-1", 7, "conv1", "agentA")}
    fields.update(overrides)
    return script_capability.mint(**fields)


# ── capability registry ──────────────────────────────────────────────

def test_capability_resolves_only_on_its_relay_and_user():
    token = _mint()
    cap = script_capability.resolve(token, relay_service_id="relayA",
                                    user_id="alice")
    assert (cap.conversation_id, cap.agent_name) == ("conv1", "agentA")
    with pytest.raises(PermissionError, match="another relay"):
        script_capability.resolve(token, relay_service_id="relayB",
                                  user_id="alice")
    with pytest.raises(PermissionError, match="another user"):
        script_capability.resolve(token, relay_service_id="relayA",
                                  user_id="bob")


def test_capability_is_unusable_after_revoke_or_expiry():
    token = _mint()
    script_capability.revoke(token)
    with pytest.raises(PermissionError):
        script_capability.resolve(token, relay_service_id="relayA",
                                  user_id="alice")
    expired = _mint(ttl_seconds=-1)
    with pytest.raises(PermissionError):
        script_capability.resolve(expired, relay_service_id="relayA",
                                  user_id="alice")


def test_capability_requires_a_full_identity():
    with pytest.raises(ValueError):
        _mint(agent_name="")


class _Relay:
    _service_id = "relayA"


def test_mint_for_relay_takes_the_agent_from_the_run_identity(monkeypatch):
    from services import _tool_relay_base
    monkeypatch.setattr(_tool_relay_base._thread_local, "run_identity",
                        ("run-1", 3, "conv1", "agentA"), raising=False)
    env, token = script_capability.mint_for_relay(
        _Relay(), user_id="alice", conversation_id="", agent_name="")
    assert env[script_capability.CAP_ENV] == token
    cap = script_capability.resolve(token, relay_service_id="relayA",
                                    user_id="alice")
    assert cap.agent_name == "agentA" and cap.run_identity[1] == 3


def test_mint_for_relay_without_identity_mints_nothing(monkeypatch):
    from services import _tool_relay_base
    monkeypatch.setattr(_tool_relay_base._thread_local, "run_identity",
                        None, raising=False)
    assert script_capability.mint_for_relay(
        _Relay(), user_id="alice", conversation_id="conv1",
        agent_name="") == ({}, "")
    assert script_capability.mint_for_relay(
        object(), user_id="alice", conversation_id="conv1",
        agent_name="agentA") == ({}, "")


# ── server handler ───────────────────────────────────────────────────

class _ToolRelay:
    def __init__(self):
        self.calls = []

    def _handle_execute(self, request_id, tool_name, arguments, user_id,
                        conversation_id, agent_name, publish_code_mode=True):
        from services._tool_relay_base import current_run_identity
        # The bridge draws its own rows, for every provider.
        assert publish_code_mode is False
        self.calls.append((tool_name, arguments, user_id, conversation_id,
                           agent_name, current_run_identity()))
        if arguments.get("path") == "missing.txt":
            # Handlers report most failures as text, not as an error reply.
            return {"type": "result", "request_id": request_id,
                    "data": "Error reading 'missing.txt': [Errno 2] No such file"}
        if tool_name == "exists":
            path = arguments["path"]
            answer = "Exists" if path == "a.txt" else "Does not exist"
            return {"type": "result", "request_id": request_id,
                    "data": f"{answer}: {path}"}
        return {"type": "result", "request_id": request_id,
                "data": f"ran {tool_name}"}

    def _handle_get_schema(self, request_id, tool_name, user_id="",
                           conversation_id="", agent_name=""):
        return {"type": "result", "request_id": request_id,
                "data": {"name": tool_name}}

    def _handle_pfp_host_call(self, request_id, invocation, host_call,
                              user_id, conversation_id, agent_name):
        return {"type": "result", "request_id": request_id,
                "data": {"ok": True, "seen": [user_id, conversation_id,
                                               agent_name]}}


@pytest.fixture
def rows(monkeypatch):
    """Transcript rows (and their SSE events) the bridge writes."""
    written = []
    monkeypatch.setattr(
        relay_tool_bridge, "_write_row",
        lambda cap, msg, sse: written.append((msg, sse)))
    monkeypatch.setattr(relay_tool_bridge, "_agent_service",
                        lambda cap: "svcA")
    return written


@pytest.fixture
def tool_relay(monkeypatch, rows):
    relay = _ToolRelay()
    monkeypatch.setattr(relay_tool_bridge, "_live_tool_relay", lambda: relay)
    return relay


def _handle(method, args, relay="relayA", user="alice"):
    return relay_tool_bridge.handle(
        method, args, relay_service_id=relay, user_id=user)


def test_call_runs_with_the_capability_identity_and_run(tool_relay):
    token = _mint()
    reply = _handle("tool.call", {"cap": token, "tool_name": "grep",
                                  "arguments": {"pattern": "x"}})
    assert reply == {"data": "ran grep"}
    name, args, user, conv, agent, run = tool_relay.calls[0]
    assert (name, args, user, conv, agent) == (
        "grep", {"pattern": "x"}, "alice", "conv1", "agentA")
    # The nested call is fenced like the script's parent call.
    assert run == ("run-1", 7, "conv1", "agentA")
    from services._tool_relay_base import current_run_identity
    assert current_run_identity() is None


def test_each_call_is_shown_as_a_display_only_script_row(tool_relay, rows):
    token = _mint(parent_call_id="tc-parent")
    _handle("tool.call", {"cap": token, "tool_name": "grep",
                          "arguments": {"pattern": "x"}})
    (call_msg, call_sse), (result_msg, result_sse) = rows
    # Rows never enter the agent's context: only what the script prints does.
    assert call_msg["display_only"] is True and result_msg["display_only"] is True
    assert call_msg["role"] == "tool_call"
    assert call_msg["tool_origin"] == "script"
    assert call_msg["source"] == {"type": "agent", "name": "agentA"}
    assert result_msg["role"] == "tool"
    assert result_msg["tool_call_id"] == call_msg["tool_call_id"]
    assert result_msg["content"] == "ran grep"
    assert call_sse["type"] == "tool_call"
    assert call_sse["data"]["parent_tc_id"] == "tc-parent"
    assert call_sse["data"]["tool_origin"] == "script"
    assert call_sse["data"]["llm_service"] == "svcA"
    assert result_sse["type"] == "tool_result"
    assert result_sse["data"]["tc_id"] == call_sse["data"]["tc_id"]


def test_a_refused_call_draws_no_row(tool_relay, rows):
    _handle("tool.call", {"cap": _mint(), "tool_name": "ask_user"})
    _handle("tool.call", {"cap": "forged", "tool_name": "read"})
    assert rows == []


def test_rows_are_real_transcript_rows(monkeypatch):
    """The row shape is accepted by the store (no writer stub)."""
    written = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            from core.conversation_writer import _require_ts_seq
            _require_ts_seq(msg)
            written.append((msg, kwargs))

    from core.conversation_writer import ConversationWriter
    monkeypatch.setattr(ConversationWriter, "for_conversation",
                        classmethod(lambda cls, cid: _Writer()))
    cap = script_capability.resolve(_mint(), relay_service_id="relayA",
                                    user_id="alice")
    relay_tool_bridge._publish_script_call(cap, "c1", "read", {"path": "a"})
    _msg, kwargs = written[0]
    assert kwargs["agent_name"] == "agentA" and kwargs["user_id"] == "alice"
    assert kwargs["sse_events"][0]["type"] == "tool_call"


def test_a_capability_from_another_relay_is_refused(tool_relay):
    token = _mint(relay_service_id="relayB")
    reply = _handle("tool.call", {"cap": token, "tool_name": "read"})
    assert reply["error"] == "EACCES"
    assert tool_relay.calls == []


def test_a_missing_capability_is_refused(tool_relay):
    reply = _handle("tool.call", {"tool_name": "read"})
    assert reply["error"] == "EACCES"


@pytest.mark.parametrize("tool", sorted(relay_tool_bridge.DENIED_TOOLS))
def test_turn_control_and_recursive_tools_are_refused(tool_relay, tool):
    reply = _handle("tool.call", {"cap": _mint(), "tool_name": tool})
    assert reply["error"] == "EPERM"
    assert tool_relay.calls == []


def test_schema_and_pfp_host_call(tool_relay):
    token = _mint()
    assert _handle("tool.schema", {"cap": token, "tool_name": "read"}) == {
        "data": {"name": "read"}}
    reply = _handle("tool.pfp_host_call", {
        "cap": token, "invocation": {"context": {}}, "host_call": {}})
    assert reply["data"]["seen"] == ["alice", "conv1", "agentA"]


def test_unknown_method_and_relay_without_owner(tool_relay):
    assert _handle("tool.nope", {"cap": _mint(), "tool_name": "read"})[
        "error"] == "ENOSYS"
    assert _handle("tool.call", {"cap": _mint()}, user="")["error"] == "EACCES"


def test_a_tool_failure_reported_as_text_is_an_error(tool_relay, rows):
    reply = _handle("tool.call", {"cap": _mint(), "tool_name": "read",
                                  "arguments": {"path": "missing.txt"}})
    assert reply["error"] == "EIO"
    assert reply["message"].startswith("Error reading 'missing.txt'")
    # The user still sees the failed call and its result.
    assert rows[1][0]["content"].startswith("Error reading")


# ── relay bridge + SDK over a real loopback socket ───────────────────

class _ServerClient:
    """Stands in for ServerFsClient: answers like the server would."""

    def __init__(self):
        self.timeouts = []

    def request(self, method, args, timeout=None):
        self.timeouts.append(timeout)
        return relay_tool_bridge.handle(
            method, args, relay_service_id="relayA", user_id="alice")


def _load_sdk(monkeypatch, endpoint, cap):
    monkeypatch.setenv("PAWFLOW_TOOL_ENDPOINT", endpoint)
    if cap:
        monkeypatch.setenv("PAWFLOW_TOOL_CAP", cap)
    else:
        monkeypatch.delenv("PAWFLOW_TOOL_CAP", raising=False)
    monkeypatch.delenv("PAWFLOW_TOOL_RELAY_URL", raising=False)
    monkeypatch.delenv("PAWFLOW_FS_SERVICE", raising=False)
    spec = importlib.util.spec_from_file_location(
        f"pawflow_sdk_bridge_{time.time_ns()}",
        ROOT / "docker" / "pawflow_sdk" / "pawflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bridge(monkeypatch):
    from pawflow_relay.tool_bridge import ENDPOINT_ENV, ToolBridge
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    server = _ServerClient()
    instance = ToolBridge().start()
    instance.swap.set_inner(server)
    try:
        yield instance, server
    finally:
        instance.stop()


def test_bridge_publishes_its_loopback_endpoint(bridge):
    import os
    instance, _ = bridge
    assert instance.endpoint.startswith("127.0.0.1:")
    assert os.environ["PAWFLOW_TOOL_ENDPOINT"] == instance.endpoint


def test_sdk_calls_a_tool_through_the_bridge(bridge, tool_relay, monkeypatch):
    instance, server = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, _mint())
    assert sdk.tools.call("grep", pattern="TODO") == "ran grep"
    assert sdk.tools.get_schema("read") == {"name": "read"}
    assert tool_relay.calls[0][:2] == ("grep", {"pattern": "TODO"})
    assert server.timeouts[0] == pytest.approx(3600.0)


def test_sdk_fs_helpers_call_the_real_tools(bridge, tool_relay, monkeypatch):
    instance, _ = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, _mint())
    sdk.fs.read_file("a.txt")
    sdk.fs.write_file("b.txt", "hi")
    sdk.fs.exec("ls")
    sdk.fs.grep("x", path="src")
    sdk.fs.delete_file("c.txt")
    assert [c[:2] for c in tool_relay.calls] == [
        ("read", {"path": "a.txt"}),
        ("write", {"path": "b.txt", "content": "hi"}),
        ("bash", {"command": "ls"}),
        ("grep", {"pattern": "x", "path": "src"}),
        ("delete", {"path": "c.txt"}),
    ]


def test_sdk_reports_a_refused_call(bridge, tool_relay, monkeypatch):
    instance, _ = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, _mint())
    with pytest.raises(RuntimeError, match="cannot be called from a script"):
        sdk.tools.call("ask_user", question="?")


def test_sdk_raises_on_a_failed_tool(bridge, tool_relay, monkeypatch):
    instance, _ = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, _mint())
    with pytest.raises(RuntimeError, match="Tool error: Error reading"):
        sdk.fs.read_file("missing.txt")


def test_sdk_exists_reads_the_exists_tool_answer(bridge, tool_relay, monkeypatch):
    instance, _ = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, _mint())
    assert sdk.fs.exists("a.txt") is True
    assert sdk.fs.exists("b.txt") is False


def test_sdk_without_capability_says_tools_are_unavailable(bridge, monkeypatch):
    instance, _ = bridge
    sdk = _load_sdk(monkeypatch, instance.endpoint, "")
    with pytest.raises(ConnectionError, match="not available to this script"):
        sdk.tools.call("read", path="a")


def test_bridge_without_a_connection_answers_eio(monkeypatch):
    from pawflow_relay.tool_bridge import ENDPOINT_ENV, ToolBridge
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    instance = ToolBridge().start()
    try:
        sdk = _load_sdk(monkeypatch, instance.endpoint, "cap")
        with pytest.raises(RuntimeError, match="not connected"):
            sdk.tools.call("read", path="a")
    finally:
        instance.stop()


# ── callers: execute_script and the PFP runner ───────────────────────

class _ExecRelay:
    _service_id = "relayA"

    def __init__(self, fail=False, host_root="/home/u/work"):
        self.fail = fail
        self.envs = []
        self.writes = []
        self.commands = []
        self.deleted = []
        self._relay_info = {"platform": "linux", "host_root": host_root}

    def write_file(self, path, content, **kwargs):
        self.writes.append((path, content, kwargs))

    def delete_file(self, path, **kwargs):
        self.deleted.append((path, kwargs))

    def exec(self, _path, command, env=None, **kwargs):
        self.envs.append(dict(env or {}))
        self.commands.append((command, kwargs))
        if self.fail:
            raise RuntimeError("relay dropped")
        return {"stdout": "done", "stderr": "", "exit_code": 0}


def _run_execute_script(relay, **arguments):
    from core.handlers.web_execute import ExecuteScriptHandler
    handler = ExecuteScriptHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_agent_name("agentA")
    handler.set_fs_resolver(lambda _name: relay)
    return handler.execute({"code": "print(1)", "destination": "relayA",
                            **arguments})


@pytest.mark.parametrize("fail", [False, True])
def test_execute_script_passes_a_capability_and_revokes_it(fail):
    relay = _ExecRelay(fail=fail)
    out = _run_execute_script(relay)
    assert ("Error executing" in out) is fail
    token = relay.envs[0]["PAWFLOW_TOOL_CAP"]
    assert relay.envs[0]["PAWFLOW_AGENT_NAME"] == "agentA"
    with pytest.raises(PermissionError):
        script_capability.resolve(token, relay_service_id="relayA",
                                  user_id="alice")
    # The run directory (script + SDK) is removed even when the exec fails.
    assert [path for path, _ in relay.deleted] == [
        relay.writes[0][0].rsplit("/", 1)[0]]


def test_execute_script_ships_the_sdk_beside_the_script():
    relay = _ExecRelay()
    _run_execute_script(relay)
    (script, code, kw1), (sdk, sdk_bytes, kw2) = relay.writes
    run_dir = script.rsplit("/", 1)[0]
    assert script == f"{run_dir}/script.py" and code == b"print(1)"
    assert sdk == f"{run_dir}/pawflow.py"
    assert sdk_bytes == (ROOT / "docker" / "pawflow_sdk" / "pawflow.py").read_bytes()
    assert kw1 == kw2 == {}
    assert relay.commands == [(f"python3 {script}", {})]


@pytest.mark.parametrize("host_root,python", [
    ("D:/PawFlow/Ultima7", "python"), ("/home/u/work", "python3")])
def test_execute_script_local_runs_on_the_host(host_root, python):
    relay = _ExecRelay(host_root=host_root)
    _run_execute_script(relay, local=True)
    assert all(kw == {"local": True} for _p, _c, kw in relay.writes)
    command, kwargs = relay.commands[0]
    assert command.startswith(python + " ") and kwargs == {"local": True}
    assert relay.deleted[0][1] == {"local": True}
    assert "PAWFLOW_TOOL_CAP" in relay.envs[0]


def test_execute_script_local_needs_a_relay():
    from core.handlers.web_execute import ExecuteScriptHandler
    out = ExecuteScriptHandler().execute(
        {"code": "1", "destination": "sandbox", "local": True})
    assert out.startswith("Error: local=true needs a relay")


# ── end to end: a real script process, real bridges, real host helper ─

_E2E_SCRIPT = (
    "from pawflow import tools, fs\n"
    "hits = tools.call('grep', pattern='TODO')\n"
    "fs.read_file('a.txt')\n"
    "try:\n"
    "    tools.call('ask_user', question='?')\n"
    "except RuntimeError as exc:\n"
    "    print('refused:', 'cannot be called' in str(exc))\n"
    "print('result:', hits)\n"
)


def _write_run_dir(root):
    from core.handlers.web_execute import _sdk_source
    run = root / ".pawflow_exec_e2e"
    run.mkdir()
    (run / "script.py").write_text(_E2E_SCRIPT, encoding="utf-8")
    (run / "pawflow.py").write_bytes(_sdk_source())
    return run


def _assert_e2e(stdout, tool_relay, rows):
    assert "result: ran grep" in stdout
    assert "refused: True" in stdout
    assert [c[0] for c in tool_relay.calls] == ["grep", "read"]
    assert all(c[2:5] == ("alice", "conv1", "agentA") for c in tool_relay.calls)
    assert [msg["role"] for msg, _ in rows] == [
        "tool_call", "tool", "tool_call", "tool"]


def test_local_true_on_a_relay_host_reaches_the_server_through_the_worker(
        tmp_path, tool_relay, rows, monkeypatch):
    import socket
    import sys
    import threading

    from pawflow_relay import auth, tool_bridge
    from pawflow_relay._thread_host import _RelayHostHelperMixin

    # Worker side: its long-lived bridge, connected to the "server".
    worker = tool_bridge.ToolBridge().start(publish_env=False)
    worker.swap.set_inner(_ServerClient())
    monkeypatch.setattr(tool_bridge, "_worker_bridge", worker)

    # Host side: the real host-helper connection handler.
    class _Host(_RelayHostHelperMixin):
        pass
    host = _Host.__new__(_Host)
    host._host_helper_token = "helper-token"
    host.allow_local = host.allow_exec = True
    host.allow_service_tunnels = host.allow_remote_desktop = False
    host.directory = str(tmp_path)
    listener = socket.create_server(("127.0.0.1", 0))

    def _serve():
        conn, _ = listener.accept()
        with conn:
            host._handle_host_helper_conn(conn)

    threading.Thread(target=_serve, daemon=True).start()
    run = _write_run_dir(tmp_path)
    monkeypatch.setenv("PAWFLOW_HOST_HELPER_TOKEN", "helper-token")
    monkeypatch.delenv("PAWFLOW_TOOL_ENDPOINT", raising=False)
    try:
        result = auth.forward_to_host_helper(
            f"127.0.0.1:{listener.getsockname()[1]}",
            {"action": "exec", "path": ".",
             "command": f"{sys.executable} {run.name}/script.py",
             "env": {"PAWFLOW_TOOL_CAP": _mint()}},
            None, None)
    finally:
        listener.close()
        worker.stop()
    assert result["ok"] is True, result
    _assert_e2e(result["data"]["stdout"], tool_relay, rows)


def test_local_true_on_the_server_local_surface(tmp_path, tool_relay, rows,
                                                monkeypatch):
    import sys

    from services._server_local_exec import execute_server_local
    monkeypatch.delenv("PAWFLOW_TOOL_ENDPOINT", raising=False)
    run = _write_run_dir(tmp_path)
    result = execute_server_local(
        "exec", str(tmp_path),
        {"command": f"cd {tmp_path} && {sys.executable} {run.name}/script.py",
         "env": {"PAWFLOW_TOOL_CAP": _mint()}},
        relay_service_id="relayA", user_id="alice")
    _assert_e2e(result["stdout"], tool_relay, rows)


class _ServerLocalRelay(_ExecRelay):
    """A managed relay whose local=true runs on the real server-local surface."""

    def _server_local_requested(self, kwargs):
        return bool(kwargs.get("local"))

    def _local(self, action, path, request):
        from services._server_local_exec import execute_server_local
        return execute_server_local(action, path, request,
                                    relay_service_id="relayA", user_id="alice")

    def write_file(self, path, content, **kwargs):
        super().write_file(path, content, **kwargs)
        self._local("write_file", path, {"content": content})

    def delete_file(self, path, **kwargs):
        super().delete_file(path, **kwargs)
        self._local("delete_file", path, {})

    def exec(self, path, command, env=None, **kwargs):
        super().exec(path, command, env=env, **kwargs)
        return self._local("exec", path, {"command": command, "env": env})


def test_execute_script_local_on_the_server_local_surface(
        tmp_path, tool_relay, rows, monkeypatch):
    """The server-local root is "/": the run dir goes to the temp dir."""
    import tempfile
    from pathlib import Path
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("PAWFLOW_TOOL_ENDPOINT", raising=False)
    relay = _ServerLocalRelay()
    out = _run_execute_script(relay, local=True, code=_E2E_SCRIPT)
    run_dir = Path(relay.writes[0][0]).parent
    assert run_dir.parent == tmp_path and run_dir.name.startswith(".pawflow_exec_")
    _assert_e2e(out, tool_relay, rows)
    assert not run_dir.exists()


def test_a_script_on_the_wrong_relay_is_refused_end_to_end(
        tmp_path, tool_relay, rows, monkeypatch):
    import sys

    from services._server_local_exec import execute_server_local
    monkeypatch.delenv("PAWFLOW_TOOL_ENDPOINT", raising=False)
    run = _write_run_dir(tmp_path)
    result = execute_server_local(
        "exec", str(tmp_path),
        {"command": f"cd {tmp_path} && {sys.executable} {run.name}/script.py",
         "env": {"PAWFLOW_TOOL_CAP": _mint(relay_service_id="relayB")}},
        relay_service_id="relayA", user_id="alice")
    assert "Tool error" in result["stderr"]
    assert tool_relay.calls == [] and rows == []


def test_pfp_package_child_cannot_reach_the_bridge_directly(monkeypatch):
    from core.pfp_runtime import _RELAY_RUNNER
    namespace = {}
    # The runner ships as a source template; load it the way the relay runs it.
    exec(compile(_RELAY_RUNNER, "pfp_relay_runner.py", "exec"), namespace)  # noqa: S102  # nosec B102
    monkeypatch.setenv("PAWFLOW_TOOL_CAP", "secret")
    monkeypatch.setenv("PAWFLOW_TOOL_ENDPOINT", "127.0.0.1:1")
    monkeypatch.setenv("PAWFLOW_PFP_SDK_PATH", "runtime/sdk")
    env = namespace["_child_env"]()
    assert "PAWFLOW_TOOL_CAP" not in env
    assert "PAWFLOW_TOOL_ENDPOINT" not in env


def test_pfp_bridge_mints_for_its_relay():
    from core.pfp_runtime import RelayPackageRuntimeBridge
    env, token = RelayPackageRuntimeBridge._capability_env(
        {"context": {"user_id": "alice", "conversation_id": "conv1",
                     "agent_name": "agentA"}}, _Relay())
    assert env["PAWFLOW_TOOL_CAP"] == token
    assert script_capability.resolve(
        token, relay_service_id="relayA", user_id="alice").agent_name == "agentA"


def test_relay_connection_routes_tool_requests_to_the_bridge(
        tool_relay, rows, monkeypatch):
    """services/_relay_conn: a `tool.*` relay_request from relay A is answered
    by the bridge with relay A's identity, as a relay_response frame."""
    import asyncio
    import json

    from services import _relay_conn

    frames = []

    async def _send(_writer, data, opcode=0x01):
        frames.append((data, opcode))

    monkeypatch.setattr(_relay_conn, "_ws_send_frame", _send)

    class _Conn(_relay_conn._RelayConnMixin):
        pass
    conn = _Conn.__new__(_Conn)
    conn._service_id = "relayA"
    conn._user_id = "alice"
    msg = {"type": "relay_request", "request_id": "r1", "method": "tool.call",
           "args": {"cap": _mint(), "tool_name": "grep",
                    "arguments": {"pattern": "x"}}}
    asyncio.run(conn._handle_relay_request(msg, None, asyncio.Lock()))
    reply = json.loads(frames[-1][0])
    assert reply == {"type": "relay_response", "request_id": "r1",
                     "data": "ran grep"}
