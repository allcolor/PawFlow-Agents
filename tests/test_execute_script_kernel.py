"""Persistent kernel behind execute_script(persistent=true).

The kernel (docker/pawflow_sdk/pawflow_kernel.py) runs here as a real
detached process, started by a real client process, exactly as the relay
runs them; only the temp dir is redirected to the test's tmp_path.
"""
import json
import os
import shutil
import socket
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

import pytest

from core import script_capability

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "docker" / "pawflow_sdk"
KEY = "testkernel0001"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX temp dir redirect")


def _load_kernel_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"pawflow_kernel_under_test_{time.time_ns()}", SDK / "pawflow_kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path):
    """A run dir like execute_script writes, and a private temp dir."""
    run = tmp_path / "run"
    run.mkdir()
    for name in ("pawflow.py", "pawflow_kernel.py"):
        shutil.copyfile(SDK / name, run / name)
    temp = tmp_path / "tmp"
    temp.mkdir()
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("PAWFLOW_")}
    base["TMPDIR"] = str(temp)
    yield {"run": run, "temp": temp, "env": base, "cwd": tmp_path}
    state_file = temp / "pawflow-kernels" / KEY / "state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text())
        try:
            os.kill(state["pid"], 9)
        except OSError:
            pass


def _client(env, code, *, restart=False, extra_env=None, idle=600, popen=False):
    script = env["run"] / f"script_{time.time_ns()}.py"
    script.write_text(code, encoding="utf-8")
    argv = [sys.executable, str(env["run"] / "pawflow_kernel.py"), "client",
            "--key", KEY, "--idle", str(idle)]
    if restart:
        argv.append("--restart")
    argv.append(str(script))
    run_env = {**env["env"], **(extra_env or {})}
    if popen:
        return subprocess.Popen(argv, env=run_env, cwd=env["cwd"],  # nosec B603
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    # The kernel must not hold the client's pipes, or this would never return.
    return subprocess.run(argv, env=run_env, cwd=env["cwd"],  # nosec B603
                          capture_output=True, text=True, timeout=60)


def _state(env):
    path = env["temp"] / "pawflow-kernels" / KEY / "state.json"
    return json.loads(path.read_text())


def test_state_survives_between_calls_and_bare_expressions_print(env):
    first = _client(env, "x = 41\nimport json")
    assert first.returncode == 0, first.stderr
    assert "[pawflow kernel] new kernel" in first.stdout
    second = _client(env, "y = json.dumps(x + 1)\ny")
    assert second.returncode == 0, second.stderr
    assert second.stdout == "'42'\n"
    state = _state(env)
    assert oct(os.stat(env["temp"] / "pawflow-kernels" / KEY / "state.json")
               .st_mode & 0o777) == "0o600"
    assert state["pid"] != os.getpid()


def test_restart_gives_a_fresh_namespace(env):
    _client(env, "x = 1")
    out = _client(env, "'x' in globals()", restart=True)
    assert "restarted on request" in out.stdout
    assert out.stdout.strip().endswith("False")


def test_errors_show_only_the_callers_frames_and_keep_the_kernel(env):
    _client(env, "x = 5")
    out = _client(env, "def f():\n    return 1 / 0\nf()")
    assert out.returncode == 1
    assert "ZeroDivisionError" in out.stderr and "<call 2>" in out.stderr
    assert "pawflow_kernel.py" not in out.stderr
    assert _client(env, "x").stdout == "5\n"
    syntax = _client(env, "def (")
    assert syntax.returncode == 1 and "SyntaxError" in syntax.stderr


def test_call_environment_exists_only_during_its_call(env):
    _client(env, "pass")
    code = "import os\nos.environ.get('PAWFLOW_TOOL_CAP')"
    assert _client(env, code, extra_env={"PAWFLOW_TOOL_CAP": "c1"}).stdout \
        == "'c1'\n"
    assert _client(env, code).stdout == ""


def test_tool_calls_use_the_capability_of_each_call(env):
    from pawflow_relay.tool_bridge import ToolBridge
    bridge = ToolBridge(lambda req: {
        "data": f"cap={req['cap']} tool={req['tool_name']}"}).start(
            publish_env=False)
    try:
        code = "from pawflow import tools\nprint(tools.call('grep', pattern='x'))"
        for cap in ("c1", "c2"):
            out = _client(env, code, extra_env={
                "PAWFLOW_TOOL_CAP": cap, "PAWFLOW_TOOL_ENDPOINT": bridge.endpoint})
            assert out.returncode == 0, out.stderr
            assert f"cap={cap} tool=grep" in out.stdout
        # Without a capability the SDK refuses instead of reusing the last one.
        out = _client(env, code)
        assert out.returncode == 1 and "cap=c2" not in out.stdout
    finally:
        bridge.stop()


def test_a_killed_client_interrupts_its_call(env):
    _client(env, "x = 3")
    slow = _client(env, "import time\ntime.sleep(60)", popen=True)
    state = _state(env)
    deadline = time.time() + 15
    while time.time() < deadline:
        with socket.create_connection(("127.0.0.1", state["port"])) as conn:
            conn.sendall((json.dumps({"secret": state["secret"], "op": "ping"})
                          + "\n").encode())
            if json.loads(conn.makefile().readline()).get("busy"):
                break
        time.sleep(0.1)
    slow.kill()
    slow.wait()
    started = time.time()
    out = _client(env, "x")
    assert out.stdout == "3\n" and time.time() - started < 15


def test_a_busy_kernel_refuses_instead_of_waiting_forever(env, monkeypatch):
    _client(env, "pass")
    slow = _client(env, "import time\ntime.sleep(30)", popen=True)
    try:
        time.sleep(1.5)
        out = _client(env, "1")
        assert out.returncode == 4
        assert "busy with another call" in out.stderr
    finally:
        slow.kill()
        slow.wait()


def test_an_sdk_update_restarts_the_kernel(env):
    _client(env, "x = 1")
    with open(env["run"] / "pawflow.py", "a", encoding="utf-8") as fh:
        fh.write("\n# updated\n")
    out = _client(env, "'x' in globals()")
    assert "the PawFlow SDK was updated" in out.stdout
    assert out.stdout.strip().endswith("False")


def test_a_bad_secret_is_ignored(env):
    _client(env, "x = 1")
    state = _state(env)
    with socket.create_connection(("127.0.0.1", state["port"])) as conn:
        conn.sendall(b'{"secret": "nope", "op": "shutdown"}\n')
        assert conn.makefile().readline() == ""
    assert _client(env, "x").stdout == "1\n"


def test_idle_timeout_stops_the_kernel(tmp_path, monkeypatch):
    kernel_mod = _load_kernel_module()
    monkeypatch.setattr(kernel_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    kernel = kernel_mod.Kernel(KEY, 60, "h")
    kernel.idle_seconds = 0.2

    class _Stopped(Exception):
        pass

    def _exit():
        raise _Stopped
    kernel.exit = _exit
    started = time.monotonic()
    with pytest.raises(_Stopped):
        kernel.run_forever()
    assert time.monotonic() - started < 5


def test_idle_bounds_match_the_handler():
    from core.handlers import web_execute
    kernel_mod = _load_kernel_module()
    assert kernel_mod.MAX_IDLE_SECONDS == web_execute.KERNEL_MAX_IDLE_MINUTES * 60
    assert kernel_mod.DEFAULT_IDLE_SECONDS == (
        web_execute.KERNEL_DEFAULT_IDLE_MINUTES * 60)
    assert kernel_mod.clamp_idle(10 ** 9) == kernel_mod.MAX_IDLE_SECONDS
    assert kernel_mod.clamp_idle("x") == kernel_mod.DEFAULT_IDLE_SECONDS


# ── execute_script handler ───────────────────────────────────────────

class _Relay:
    _service_id = "relayA"

    def __init__(self):
        self.writes, self.commands, self.envs = [], [], []
        self._relay_info = {"platform": "linux", "host_root": "/w"}

    def write_file(self, path, content, **kwargs):
        self.writes.append((path, content, kwargs))

    def delete_file(self, path, **kwargs):
        pass

    def exec(self, _path, command, env=None, **kwargs):
        self.commands.append((command, kwargs))
        self.envs.append(dict(env or {}))
        return {"stdout": "ok", "stderr": "", "exit_code": 0}


def _execute(relay, conv="conv1", agent="agentA", **arguments):
    from core.handlers.web_execute import ExecuteScriptHandler
    handler = ExecuteScriptHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id(conv)
    handler.set_agent_name(agent)
    handler.set_fs_resolver(lambda _name: relay)
    return handler.execute({"code": "x = 1", "destination": "relayA",
                            **arguments})


def _key(command):
    parts = command.split()
    return parts[parts.index("--key") + 1]


def test_persistent_runs_the_kernel_client_with_its_options():
    relay = _Relay()
    assert _execute(relay, persistent=True, restart=True,
                    idle_timeout_minutes=90) == "ok"
    paths = [p.rsplit("/", 1)[1] for p, _c, _k in relay.writes]
    assert paths == ["script.py", "pawflow.py", "pawflow_kernel.py"]
    assert relay.writes[2][1] == (SDK / "pawflow_kernel.py").read_bytes()
    command, _ = relay.commands[0]
    run_dir = relay.writes[0][0].rsplit("/", 1)[0]
    assert command == (
        f"python3 {run_dir}/pawflow_kernel.py client --key {_key(command)} "
        f"--idle 5400 --restart {run_dir}/script.py")
    assert script_capability.CAP_ENV in relay.envs[0]


def test_one_kernel_per_surface_conversation_and_agent():
    def key(**kw):
        relay = _Relay()
        _execute(relay, persistent=True, **kw)
        return _key(relay.commands[0][0])
    base = key()
    assert base == key() and len(base) == 24 and base.isalnum()
    assert len({base, key(local=True), key(conv="conv2"),
                key(agent="agentB")}) == 4


@pytest.mark.parametrize("arguments,message", [
    ({"restart": True}, "need persistent=true"),
    ({"idle_timeout_minutes": 5}, "need persistent=true"),
    ({"persistent": True, "idle_timeout_minutes": 0}, "between 1 and 480"),
    ({"persistent": True, "idle_timeout_minutes": 481}, "between 1 and 480"),
    ({"persistent": True, "idle_timeout_minutes": "soon"}, "must be an integer"),
    ({"persistent": True, "destination": "sandbox"}, "needs a relay"),
])
def test_invalid_kernel_options_are_refused(arguments, message):
    relay = _Relay()
    out = _execute(relay, **arguments)
    assert out.startswith("Error:") and message in out
    assert relay.commands == []


def test_without_persistent_nothing_changes():
    relay = _Relay()
    _execute(relay)
    assert [p.rsplit("/", 1)[1] for p, _c, _k in relay.writes] == [
        "script.py", "pawflow.py"]
    assert "pawflow_kernel" not in relay.commands[0][0]


def test_schema_and_description_teach_the_kernel():
    from core.handlers.web_execute import ExecuteScriptHandler
    handler = ExecuteScriptHandler()
    props = handler.parameters_schema["properties"]
    assert {"persistent", "restart", "idle_timeout_minutes"} <= set(props)
    assert "Persistent kernel" in handler.description
    assert "2,000,000" in handler.description


# ── result size for script calls ──────────────────────────────────────

def test_script_calls_get_large_results_in_full():
    from core.tool_handler import ToolHandler
    from core.tool_registry import ToolRegistry, result_cap_override

    class _Big(ToolHandler):
        name = "big"
        description = "big"
        parameters_schema = {"type": "object", "properties": {}}

        def execute(self, arguments):
            return "x" * 120_000

    registry = ToolRegistry()
    registry.register(_Big())
    assert len(registry.execute("big", {})) < 120_000
    with result_cap_override(script_capability.SCRIPT_RESULT_MAX_CHARS):
        assert registry.execute("big", {}) == "x" * 120_000
    # The override never outlives its block.
    assert len(registry.execute("big", {})) < 120_000
