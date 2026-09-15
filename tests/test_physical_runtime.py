"""Private worker bootstrap invariants; mounted acceptance is separate."""

from pathlib import Path

import pytest

from pawflow_relay import _physical_runtime as runtime


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
