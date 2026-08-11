"""Remote VNC sessions must travel through the outbound relay tunnel."""

import base64
import json

import pytest

from core import capability_auth as ca
from core.flowfile import FlowFile
from services import vnc_proxy


@pytest.fixture()
def cap_db(tmp_path):
    ca._reset_for_tests()
    ca.init_db(tmp_path / "caps.json")
    with vnc_proxy._lock:
        vnc_proxy._sessions.clear()
    yield
    with vnc_proxy._lock:
        vnc_proxy._sessions.clear()
    ca._reset_for_tests()


class _BrowserSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class _Relay:
    config = {}
    _relay_addr = "198.51.100.10"

    def __init__(self):
        self.calls = []

    def _request(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "desktop_status":
            return {"running": False, "local_screen_running": False}
        if action in ("start_desktop", "start_local_desktop"):
            return {"novnc_port": 6080 if action == "start_desktop" else 62966}
        if action == "desktop_ws_open":
            return {"ok": True}
        return {}


class _HttpRequest:
    def __init__(self, session_id, token, path):
        self.auth_user_id = "alice"
        self.remote_addr = "127.0.0.1"
        self.path_params = {
            "session_id": session_id,
            "token": token,
            "path": path,
        }
        self.query_string = ""
        self.method = "GET"
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, dict(headers), bytes(body))


class _HttpRelay(_Relay):
    def __init__(self, body):
        super().__init__()
        self.body = body
        self.http_proxy_calls = []
        self.http_fetch_calls = []

    def _request(self, action, **kwargs):
        if action == "novnc_asset":
            self.calls.append((action, kwargs))
            return {
                "body": base64.b64encode(self.body).decode("ascii"),
                "content_type": "text/css",
            }
        return super()._request(action, **kwargs)

    def _emit_http_response(self, on_output):
        on_output("start", {
            "status": 200,
            "headers": {"Content-type": "text/html"},
        })
        on_output("chunk", base64.b64encode(self.body).decode("ascii"))
        on_output("end", {})
        return {"ok": True}

    def http_proxy_stream(self, **kwargs):
        self.http_proxy_calls.append(kwargs)
        return self._emit_http_response(kwargs["on_output"])

    def http_fetch_stream(self, **kwargs):
        self.http_fetch_calls.append(kwargs)
        return self._emit_http_response(kwargs["on_output"])


def test_remote_vnc_websocket_uses_relay_tunnel(cap_db, monkeypatch):
    relay = _Relay()
    token = vnc_proxy.register_session(
        "desktop-remote", 6080,
        owner_user_id="alice",
        relay_service=relay,
        relay_id="relay-1")
    browser = _BrowserSocket()
    commands = []

    monkeypatch.setattr(vnc_proxy, "_ws_recv_frame", lambda _sock: (0x08, b""))
    monkeypatch.setattr(
        vnc_proxy, "_send_command_to_relay",
        lambda _relay, command: commands.append(command))
    monkeypatch.setattr(
        vnc_proxy.socket, "create_connection",
        lambda *_args, **_kwargs: pytest.fail("remote VNC must not use direct TCP"))

    vnc_proxy.vnc_ws_proxy(
        browser,
        {"session_id": "desktop-remote", "token": token},
        {"auth_user_id": "alice", "remote_addr": "127.0.0.1", "headers": {}},
    )

    open_call = next(call for call in relay.calls if call[0] == "desktop_ws_open")
    assert open_call[1]["port"] == 6080
    assert open_call[1]["ws_path"] == "/websockify"
    assert open_call[1]["headers"]["Sec-WebSocket-Protocol"] == "binary"
    assert commands[-1]["action"] == "desktop_ws_close"
    assert browser.closed is True


