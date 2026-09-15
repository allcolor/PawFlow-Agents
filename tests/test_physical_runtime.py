"""Private worker bootstrap invariants; mounted acceptance is separate."""

import importlib.util
import json
from pathlib import Path

import pytest

from pawflow_relay import _physical_runtime as runtime


def test_group_seccomp_allows_only_privileged_pivot_without_opening_other_syscalls():
    profile = json.loads(runtime.SECCOMP_PROFILE.read_text(encoding="utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert profile["defaultErrnoRet"] == 1
    pivot = [rule for rule in profile["syscalls"] if "pivot_root" in rule["names"]]
    assert len(pivot) == 1
    assert pivot[0]["names"] == ["pivot_root"]
    assert pivot[0]["action"] == "SCMP_ACT_ALLOW"
    assert pivot[0]["includes"] == {"caps": ["CAP_SYS_ADMIN"]}
    for rule in profile["syscalls"]:
        if rule["action"] != "SCMP_ACT_ALLOW":
            continue
        assert "keyctl" not in rule["names"]
        if "reboot" in rule["names"]:
            assert rule["includes"] == {"caps": ["CAP_SYS_BOOT"]}
        if "open_by_handle_at" in rule["names"]:
            assert rule["includes"] == {"caps": ["CAP_DAC_READ_SEARCH"]}


def test_cli_binary_collects_the_physical_seccomp_data(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "physical_relay_cli_builder", root / "scripts/build-relay-cli-installer.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "DIST_ROOT", tmp_path / "dist")
    monkeypatch.setattr(builder, "BUILD_ROOT", tmp_path / "build")
    monkeypatch.setattr(builder, "ensure_pyinstaller", lambda _python: None)
    calls = []

    def build(command, **_kwargs):
        calls.append(command)
        output = Path(command[command.index("--distpath") + 1])
        (output / builder.executable_name()).touch()

    monkeypatch.setattr(builder, "_run", build)
    builder.build_binary("python3", "acceptance-test")
    command = calls[0]
    assert command[command.index("--collect-data") + 1] == "pawflow_relay"


def test_pivot_detaches_old_root_before_worker_execution(tmp_path, monkeypatch):
    events = []
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(runtime.os, "chdir", lambda path: events.append(("chdir", str(path))))
    monkeypatch.setattr(runtime.os, "rmdir", lambda path: events.append(("rmdir", path)))
    monkeypatch.setattr(runtime, "_run", lambda *args: events.append(args))
    runtime.enter_root(root)
    assert events == [
        ("chdir", str(root)), ("pivot_root", ".", ".physical-old-root"),
        ("chdir", "/"), ("umount", "-l", "/.physical-old-root"),
        ("rmdir", "/.physical-old-root"),
    ]


def test_private_command_waits_for_parent_mapping_without_subid_helpers():
    command = runtime.private_command(17)
    for option in ("--user", "--mount", "--pid", "--fork", "--kill-child=SIGKILL",
                   "--mount-proc", "--setgroups=allow"):
        assert option in command
    assert not any(option.startswith(("--map-users", "--map-groups")) for option in command)
    assert command[-3:] == [runtime.SCRIPT, "--private-fd", "17"]


def test_missing_parent_mapping_cannot_launch_an_unrestricted_worker(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: "")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: pytest.fail("unexpected launch"))
    with pytest.raises(ValueError, match="mapped"):
        runtime.run_private(["worker"], {})


def test_child_removes_all_control_descriptors_before_application_launch(monkeypatch, tmp_path):
    events = []
    export = {"command": ["worker"], "environment": {"TEST_SETTING": "value"}}

    class Control:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append("control-closed")

        def settimeout(self, _timeout):
            pass

        def recv(self, size):
            if size == 16:
                return b"start"
            import json
            return json.dumps(export).encode()

        def sendmsg(self, *_args):
            events.append("namespace-transferred")

    def launch(command, environment):
        events.append("application-launched")
        assert events[:3] == ["namespace-transferred", "namespace-closed", "control-closed"]
        assert command == ["worker"]
        assert environment["HOME"] == "/home/pawflow"
        return 7

    monkeypatch.setattr(runtime.socket, "socket", lambda **_k: Control())
    monkeypatch.setattr(runtime, "prepare_root", lambda _e: tmp_path)
    monkeypatch.setattr(runtime, "enter_root", lambda _r: None)
    monkeypatch.setattr(runtime.os, "open", lambda *_a: 42)
    monkeypatch.setattr(runtime.os, "close", lambda _fd: events.append("namespace-closed"))
    monkeypatch.setattr(runtime.os, "chdir", lambda _p: None)
    monkeypatch.setattr(runtime, "run_private", launch)
    assert runtime.worker(12) == 7


def test_identity_maps_preserve_all_parent_ranges_and_their_holes(monkeypatch):
    maps = {
        "/proc/self/uid_map": "0 100000 1000\n1001 200000 32\n",
        "/proc/self/gid_map": "0 300000 65536\n",
    }
    monkeypatch.setattr(Path, "read_text", lambda self, **_k: maps[str(self)])
    assert runtime.identity_maps() == {
        "uid": "0 0 1000\n1001 1001 32\n",
        "gid": "0 0 65536\n",
    }


@pytest.fixture
def private_launch(monkeypatch):
    events = []
    mappings = {"uid": "0 0 4294967295\n", "gid": "0 0 65536\n"}
    monkeypatch.setattr(runtime, "identity_maps", lambda: mappings)
    monkeypatch.setattr(Path, "write_text", lambda path, text, **_k:
                        events.append(("map", str(path), text)))

    class Control:
        def __init__(self, name):
            self.name = name

        def fileno(self):
            return 21

        def settimeout(self, _timeout):
            pass

        def close(self):
            events.append(("close", self.name))

        def recv(self, _size):
            events.append(("ready",))
            return b"user-ready"

        def sendall(self, payload):
            events.append(("start", json.loads(payload)))

    class Process:
        pid = 42
        status = None

        def poll(self):
            events.append(("poll",))
            return self.status

        def wait(self, timeout=None):
            events.append(("wait", timeout))
            if self.status is None:
                self.status = 7
            return self.status

        def kill(self):
            events.append(("kill",))
            self.status = -9

    parent, child, process = Control("parent"), Control("child"), Process()
    monkeypatch.setattr(runtime.socket, "socketpair", lambda *_a: (parent, child))

    def launch(command, **kwargs):
        events.append(("launch",))
        assert command == runtime.private_command(21)
        assert kwargs["pass_fds"] == (21,)
        assert kwargs["close_fds"] is True
        assert kwargs["env"] == {"HOME": "/home/pawflow"}
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    return events, parent


def test_parent_maps_unreaped_child_before_releasing_application(private_launch):
    events, _parent = private_launch
    assert runtime.run_private(["worker"], {"HOME": "/home/pawflow"}) == 7
    assert events[:7] == [
        ("launch",), ("close", "child"), ("ready",),
        ("map", "/proc/42/uid_map", "0 0 4294967295\n"),
        ("map", "/proc/42/gid_map", "0 0 65536\n"),
        ("start", ["worker"]), ("close", "parent"),
    ]
    assert events.index(("poll",)) > events.index(("wait", None))
    assert ("kill",) not in events


@pytest.mark.parametrize("kind", ["uid", "gid"])
def test_map_failure_kills_and_reaps_without_releasing_application(private_launch, monkeypatch, kind):
    events, _parent = private_launch
    write = Path.write_text

    def deny(path, text, **kwargs):
        if path.name == kind + "_map":
            raise PermissionError("injected mapping refusal")
        return write(path, text, **kwargs)

    monkeypatch.setattr(Path, "write_text", deny)
    with pytest.raises(PermissionError, match="mapping refusal"):
        runtime.run_private(["worker"], {"HOME": "/home/pawflow"})
    assert not any(event[0] == "start" for event in events)
    assert events[-2:] == [("kill",), ("wait", 10)]
    assert ("close", "parent") in events


def test_missing_namespace_ready_never_writes_maps(private_launch, monkeypatch):
    events, parent = private_launch
    monkeypatch.setattr(parent, "recv", lambda _size: b"")
    with pytest.raises(RuntimeError, match="before user namespace mapping"):
        runtime.run_private(["worker"], {"HOME": "/home/pawflow"})
    assert not any(event[0] in ("map", "start") for event in events)
    assert events[-2:] == [("kill",), ("wait", 10)]


def test_private_spawn_failure_closes_both_control_endpoints(private_launch, monkeypatch):
    events, _parent = private_launch

    def fail(*_args, **_kwargs):
        raise OSError("injected spawn failure")

    monkeypatch.setattr(runtime.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="spawn failure"):
        runtime.run_private(["worker"], {"HOME": "/home/pawflow"})
    assert events == [("close", "child"), ("close", "parent")]


@pytest.mark.parametrize("payload", [b'["worker", "--option"]', b""])
def test_private_init_requires_mapping_ack_and_closes_control_before_exec(monkeypatch, payload):
    events = []

    class Control:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append("closed")

        def settimeout(self, _timeout):
            pass

        def sendall(self, data):
            assert data == b"user-ready"

        def recv(self, _size):
            return payload

    def execute(path, command):
        assert events == ["closed"]
        assert path == "/usr/bin/tini"
        assert command == [path, "--", "/usr/local/bin/init.sh", "worker", "--option"]
        events.append("executed")

    monkeypatch.setattr(runtime.socket, "socket", lambda **_k: Control())
    monkeypatch.setattr(runtime.os, "execv", execute)
    if payload:
        runtime.private_init(21)
        assert events == ["closed", "executed"]
    else:
        with pytest.raises(json.JSONDecodeError):
            runtime.private_init(21)
        assert events == ["closed"]
