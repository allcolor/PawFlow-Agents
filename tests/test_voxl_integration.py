"""voxl, the default desktop viewer: relay launcher, server proxy, URLs."""
import http.server
import os
import stat
import sys
import textwrap
import threading
import types

import pytest

from pawflow_relay import voxl
from pawflow_relay import _relay_desktop as dt


# ── Relay launcher ───────────────────────────────────────────────────


@pytest.fixture
def fake_voxl(tmp_path, monkeypatch):
    """An executable that behaves like voxl: serves /healthz on --port and
    records its argv and VOXL_PASSWORD."""
    record = tmp_path / "record.txt"
    script = tmp_path / "voxl"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import http.server, os, sys
        port = int([a for a in sys.argv if a.startswith('--port=')][0][7:])
        with open({str(record)!r}, 'w') as f:
            f.write(' '.join(sys.argv[1:]) + '\\n' + os.environ['VOXL_PASSWORD'])
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/healthz' else 401)
                self.end_headers()
            def log_message(self, *a):
                pass
        http.server.HTTPServer(('127.0.0.1', port), H).serve_forever()
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PAWFLOW_VOXL_BIN", str(script))
    return record


def test_start_voxl_passes_a_random_password_and_waits_until_ready(fake_voxl):
    started = voxl.start_voxl(addr="127.0.0.1", audio_device="virtual_out.monitor",
                              files_root="/home/pawflow")
    assert started is not None
    process, port, password = started
    try:
        assert voxl.voxl_ready(port)
        argv, recorded_password = fake_voxl.read_text().split("\n")
        assert recorded_password == password and len(password) >= 24
        assert f"--port={port}" in argv and "--addr=127.0.0.1" in argv
        assert "--audio-device-name=virtual_out.monitor" in argv
        assert "--files-root=/home/pawflow" in argv
        # The password never appears on the command line.
        assert password not in argv
    finally:
        voxl.stop_voxl(process)
    assert process.poll() is not None


def test_two_starts_get_different_passwords(fake_voxl):
    first = voxl.start_voxl()
    second = voxl.start_voxl()
    try:
        assert first[2] != second[2] and first[1] != second[1]
    finally:
        voxl.stop_voxl(first[0])
        voxl.stop_voxl(second[0])


def test_no_binary_means_no_voxl(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_VOXL_BIN", str(tmp_path / "missing"))
    assert voxl.find_voxl() == ""
    assert voxl.start_voxl() is None


def test_a_voxl_that_never_gets_ready_is_stopped(tmp_path, monkeypatch):
    script = tmp_path / "voxl"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PAWFLOW_VOXL_BIN", str(script))
    assert voxl.start_voxl(ready_timeout=0.5) is None


def test_find_voxl_uses_the_packaged_runtime_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("PAWFLOW_VOXL_BIN", raising=False)
    name = "voxl.exe" if sys.platform == "win32" else "voxl"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / name).write_text("")
    monkeypatch.setenv("PAWFLOW_RELAY_RUNTIME_ROOT", str(tmp_path))
    assert voxl.find_voxl() == str(tmp_path / "bin" / name)


def test_find_voxl_finds_the_binary_next_to_a_frozen_relay(tmp_path, monkeypatch):
    # Windows Relay CLI install: voxl.exe sits beside pawflow-relay.exe.
    monkeypatch.delenv("PAWFLOW_VOXL_BIN", raising=False)
    monkeypatch.delenv("PAWFLOW_RELAY_RUNTIME_ROOT", raising=False)
    name = "voxl.exe" if sys.platform == "win32" else "voxl"
    install = tmp_path / "PawFlow Relay CLI"
    install.mkdir()
    (install / "pawflow-relay.exe").write_text("")
    (install / name).write_text("")
    monkeypatch.setattr(voxl.sys, "frozen", True, raising=False)
    monkeypatch.setattr(voxl.sys, "executable", str(install / "pawflow-relay.exe"))
    monkeypatch.setattr(voxl, "__file__", str(tmp_path / "unpack" / "pawflow_relay" / "voxl.py"))
    monkeypatch.setattr(voxl.shutil, "which", lambda _name: None)
    assert voxl.find_voxl() == str((install / name).resolve())


def test_desktop_answers_carry_voxl_only_while_it_runs():
    alive = types.SimpleNamespace(poll=lambda: None)
    dead = types.SimpleNamespace(poll=lambda: 1)
    assert dt._voxl_fields(alive, 6901, "pw") == {
        "voxl_port": 6901, "voxl_password": "pw"}
    assert dt._voxl_fields(dead, 6901, "pw") == {
        "voxl_port": 0, "voxl_password": ""}
    assert dt._state_voxl(types.SimpleNamespace(), "desktop") == {
        "voxl_port": 0, "voxl_password": ""}


# ── Server: session fields and URLs ──────────────────────────────────