def test_remote_local_screen_marks_relay_backend(cap_db, monkeypatch):
    relay = _Relay()
    token = vnc_proxy.register_session(
        "desktop-host", 62966,
        owner_user_id="alice",
        relay_service=relay,
        relay_id="relay-1",
        local_screen=True)
    browser = _BrowserSocket()

    monkeypatch.setattr(vnc_proxy, "_ws_recv_frame", lambda _sock: (0x08, b""))
    monkeypatch.setattr(vnc_proxy, "_send_command_to_relay", lambda *_args: None)

    vnc_proxy.vnc_ws_proxy(
        browser,
        {"session_id": "desktop-host", "token": token},
        {"auth_user_id": "alice", "remote_addr": "127.0.0.1", "headers": {}},
    )

    open_call = next(call for call in relay.calls if call[0] == "desktop_ws_open")
    assert open_call[1]["local_screen"] is True


def test_remote_vnc_assets_use_relay_runtime_http(cap_db, monkeypatch):
    marker = b'<script type="module" crossorigin="anonymous" src="app/ui.js"></script>'
    relay = _HttpRelay(b"<html>" + marker + b"</html>")
    token = vnc_proxy.register_session(
        "desktop-remote", 6080,
        owner_user_id="alice",
        relay_service=relay,
        relay_id="relay-1")
    monkeypatch.setattr(vnc_proxy, "_NOVNC_LOCAL_DIRS", [])
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "remote VNC assets must not use server-local HTTP"))

    request = _HttpRequest("desktop-remote", token, "vnc.html")
    vnc_proxy.vnc_http_proxy(request)

    assert request.completed[0] == 200
    assert request.completed[1]["Content-Type"] == "text/html"
    assert "Content-type" not in request.completed[1]
    assert b"PawFlowNoVNC" in request.completed[2]
    assert relay.http_proxy_calls[0]["port"] == 6080
    assert relay.http_proxy_calls[0]["req_path"] == "/vnc.html"
    assert relay.http_fetch_calls == []


def test_remote_local_screen_assets_use_host_helper_http(cap_db, monkeypatch):
    relay = _HttpRelay(b"body{}")
    token = vnc_proxy.register_session(
        "desktop-host", 62966,
        owner_user_id="alice",
        relay_service=relay,
        relay_id="relay-1",
        local_screen=True)
    monkeypatch.setattr(vnc_proxy, "_NOVNC_LOCAL_DIRS", [])

    request = _HttpRequest(
        "desktop-host", token, "app/styles/base.css")
    vnc_proxy.vnc_http_proxy(request)

    assert request.completed[0] == 200
    assert request.completed[2] == b"body{}"
    assert relay.calls[-1] == (
        "novnc_asset", {"path": "app/styles/base.css"})
    assert relay.http_fetch_calls == []
    assert relay.http_proxy_calls == []


def test_relay_vnc_frames_are_forwarded_to_browser(cap_db):
    relay = _Relay()
    vnc_proxy.register_session(
        "desktop-remote", 6080,
        owner_user_id="alice",
        relay_service=relay,
        relay_id="relay-1")
    browser = _BrowserSocket()
    with vnc_proxy._lock:
        vnc_proxy._sessions["desktop-remote"]["vnc_ws_sessions"]["ws-1"] = {
            "browser_sock": browser,
        }

    vnc_proxy.dispatch_vnc_ws_data("relay-1", "ws-1", "YWJj", opcode=2)
    assert browser.sent == [b"\x82\x03abc"]

    vnc_proxy.dispatch_vnc_ws_close("relay-1", "ws-1")
    assert browser.closed is True
    assert browser.sent[-1].startswith(b"\x88")


