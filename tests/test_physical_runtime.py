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


def test_private_command_maps_only_parent_ids_and_runs_own_init(monkeypatch):
    maps = {
        "/proc/self/uid_map": "0 100000 65536\n",
        "/proc/self/gid_map": "0 200000 65536\n",
    }
    monkeypatch.setattr(Path, "read_text", lambda self, **_k: maps[str(self)])
    command = runtime.private_command(["python3", "/opt/pawflow/worker.py"])
    for option in ("--user", "--mount", "--pid", "--fork", "--kill-child=SIGKILL",
                   "--mount-proc", "--setgroups=allow", "--map-users=0:0:65536",
                   "--map-groups=0:0:65536"):
        assert option in command
    assert command[-5:] == [
        "/usr/bin/tini", "--", "/usr/local/bin/init.sh", "python3", "/opt/pawflow/worker.py"]
    assert not any("100000" in argument or "200000" in argument for argument in command)


def test_missing_parent_mapping_cannot_launch_an_unrestricted_worker(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: "")
    with pytest.raises(ValueError, match="mapped"):
        runtime.private_command(["worker"])


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

    class Process:
        def wait(self):
            events.append("worker-waited")
            return 7

    def launch(command, **kwargs):
        events.append("application-launched")
        assert events[:3] == ["namespace-transferred", "namespace-closed", "control-closed"]
        assert kwargs["close_fds"] is True
        assert kwargs["env"]["HOME"] == "/home/pawflow"
        return Process()

    monkeypatch.setattr(runtime.socket, "socket", lambda **_k: Control())
    monkeypatch.setattr(runtime, "prepare_root", lambda _e: tmp_path)
    monkeypatch.setattr(runtime, "enter_root", lambda _r: None)
    monkeypatch.setattr(runtime, "private_command", lambda c: c)
    monkeypatch.setattr(runtime.os, "open", lambda *_a: 42)
    monkeypatch.setattr(runtime.os, "close", lambda _fd: events.append("namespace-closed"))
    monkeypatch.setattr(runtime.os, "chdir", lambda _p: None)
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    assert runtime.worker(12) == 7