def test_open_desktop_urls_default_to_voxl_with_novnc_fallback():
    from tasks.ai.actions._sf_k7 import _desktop_urls, _voxl_session_kwargs

    relay = object()
    vx = _voxl_session_kwargs({"voxl_port": 6901, "voxl_password": "pw"},
                              relay, prefix="", local_screen=False,
                              server_local=False)
    assert vx == {"voxl_port": 6901, "voxl_password": "pw",
                  "voxl_relay_service": relay, "voxl_local": False}
    urls = _desktop_urls("desktop_r", "tok", 6080, vx)
    assert urls["viewer"] == "voxl"
    assert urls["url"] == urls["voxl_url"] == "/vnc/desktop_r/tok/voxl/"
    assert urls["novnc_url"].startswith("/vnc/desktop_r/tok/vnc.html?")
    assert "pw" not in str(urls)

    no_voxl = _desktop_urls("desktop_r", "tok", 6080, {})
    assert no_voxl["viewer"] == "novnc" and no_voxl["url"] == no_voxl["novnc_url"]

    host = _voxl_session_kwargs(
        {"local_screen_voxl_port": 7000, "local_screen_voxl_password": "x"},
        relay, prefix="local_screen_", local_screen=True, server_local=False)
    assert host["voxl_local"] is True and host["voxl_port"] == 7000
    server_local = _voxl_session_kwargs(
        {"voxl_port": 7001}, relay, prefix="", local_screen=True,
        server_local=True)
    assert server_local["voxl_host"] == "127.0.0.1"
    assert _voxl_session_kwargs({}, relay, prefix="", local_screen=False,
                                server_local=False) == {}


# ── Server: voxl proxy ───────────────────────────────────────────────


class _Req:
    def __init__(self, method="GET", headers=None, body=b"", query="",
                 path_params=None):
        self.method = method
        self.headers = headers or {}
        self.body = body
        self.query_string = query
        self.path_params = path_params or {}
        self.completed = None
        self.streamed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)

    def complete_stream(self, status, headers, chunks):
        self.streamed = (status, headers, b"".join(chunks))


BROWSER_HEADERS = {
    "Cookie": "pawflow_token=secret", "Origin": "https://webchat.example",
    "Authorization": "Bearer pawflow-user-token", "Referer": "https://x",
    "Content-Type": "application/octet-stream", "Range": "bytes=0-9",
}


def test_relay_http_sends_voxl_password_and_drops_browser_credentials(monkeypatch):
    from services import voxl_proxy
    from services import _relay_http_response as rhr

    calls = []

    class _Stream:
        status, headers, error = 200, {"Content-Type": "text/html",
                                       "Connection": "keep-alive"}, ""

        def start(self):
            return self

        def wait_ready(self):
            pass

        def iter_bytes(self):
            yield b"<html>voxl</html>"

    monkeypatch.setattr(rhr.RelayHttpResponseStream, "for_local_port",
                        classmethod(lambda cls, relay, **kw: calls.append(
                            ("port", kw)) or _Stream()))
    monkeypatch.setattr(rhr.RelayHttpResponseStream, "for_fetch",
                        classmethod(lambda cls, relay, **kw: calls.append(
                            ("fetch", kw)) or _Stream()))
    session = {"voxl_port": 6901, "voxl_password": "voxl-pw",
               "voxl_relay_service": object(), "voxl_local": False}
    req = _Req("PUT", BROWSER_HEADERS, body=b"upload", query="path=a")
    voxl_proxy.voxl_http_proxy(req, "sid", session, "files/upload")

    kind, kw = calls[0]
    assert kind == "port" and kw["port"] == 6901
    assert kw["method"] == "PUT" and kw["body"] == b"upload"
    assert kw["req_path"] == "/files/upload?path=a"
    assert kw["headers"] == {
        "Content-Type": "application/octet-stream", "Range": "bytes=0-9",
        "Host": "127.0.0.1:6901", "Authorization": "Bearer voxl-pw"}
    assert req.streamed == (200, {
        "Content-Type": "text/html",
        # The chat page is cross-origin isolated: without these the
        # browser refuses to show voxl in its iframe.
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
    }, b"<html>voxl</html>")

    # A host-screen voxl goes through the host helper (local=True).
    calls.clear()
    voxl_proxy.voxl_http_proxy(_Req(), "sid", {**session, "voxl_local": True}, "")
    kind, kw = calls[0]
    assert kind == "fetch" and kw["local"] is True
    assert kw["url"] == "http://127.0.0.1:6901/"


def test_direct_http_streams_from_a_local_voxl():
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            seen["cookie"] = self.headers.get("Cookie")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(b"console.log(1)")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.handle_request, daemon=True).start()
    from services import voxl_proxy
    req = _Req(headers=BROWSER_HEADERS)
    voxl_proxy.voxl_http_proxy(req, "sid", {
        "voxl_port": server.server_address[1], "voxl_password": "pw",
        "voxl_host": "127.0.0.1"}, "client.js")
    server.server_close()
    assert req.streamed[0] == 200 and req.streamed[2] == b"console.log(1)"
    assert seen == {"auth": "Bearer pw", "cookie": None}


