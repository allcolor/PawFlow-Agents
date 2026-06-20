"""Tests for the relay desktop lifecycle (_relay_desktop helpers + actions).

The X11/VNC stack binaries are absent in CI, so start_desktop is driven
with subprocess + readiness mocked to lock the process-arg invariants;
the health/cleanup/watchdog helpers are unit-tested with fake processes.
State is a SimpleNamespace carrying the desktop_* fields the functions
touch.
"""
import types

import pytest

from pawflow_relay import _relay_desktop as dt


def _state():
    return types.SimpleNamespace(
        desktop_procs=None, desktop_essential_procs=None,
        desktop_vnc_port=None, desktop_novnc_port=None, desktop_display=None,
        desktop_watchdog_stop=None, desktop_watchdog_thread=None,
        local_desktop_procs=None, local_desktop_vnc_port=None,
        local_desktop_novnc_port=None)


class FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False
        self.killed = False
        self.pid = 1000

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


def test_novnc_http_ready_false_without_port():
    assert dt.novnc_http_ready(_state(), port=0) is False


def test_desktop_is_healthy(monkeypatch):
    st = _state()
    assert dt.desktop_is_healthy(st) is False  # no procs
    monkeypatch.setattr(dt, "novnc_http_ready", lambda *a, **k: True)
    st.desktop_procs = [FakeProc(True), FakeProc(True)]
    st.desktop_essential_procs = st.desktop_procs
    assert dt.desktop_is_healthy(st) is True
    st.desktop_essential_procs = [FakeProc(False)]  # one dead
    assert dt.desktop_is_healthy(st) is False


def test_desktop_is_healthy_false_when_novnc_unreachable(monkeypatch):
    st = _state()
    monkeypatch.setattr(dt, "novnc_http_ready", lambda *a, **k: False)
    st.desktop_procs = [FakeProc(True)]
    st.desktop_essential_procs = st.desktop_procs
    assert dt.desktop_is_healthy(st) is False


def test_desktop_cleanup_terminates_and_clears(monkeypatch):
    st = _state()
    stop = types.SimpleNamespace(_set=False)
    stop.set = lambda: setattr(stop, "_set", True)
    procs = [FakeProc(True), FakeProc(True)]
    st.desktop_procs = procs
    st.desktop_watchdog_stop = stop
    st.desktop_novnc_port = 6080
    monkeypatch.setitem(dt.os.environ, "DISPLAY", ":99")
    dt.desktop_cleanup(st, "requested")
    assert all(p.terminated for p in procs)
    assert stop._set is True
    assert st.desktop_procs is None and st.desktop_novnc_port is None
    assert "DISPLAY" not in dt.os.environ


def test_start_desktop_arg_invariants(monkeypatch, tmp_path):
    calls = []

    def fake_popen(args, **kwargs):
        calls.append(list(args))
        return FakeProc(True)

    monkeypatch.setattr(dt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(dt, "novnc_http_ready", lambda *a, **k: True)
    monkeypatch.setattr(dt, "start_desktop_watchdog", lambda *a, **k: None)
    import shutil
    import time
    monkeypatch.setattr(shutil, "which", lambda _n: None)  # skip pulseaudio/autocutsel
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    st = _state()
    res = dt.start_desktop(st, {"resolution": "1280x800", "depth": 24,
                                "display": 99, "vnc_port": 5901, "novnc_port": 6080})
    assert res["ok"] is True
    assert res["data"]["vnc_port"] == 5901 and res["data"]["novnc_port"] == 6080
    assert st.desktop_procs is not None and st.desktop_display == ":99"
    assert len(st.desktop_essential_procs) == 3  # Xvfb, x11vnc, websockify

    flat = [a[0] for a in calls]
    assert "Xvfb" in flat and "x11vnc" in flat and "websockify" in flat
    xvfb = next(a for a in calls if a[0] == "Xvfb")
    assert ":99" in xvfb and "1280x800x24" in xvfb
    x11vnc = next(a for a in calls if a[0] == "x11vnc")
    assert "5901" in x11vnc
    ws = next(a for a in calls if a[0] == "websockify")
    assert "0.0.0.0:6080" in ws and "localhost:5901" in ws


def test_start_desktop_idempotent_when_healthy(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_vnc_port, st.desktop_novnc_port, st.desktop_display = 1, 2, ":99"
    monkeypatch.setattr(dt, "desktop_is_healthy", lambda _s: True)
    res = dt.start_desktop(st, {})
    assert res == {"ok": True, "data": {
        "vnc_port": 1, "novnc_port": 2, "display": ":99", "already_running": True}}


def test_stop_desktop(monkeypatch):
    st = _state()
    assert dt.stop_desktop(st) == {"ok": True, "data": {"was_running": False}}
    st.desktop_procs = [FakeProc(True)]
    monkeypatch.setattr(dt, "desktop_cleanup", lambda *a, **k: None)
    assert dt.stop_desktop(st) == {"ok": True}


def test_stop_local_desktop():
    st = _state()
    assert dt.stop_local_desktop(st) == {"ok": True, "data": {"was_running": False}}
    procs = [FakeProc(True)]
    st.local_desktop_procs = procs
    assert dt.stop_local_desktop(st) == {"ok": True}
    assert st.local_desktop_procs is None and procs[0].terminated


def test_local_screen_check_returns_platform_and_flag():
    res = dt.local_screen_check(allow_local_screen=True)
    assert res["ok"] is True
    assert "platform" in res["data"]
    assert res["data"]["allow_local_screen"] is True
    assert "ready" in res["data"]