@pytest.mark.parametrize(
    ("local_screen", "start_action", "port"),
    [
        (False, "start_desktop", 6080),
        (True, "start_local_desktop", 62966),
    ],
)
def test_open_remote_desktop_registers_relay_transport(
        monkeypatch, local_screen, start_action, port):
    from tasks.ai.actions import _sf_k7

    relay = _Relay()
    registered = []
    audio_registered = []

    def _request(action, **kwargs):
        relay.calls.append((action, kwargs))
        if action == "desktop_status":
            return {"running": False, "local_screen_running": False}
        if action == start_action:
            return {"novnc_port": port, "audio_port": 6180}
        return {}

    relay._request = _request
    monkeypatch.setattr(
        "services.vnc_proxy.register_session",
        lambda *args, **kwargs: registered.append((args, kwargs)) or "token")
    monkeypatch.setattr(
        "services.audio_proxy.register_audio_source",
        lambda *args, **kwargs: audio_registered.append((args, kwargs)) or "audio-token")
    monkeypatch.setattr(_sf_k7, "_ensure_vnc_routes", lambda _flowfile: None)
    helpers = (
        lambda _relay_id: relay,
        None,
        None,
        None,
        lambda _relay_id, _port: ("", 0),
        None,
    )
    flowfile = FlowFile(attributes={"auth.session_id": "login-1"})

    _sf_k7._handle_sf_k7(
        None, "open_desktop",
        {"relay_id": "relay-1", "local_screen": local_screen},
        None, "alice", flowfile, helpers)

    assert relay.calls[:2] == [
        ("desktop_status", {}),
        (start_action, {}),
    ]
    assert registered[0][0][1] == port
    assert registered[0][1]["relay_service"] is relay
    assert registered[0][1]["relay_id"] == "relay-1"
    assert bool(registered[0][1].get("local_screen")) is local_screen
    assert "host" not in registered[0][1]
    assert audio_registered[0][0][1:] == ("", 6180)
    assert audio_registered[0][1]["relay_service"] is relay
    assert audio_registered[0][1]["relay_id"] == "relay-1"
    assert bool(audio_registered[0][1]["local_screen"]) is local_screen
    assert json.loads(flowfile.get_content())["ok"] is True


def test_server_dispatch_routes_desktop_frames():
    source = open("services/_relay_conn.py", encoding="utf-8").read()
    assert "mtype == 'desktop_ws_data'" in source
    assert "dispatch_vnc_ws_data(service._service_id" in source
    assert "mtype == 'desktop_ws_close'" in source
    assert "dispatch_vnc_ws_close(service._service_id" in source
    assert "mtype == 'desktop_audio_data'" in source
    assert "dispatch_audio_data(service._service_id" in source
    assert "mtype == 'desktop_audio_close'" in source
    assert "dispatch_audio_close(service._service_id" in source


def test_server_managed_desktop_keeps_direct_vnc_and_audio_registration(
        monkeypatch):
    from tasks.ai.actions import _sf_k7

    relay = _Relay()
    vnc_registered = []
    audio_registered = []

    def _request(action, **kwargs):
        if action == "desktop_status":
            return {"running": False}
        if action == "start_desktop":
            return {"novnc_port": 6080, "audio_port": 6180}
        return {}

    relay._request = _request
    monkeypatch.setattr(
        "services.vnc_proxy.register_session",
        lambda *args, **kwargs: vnc_registered.append((args, kwargs)) or "vnc-token")
    monkeypatch.setattr(
        "services.audio_proxy.register_audio_source",
        lambda *args, **kwargs: audio_registered.append((args, kwargs)) or "audio-token")
    monkeypatch.setattr(_sf_k7, "_ensure_vnc_routes", lambda _flowfile: None)
    helpers = (
        lambda _relay_id: relay,
        None,
        None,
        None,
        lambda _relay_id, port: ("172.17.0.2", port),
        None,
    )
    flowfile = FlowFile(attributes={"auth.session_id": "login-1"})

    _sf_k7._handle_sf_k7(
        None, "open_desktop",
        {"relay_id": "MyWorkspace", "local_screen": False},
        None, "alice", flowfile, helpers)

    assert vnc_registered[0][0][1] == 6080
    assert vnc_registered[0][1]["host"] == "172.17.0.2"
    assert "relay_service" not in vnc_registered[0][1]
    assert audio_registered[0][0][1:] == ("172.17.0.2", 6180)
    assert "relay_service" not in audio_registered[0][1]