def test_voxl_http_without_voxl_is_404():
    from services import voxl_proxy
    req = _Req()
    voxl_proxy.voxl_http_proxy(req, "sid", {"port": 6080}, "")
    assert req.completed[0] == 404


def test_vnc_routes_hand_voxl_paths_to_the_voxl_proxy(monkeypatch):
    from services import vnc_proxy, voxl_proxy
    import core.capability_routes as routes

    monkeypatch.setattr(routes, "verify_route_request",
                        lambda *a, **k: ({}, None))
    calls = []
    monkeypatch.setattr(voxl_proxy, "voxl_http_proxy",
                        lambda req, sid, session, sub: calls.append((sid, sub)))
    with vnc_proxy._lock:
        vnc_proxy._sessions["sid"] = {"port": 6080, "voxl_port": 6901}
    try:
        req = _Req(path_params={"session_id": "sid", "token": "tok",
                                "path": "voxl/worker.js"})
        vnc_proxy.vnc_http_proxy(req)
        assert calls == [("sid", "worker.js")]

        redirect = _Req(path_params={"session_id": "sid", "token": "tok",
                                     "path": "voxl"})
        vnc_proxy.vnc_http_proxy(redirect)
        assert redirect.completed[:2] == (
            301, {"Location": "/vnc/sid/tok/voxl/"})

        put = _Req("PUT", path_params={"session_id": "sid", "token": "tok",
                                       "path": "vnc.html"})
        vnc_proxy.vnc_http_proxy(put)
        assert put.completed[0] == 405
    finally:
        with vnc_proxy._lock:
            vnc_proxy._sessions.pop("sid", None)


def test_voxl_websocket_uses_the_relay_tunnel_with_clean_headers(monkeypatch):
    from services import vnc_proxy, voxl_proxy
    import core.capability_routes as routes

    monkeypatch.setattr(routes, "verify_route_ws", lambda *a, **k: ({}, None))
    tunnels = []
    monkeypatch.setattr(vnc_proxy, "_vnc_ws_relay_proxy",
                        lambda *a, **kw: tunnels.append(kw))
    relay = object()
    with vnc_proxy._lock:
        vnc_proxy._sessions["sid"] = {
            "port": 6080, "voxl_port": 6901, "voxl_password": "pw",
            "voxl_relay_service": relay, "voxl_local": True}
    try:
        voxl_proxy.voxl_ws_proxy(
            object(), {"session_id": "sid", "token": "tok"},
            {"headers": BROWSER_HEADERS, "query": "stats"})
    finally:
        with vnc_proxy._lock:
            vnc_proxy._sessions.pop("sid", None)
    assert tunnels == [{
        "port": 6901, "ws_path": "/ws?stats",
        "headers": {"Host": "127.0.0.1:6901", "Authorization": "Bearer pw"},
        "relay_service": relay, "local_screen": True}]


def test_relay_tunnel_defaults_still_serve_novnc(monkeypatch):
    """The parametrized tunnel keeps noVNC's path, port and subprotocol."""
    from services import vnc_proxy

    opened = []

    class _Relay:
        _service_id = "relay-1"

        def _request(self, action, **kw):
            opened.append(kw)
            return {"ok": False, "error": "stop here"}

    monkeypatch.setattr(vnc_proxy, "_ws_close", lambda *a: None)
    with vnc_proxy._lock:
        vnc_proxy._sessions["sid"] = {"port": 6080, "vnc_ws_sessions": {}}
    try:
        vnc_proxy._vnc_ws_relay_proxy(
            object(), "sid", {"port": 6080, "relay_service": _Relay()},
            {"headers": {}})
    finally:
        with vnc_proxy._lock:
            vnc_proxy._sessions.pop("sid", None)
    assert opened[0]["ws_path"] == "/websockify" and opened[0]["port"] == 6080
    assert opened[0]["headers"]["Sec-WebSocket-Protocol"] == "binary"


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake binary")
def test_route_registration_includes_voxl_websocket_and_methods():
    from tasks.ai.actions import _sf_routes
    import services.http_listener_service as listener

    registered = []

    class _Svc:
        def register_route(self, method, pattern, owner, **kw):
            registered.append((method, pattern))

        def get_routes(self):
            return []

    flowfile = types.SimpleNamespace(
        get_attribute=lambda name: "9999" if name == "http.listener.port" else "")
    listener._instances[9999] = _Svc()
    try:
        _sf_routes._ensure_vnc_routes(flowfile)
    finally:
        listener._instances.pop(9999, None)
    assert ("GET", "/vnc/{session_id}/{token}/voxl/ws") in registered
    for method in ("HEAD", "PUT", "POST"):
        assert (method, "/vnc/{session_id}/{token}/{path+}") in registered
