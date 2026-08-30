"""Tests for the relay desktop lifecycle (_relay_desktop helpers + actions).

The X11/VNC stack binaries are absent in CI, so start_desktop is driven
with subprocess + readiness mocked to lock the process-arg invariants;
the health/cleanup/watchdog helpers are unit-tested with fake processes.
State is a SimpleNamespace carrying the desktop_* fields the functions
touch.
"""
import base64
import types


from pawflow_relay import _relay_desktop as dt


def _state():
    return types.SimpleNamespace(
        desktop_procs=None, desktop_essential_procs=None,
        desktop_vnc_port=None, desktop_novnc_port=None, desktop_display=None,
        desktop_watchdog_stop=None, desktop_watchdog_thread=None,
        desktop_session_id=None, desktop_started_at=None,
        local_desktop_procs=None, local_desktop_vnc_port=None,
        local_desktop_novnc_port=None,
        local_desktop_session_id=None, local_desktop_started_at=None)


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


def test_novnc_asset_reads_relay_runtime(monkeypatch, tmp_path):
    root = tmp_path / "novnc"
    (root / "app").mkdir(parents=True)
    (root / "vnc.html").write_text("<html>relay UI</html>", encoding="utf-8")
    (root / "app" / "ui.js").write_text("export default {};", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_NOVNC_WEB", str(root))

    html = dt.novnc_asset({"path": "vnc.html"})
    assert html["ok"] is True
    assert base64.b64decode(html["data"]["body"]) == b"<html>relay UI</html>"
    assert html["data"]["content_type"] == "text/html"

    js = dt.novnc_asset({"path": "app/ui.js"})
    assert js["ok"] is True
    assert js["data"]["content_type"] in {
        "text/javascript", "application/javascript"}


def test_novnc_asset_rejects_non_ui_and_traversal_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_NOVNC_WEB", str(tmp_path))
    assert dt.novnc_asset({"path": "../../etc/passwd"})["ok"] is False
    assert dt.novnc_asset({"path": "secrets.txt"})["ok"] is False


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
    st.desktop_session_id, st.desktop_started_at = "sess-live", 1234.5
    monkeypatch.setattr(dt, "desktop_is_healthy", lambda _s: True)
    res = dt.start_desktop(st, {})
    assert res["ok"] is True
    assert res["data"]["already_running"] is True
    assert res["data"]["vnc_port"] == 1 and res["data"]["novnc_port"] == 2
    # The healthy branch reports the EXISTING session, never a new one.
    assert res["data"]["session_id"] == "sess-live"
    assert res["data"]["started_at"] == 1234.5


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


# ── Desktop session identity (WS7 / plan §11, §12) ──────────────────

def test_start_desktop_mints_session_id(monkeypatch):
    def fake_popen(args, **kwargs):
        return FakeProc(True)

    monkeypatch.setattr(dt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(dt, "novnc_http_ready", lambda *a, **k: True)
    monkeypatch.setattr(dt, "start_desktop_watchdog", lambda *a, **k: None)
    import shutil
    import time
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    st = _state()
    res = dt.start_desktop(st, {"vnc_port": 5901, "novnc_port": 6080})
    assert res["ok"] is True
    sid = res["data"]["session_id"]
    assert sid and len(sid) >= 16
    assert st.desktop_session_id == sid
    assert st.desktop_started_at and res["data"]["started_at"] == st.desktop_started_at


def test_desktop_status_reports_session_identity(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_essential_procs = st.desktop_procs
    st.desktop_session_id, st.desktop_started_at = "sess-abc", 111.0
    st.desktop_novnc_port = 6080
    monkeypatch.setattr(dt, "novnc_http_ready", lambda *a, **k: True)
    data = dt.desktop_status(st)["data"]
    assert data["running"] is True
    assert data["session_id"] == "sess-abc"
    assert data["started_at"] == 111.0


def test_desktop_status_omits_session_when_stopped():
    st = _state()
    data = dt.desktop_status(st)["data"]
    assert data["running"] is False
    assert not data.get("session_id")


def test_desktop_cleanup_clears_session_identity(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id, st.desktop_started_at = "sess-x", 1.0
    dt.desktop_cleanup(st, "requested")
    assert st.desktop_session_id is None
    assert st.desktop_started_at is None


def test_stop_desktop_stale_session_conflicts(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id = "sess-current"
    cleaned = []
    monkeypatch.setattr(dt, "desktop_cleanup",
                        lambda *a, **k: cleaned.append(a))
    res = dt.stop_desktop(st, {"session_id": "sess-old"})
    # A conflict is a DATA answer (ok stays true): the transport strips
    # ok:false envelopes, so the refusal must live inside `data`.
    assert res["ok"] is True
    assert res["data"]["stopped"] is False
    assert res["data"]["conflict"] is True
    assert res["data"]["current_session_id"] == "sess-current"
    assert not cleaned  # a stale confirmation must never stop the newer session


def test_stop_desktop_exact_session_stops(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id = "sess-current"
    cleaned = []
    monkeypatch.setattr(dt, "desktop_cleanup",
                        lambda *a, **k: cleaned.append(a))
    res = dt.stop_desktop(st, {"session_id": "sess-current"})
    assert res == {"ok": True}
    assert cleaned


def test_stop_desktop_without_session_keeps_legacy_behavior(monkeypatch):
    # No session_id in the request = unconditional stop (existing callers).
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id = "sess-current"
    monkeypatch.setattr(dt, "desktop_cleanup", lambda *a, **k: None)
    assert dt.stop_desktop(st) == {"ok": True}


def test_start_local_desktop_mints_session_id(monkeypatch):
    import shutil
    monkeypatch.setattr(dt.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/" + _n)
    monkeypatch.setattr(dt.subprocess, "Popen",
                        lambda *a, **k: FakeProc(True))
    import time
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    st = _state()
    res = dt.start_local_desktop(st, {"novnc_port": 6090})
    assert res["ok"] is True
    assert res["data"]["session_id"] == st.local_desktop_session_id
    assert st.local_desktop_session_id
    status = dt.desktop_status(st)["data"]
    assert status["local_screen_session_id"] == st.local_desktop_session_id
    assert dt.stop_local_desktop(st) == {"ok": True}
    assert st.local_desktop_session_id is None


def _server_transport_unwrap(envelope):
    """Reproduce the REAL wire contract between relay and server.

    The relay message loop forwards ``result.get("data", result)`` and the
    server-side ``_request`` raises when the unwrapped dict carries
    ``ok: false`` (services/_filesystem_ops.py). Every relay-result shape
    the server relies on must be asserted THROUGH this function.
    """
    data = envelope.get("data", envelope)
    if isinstance(data, dict) and data.get("ok") is False:
        raise Exception(data.get("error", "Relay error"))
    return data


def test_stop_conflict_survives_the_transport(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id = "sess-current"
    monkeypatch.setattr(dt, "desktop_cleanup", lambda *a, **k: None)
    seen = _server_transport_unwrap(
        dt.stop_desktop(st, {"session_id": "sess-old"}))
    # The server must still see the conflict and the current session.
    assert seen["conflict"] is True
    assert seen["stopped"] is False
    assert seen["current_session_id"] == "sess-current"


def test_stop_success_survives_the_transport(monkeypatch):
    st = _state()
    st.desktop_procs = [FakeProc(True)]
    st.desktop_session_id = "sess-current"
    monkeypatch.setattr(dt, "desktop_cleanup", lambda *a, **k: None)
    seen = _server_transport_unwrap(
        dt.stop_desktop(st, {"session_id": "sess-current"}))
    assert not (isinstance(seen, dict) and seen.get("conflict"))


def test_stop_local_desktop_stale_session_conflicts():
    st = _state()
    procs = [FakeProc(True)]
    st.local_desktop_procs = procs
    st.local_desktop_session_id = "host-current"
    seen = _server_transport_unwrap(
        dt.stop_local_desktop(st, {"session_id": "host-old"}))
    assert seen["conflict"] is True
    assert seen["current_session_id"] == "host-current"
    assert st.local_desktop_procs is procs  # nothing was stopped
    assert not procs[0].terminated


def test_stop_local_desktop_exact_session_stops():
    st = _state()
    procs = [FakeProc(True)]
    st.local_desktop_procs = procs
    st.local_desktop_session_id = "host-current"
    assert dt.stop_local_desktop(
        st, {"session_id": "host-current"}) == {"ok": True}
    assert procs[0].terminated
    assert st.local_desktop_session_id is None


def test_host_helper_stop_compares_session():
    from pawflow_relay import _thread_host
    import types as _types

    fake = _types.SimpleNamespace(
        _local_desktop_procs=[FakeProc(True)],
        _local_desktop_session_id="host-current",
        _local_desktop_started_at=1.0,
        _log=lambda *_a: None)
    stop = None
    for _klass in vars(_thread_host).values():
        if isinstance(_klass, type) and hasattr(
                _klass, "_host_stop_local_desktop"):
            stop = _klass._host_stop_local_desktop
            break
    assert stop is not None, "host helper stop method not found"
    res = stop(fake, {"session_id": "host-old"})
    assert res["conflict"] is True
    assert res["current_session_id"] == "host-current"
    assert fake._local_desktop_procs[0].terminated is False
    ok = stop(fake, {"session_id": "host-current"})
    assert ok == {"ok": True}
    assert fake._local_desktop_session_id is None
