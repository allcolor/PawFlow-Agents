"""Tests for pawflow_relay._relay_children (spawn_relay / stop_relay).

First execution coverage of the child-relay lifecycle: directory validation,
thread spawn + _ws_connect invocation, the Docker-container path (run argv,
_DOCKER_CONTAINERS registration, cleanup), and stop bookkeeping. _ws_connect
is stubbed so no real connection is attempted.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

# tools/ on path for the lazy `from fs_common import ...` / `import fs_actions`
# the Docker path performs, mirroring the relay container layout.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay import _relay_children as rc


def _cfg():
    return rc.ChildRelayConfig(
        url="wss://srv/ws/relay", token="tok", secret="sec",
        readonly=False, allow_exec=True, allow_automation=False,
        allow_local_screen=False, allow_local=False)


def _no_docker():
    return rc.DockerEnv(parent_docker=False, cpus="2", memory="4g")


class _Collector:
    def __init__(self):
        self.frames = []

    def __call__(self, frame):
        self.frames.append(json.loads(frame.decode("utf-8")))


def test_spawn_rejects_missing_directory():
    mgr = rc.ChildRelayManager()
    out = _Collector()
    mgr.handle_spawn(
        {"root": "/no/such/dir/xyz", "relay_id": "c1", "request_id": "r1"},
        _cfg(), _no_docker(), out)
    assert out.frames[0]["data"]["ok"] is False
    assert "Directory not found" in out.frames[0]["data"]["error"]
    assert mgr.children == {}


def test_spawn_starts_child_and_calls_ws_connect(monkeypatch, tmp_path):
    seen = {}
    done = threading.Event()

    def fake_ws_connect(url, tok, sec, rid, root, **kw):
        seen["args"] = (url, tok, sec, rid, root)
        seen["kw"] = kw
        done.set()

    # _child_relay imports _ws_connect lazily from pawflow_relay.worker;
    # inject a stub module so no real connection is attempted.
    fake_worker = types.ModuleType("pawflow_relay.worker")
    fake_worker._ws_connect = fake_ws_connect
    monkeypatch.setitem(sys.modules, "pawflow_relay.worker", fake_worker)

    mgr = rc.ChildRelayManager()
    out = _Collector()
    mgr.handle_spawn(
        {"root": str(tmp_path), "relay_id": "c2", "request_id": "r2",
         "token": "ctok", "secret": "csec"},
        _cfg(), _no_docker(), out)

    assert "c2" in mgr.children
    assert done.wait(5), "child thread did not call _ws_connect"
    assert seen["args"] == ("wss://srv/ws/relay", "ctok", "csec", "c2", str(tmp_path))
    assert seen["kw"]["allow_exec"] is True
    assert seen["kw"]["readonly"] is False
    # ok result emitted
    ok = [f for f in out.frames if f["data"].get("ok")]
    assert ok and ok[0]["data"]["relay_id"] == "c2"


def test_spawn_docker_path_runs_container_and_cleans_up(monkeypatch, tmp_path):
    import fs_actions as fsa
    runs = []
    done = threading.Event()

    class _R:
        returncode = 0

    def fake_run(argv, **kw):
        runs.append(argv)
        return _R()

    def fake_ws_connect(*a, **k):
        # container must be registered while the child is "connected"
        assert str(tmp_path.resolve()) in fsa._DOCKER_CONTAINERS
        done.set()

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    monkeypatch.setattr("fs_common._docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("fs_common._to_host_path", lambda p: p)
    monkeypatch.setattr("fs_common._translate_path", lambda p: p)
    fake_worker = types.ModuleType("pawflow_relay.worker")
    fake_worker._ws_connect = fake_ws_connect
    monkeypatch.setitem(sys.modules, "pawflow_relay.worker", fake_worker)

    mgr = rc.ChildRelayManager()
    out = _Collector()
    mgr.handle_spawn(
        {"root": str(tmp_path), "relay_id": "c3", "request_id": "r3"},
        _cfg(), rc.DockerEnv(parent_docker=True, cpus="2", memory="4g"), out)

    mgr.children["c3"].join(5)
    assert done.is_set()
    # docker run then docker rm -f
    assert any(a[:3] == ["docker", "run", "-d"] for a in runs)
    assert any(a[:3] == ["docker", "rm", "-f"] for a in runs)
    # container unregistered after the child exits
    assert str(tmp_path.resolve()) not in fsa._DOCKER_CONTAINERS


def test_stop_pops_child_and_replies():
    mgr = rc.ChildRelayManager()
    mgr.children["c9"] = "thread-handle"
    out = _Collector()
    mgr.handle_stop({"relay_id": "c9", "request_id": "r9"}, out)
    assert "c9" not in mgr.children
    assert out.frames[0]["data"] == {"ok": True, "stopped": "c9"}
