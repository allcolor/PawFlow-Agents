"""Regression tests for the post-review fixes:
  - ConfigStore.load_secrets must NOT silently return ciphertext on
    decrypt failure (P0/P1 #3).
  - read_only mode must take precedence over per-tool `allow`
    overrides (P1 #4).
  - The HTTP route registry's `{path+}` pattern requires at least
    one segment, so a registered `/code/{sid}/{tok}/{path+}` does
    NOT match `/code/s/t/`. Phase 2-5 hand the user the trailing-
    slash URL, so a root pattern (`/code/{sid}/{tok}/`) MUST also
    be registered (P0 #1).
"""

import json

import pytest


# ---------------------------------------------------------------------------
# P0/P1 #3 — ConfigStore.load_secrets fail-loud
# ---------------------------------------------------------------------------


def test_load_secrets_drops_undecryptable_value(tmp_path, monkeypatch):
    from core import secrets as secrets_mod
    from core.config_store import ConfigStore

    # Force a fresh manager bound to a known password so we can write
    # the on-disk file with a *different* password and watch decrypt
    # fail.
    secrets_mod._reset_for_tests()
    monkeypatch.setenv("PAWFLOW_SECRET_KEY", "writer-password")
    sm_writer = secrets_mod.get_secrets_manager()
    enc = sm_writer.encrypt("sk-real-secret")
    secrets_mod._reset_for_tests()

    p = tmp_path / "secrets.json"
    p.write_text(json.dumps({"api_key": enc}), encoding="utf-8")

    # Re-init with a different password — decrypt must fail.
    monkeypatch.setenv("PAWFLOW_SECRET_KEY", "reader-DIFFERENT-password")
    out = ConfigStore.load_secrets(p)
    assert "api_key" in out
    # MUST NOT be the ciphertext, MUST NOT be the plaintext, MUST
    # be the empty fallback so the caller fails visibly.
    assert out["api_key"].as_str() == ""

    secrets_mod._reset_for_tests()


# ---------------------------------------------------------------------------
# P1 #4 — read_only takes precedence over a stale per-tool `allow`
# ---------------------------------------------------------------------------


def test_read_only_blocks_write_tool_even_with_allow_override(tmp_path, monkeypatch):
    """In read_only mode, a leftover `tool_permissions['edit'] = 'allow'`
    from a previous mode must NOT let `edit` through. The relay
    consults `ToolApprovalGate.is_read_only_allowed` BEFORE the
    per-tool override."""
    # We don't need a full ConversationStore stand-up — the precedence
    # rule lives in tool_relay_service._do_execute. Inspect the source
    # to make sure the ordering is right; this matches the structural
    # checks we already use for the gauge invariants in JS.
    src = open("services/_tool_relay_execute.py", encoding="utf-8").read()  # _do_execute moved here in the <=800 split
    # The read_only block must appear before the `_tool_perm == "allow"`
    # branch (otherwise a stale `allow` wins).
    ro_idx = src.index('if _perm_mode == "read_only":')
    allow_idx = src.index('elif _tool_perm == "allow":')
    assert ro_idx < allow_idx, (
        "read_only check must run BEFORE the per-tool allow override; "
        "otherwise a stale allow leaks through after switching to read_only.")


# ---------------------------------------------------------------------------
# P0 #1 — The trailing-slash root URL we hand to the user must match
# ---------------------------------------------------------------------------


def test_route_pattern_path_plus_does_not_match_empty_segment():
    """The `{path+}` pattern requires ≥1 segment after the slash,
    so without an explicit trailing-slash route the URL we hand to
    the user (`/code/<sid>/<tok>/`) lands on a 404. This locks in
    the requirement to register BOTH patterns."""
    from services.http_listener_service import RouteRegistry
    reg = RouteRegistry()
    reg.register("GET", "/code/{session_id}/{token}/{path+}",
                 "x", callback=lambda r: None)
    # Subroute matches.
    assert reg.match("GET", "/code/s/t/index.html") is not None
    # Trailing-slash root does NOT match the {path+} pattern alone.
    assert reg.match("GET", "/code/s/t/") is None
    # Adding the explicit root pattern makes the URL match.
    reg.register("GET", "/code/{session_id}/{token}/",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/code/s/t/") is not None


def test_code_server_routes_use_request_listener_port(monkeypatch):
    from core import FlowFile
    from services import http_listener_service as hls
    from tasks.ai.actions.service_flow import _ensure_code_server_routes

    class FakeListener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return [
                {"method": method, "pattern": pattern, "owner": owner}
                for method, pattern, owner, _, _, _ in self.routes
            ]

        def register_route(self, method, pattern, owner, callback,
                           ws_handler=None, public=False):
            self.routes.append((method, pattern, owner, callback, ws_handler, public))

    request_listener = FakeListener()
    other_listener = FakeListener()
    monkeypatch.setattr(hls, "_instances", {
        8080: other_listener,
        9090: request_listener,
    })
    ff = FlowFile()
    ff.set_attribute("http.listener.port", "9090")

    _ensure_code_server_routes(ff)

    request_patterns = {(route[0], route[1]) for route in request_listener.routes}
    assert ("GET", "/code/{session_id}/{token}/") in request_patterns
    assert ("GET", "/code/{session_id}/{token}/{path+}") in request_patterns
    assert all(route[5] is True for route in request_listener.routes)
    assert other_listener.routes == []


def test_code_server_routes_fallback_to_all_listeners_without_request_port(monkeypatch):
    from core import FlowFile
    from services import http_listener_service as hls
    from tasks.ai.actions.service_flow import _ensure_code_server_routes

    class FakeListener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return [
                {"method": method, "pattern": pattern, "owner": owner}
                for method, pattern, owner, _, _, _ in self.routes
            ]

        def register_route(self, method, pattern, owner, callback,
                           ws_handler=None, public=False):
            self.routes.append((method, pattern, owner, callback, ws_handler, public))

    listener_a = FakeListener()
    listener_b = FakeListener()
    monkeypatch.setattr(hls, "_instances", {
        8080: listener_a,
        9090: listener_b,
    })

    _ensure_code_server_routes(FlowFile())

    for listener in (listener_a, listener_b):
        patterns = {(route[0], route[1]) for route in listener.routes}
        assert ("GET", "/code/{session_id}/{token}/") in patterns
        assert ("GET", "/code/{session_id}/{token}/{path+}") in patterns
        assert all(route[5] is True for route in listener.routes)


def test_fwd_root_url_requires_explicit_trailing_slash_route():
    from services.http_listener_service import RouteRegistry
    reg = RouteRegistry()
    reg.register("GET", "/fwd/{forward_id}/{token}/{path+}",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/fwd/f/t/") is None
    reg.register("GET", "/fwd/{forward_id}/{token}/",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/fwd/f/t/") is not None


# ---------------------------------------------------------------------------
# Approval gate failures must deny, not execute
# ---------------------------------------------------------------------------


def test_tool_relay_approval_exception_denies_without_executing(monkeypatch):
    from services.tool_relay_service import ToolRelayService
    from core.conversation_store import ConversationStore

    class FakeRegistry:
        executed = False

        def execute(self, tool_name, arguments):
            self.executed = True
            return "should-not-run"

    reg = FakeRegistry()
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *a, **k: reg)
    monkeypatch.setattr(
        ConversationStore, "instance",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("store down"))))

    result = svc._do_execute(
        "rid1", "bash", {"command": "echo unsafe"},
        "alice", "conv1", "agent1")

    assert reg.executed is False
    assert result["type"] == "result"
    assert "approval check failed" in result["data"]
    assert "approval check failed" in result["data"]


# ---------------------------------------------------------------------------
# Code-server WS sessions must use the code session id, not relay id
# ---------------------------------------------------------------------------


def test_code_server_ws_registers_browser_socket_under_session_id(tmp_path, monkeypatch):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeSock:
        closed = False

        def sendall(self, data):
            pass

        def close(self):
            self.closed = True

    class FakeRelay:
        def _request(self, action, **kwargs):
            assert action == "cs_ws_open"
            return {"ok": True}

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    session_id, token = csp.register_code_server(
        "relay-1", 8765, FakeRelay(), owner_user_id="alice")
    seen = {}

    def fake_ws_recv(sock):
        with csp._lock:
            active = csp._sessions[session_id]["cs_ws_sessions"]
            seen["count"] = len(active)
            seen["sock"] = next(iter(active.values()))["browser_sock"]
        return 0x08, b""

    monkeypatch.setattr(csp, "_ws_recv", fake_ws_recv)
    monkeypatch.setattr(csp, "_send_command_to_relay", lambda *a, **k: None)

    sock = FakeSock()
    csp.code_ws_proxy(
        sock,
        {"session_id": session_id, "token": token, "path": "websocket"},
        {"auth_user_id": "alice", "remote_addr": "127.0.0.1", "headers": {}, "query": ""},
    )

    assert seen == {"count": 1, "sock": sock}
    assert sock.closed is True


def test_code_server_ws_forwards_selected_subprotocol(tmp_path, monkeypatch):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeSock:
        def sendall(self, data):
            pass

        def close(self):
            pass

    class FakeRelay:
        def __init__(self):
            self.headers = None

        def _request(self, action, **kwargs):
            assert action == "cs_ws_open"
            self.headers = kwargs["headers"]
            return {"ok": True}

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    monkeypatch.setattr(csp, "_ws_recv", lambda sock: (0x08, b""))
    monkeypatch.setattr(csp, "_send_command_to_relay", lambda *a, **k: None)

    csp.code_ws_proxy(
        FakeSock(),
        {"session_id": session_id, "token": token, "path": "websocket"},
        {
            "auth_user_id": "alice",
            "remote_addr": "127.0.0.1",
            "headers": {"Sec-WebSocket-Protocol": "vscode, extra"},
            "query": "",
        },
    )

    assert relay.headers["Sec-WebSocket-Protocol"] == "vscode"


def test_code_server_http_proxy_strips_public_prefix_by_default(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.kwargs = None

        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            self.kwargs = kwargs
            return {"ok": True, "data": {
                "status": 200,
                "headers": {"Content-Type": "text/html"},
                "body": "SGVsbG8=",
            }}

    class FakeReq:
        method = "GET"
        query_string = "v=1"
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 0, relay, owner_user_id="alice")
    csp.update_code_server_port(session_id, 8765)

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": "static/app.js"}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.body == b"Hello"
    assert relay.kwargs["port"] == 8765
    assert relay.kwargs["req_path"] == "/static/app.js?v=1"


def test_code_server_http_proxy_accepts_public_capability_url(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.called = False

        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            self.called = True
            return {"status": 200, "headers": {}, "body": "T0s="}

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = ""
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": ""}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.body == b"OK"
    assert relay.called is True


def test_code_server_proxy_strips_public_prefix_for_root_upstream(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.kwargs = None

        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            self.kwargs = kwargs
            return {"status": 200, "headers": {}, "body": "T0s="}

    class FakeReq:
        method = "GET"
        query_string = "v=1"
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 0, relay, owner_user_id="alice")
    csp.update_code_server_port(session_id, 8765, upstream_base_path="/")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": "static/app.js"}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert relay.kwargs["req_path"] == "/static/app.js?v=1"


def test_code_server_http_proxy_recomputes_binary_content_length(tmp_path):
    import base64

    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    png = b"\x89PNG\r\n\x1a\nfake"

    class FakeRelay:
        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            return {
                "status": 200,
                "headers": {
                    "Content-Type": "image/png",
                    "Content-Length": "999999",
                    "Transfer-Encoding": "chunked",
                },
                "body": base64.b64encode(png).decode("ascii"),
            }

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    session_id, token = csp.register_code_server(
        "relay-1", 8765, FakeRelay(), owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": "file.png"}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.headers["Content-Type"] == "image/png"
    assert req.headers["Content-Length"] == str(len(png))
    assert "Transfer-Encoding" not in req.headers
    assert req.body == png


def test_code_server_http_proxy_sets_webview_csp(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            return {
                "status": 200,
                "headers": {"Content-Type": "text/html"},
                "body": "PGh0bWw+PC9odG1sPg==",
            }

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    session_id, token = csp.register_code_server(
        "relay-1", 8765, FakeRelay(), owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": ""}
    csp.code_http_proxy(req)

    assert req.status == 200
    policy = req.headers["Content-Security-Policy"]
    assert "https://*.vscode-cdn.net" in policy
    assert "script-src-elem" in policy
    assert "style-src-elem" in policy
    assert "font-src" in policy
    assert "https://open-vsx.org" not in policy


def test_code_server_http_proxy_accepts_tokenless_static_asset(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.kwargs = None

        def _request(self, action, **kwargs):
            raise AssertionError("seti.woff compatibility asset must not hit upstream")

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = ""
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    req = FakeReq()
    req.path_params = {
        "session_id": session_id,
        "token": "_i",
        "path": "icons/seti.woff",
    }
    csp.code_http_proxy(req)

    assert req.status == 200
    assert relay.kwargs is None
    assert req.headers["Content-Type"] == "font/woff"
    assert req.body == b""
    assert token != "_i"


def test_code_server_http_proxy_falls_back_for_missing_seti_font(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def _request(self, action, **kwargs):
            assert action == "http_proxy"
            return {
                "status": "404",
                "headers": {"Content-Type": "text/plain"},
                "body": "Tm90IGZvdW5k",
            }

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    session_id, token = csp.register_code_server(
        "relay-1", 8765, FakeRelay(), owner_user_id="alice")

    req = FakeReq()
    req.path_params = {
        "session_id": session_id,
        "token": token,
        "path": "stable-build/static/extensions/theme-seti/icons/seti.woff",
    }
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.headers["Content-Type"] == "font/woff"
    assert req.body == b""


def test_code_server_http_proxy_restarts_dead_upstream_once(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.calls = []

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "http_proxy" and kwargs["port"] == 8765:
                return {"ok": False, "error": "Proxy error: [Errno 111] Connection refused"}
            if action == "start_code_server":
                return {"ok": True, "data": {"port": 9876, "upstream_base_path": "/"}}
            if action == "http_proxy" and kwargs["port"] == 9876:
                return {"status": 200, "headers": {}, "body": "T0s="}
            raise AssertionError((action, kwargs))

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": ""}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.body == b"OK"
    assert [action for action, _ in relay.calls] == [
        "http_proxy", "start_code_server", "http_proxy"]
    with csp._lock:
        assert csp._sessions[session_id]["port"] == 9876


def test_code_server_http_proxy_restarts_after_proxy_exception(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def __init__(self):
            self.calls = []

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "http_proxy" and kwargs["port"] == 8765:
                raise RuntimeError("Proxy error: [Errno 111] Connection refused")
            if action == "start_code_server":
                return {"ok": True, "data": {"port": 9876, "upstream_base_path": "/"}}
            if action == "http_proxy" and kwargs["port"] == 9876:
                return {"status": 200, "headers": {}, "body": "T0s="}
            raise AssertionError((action, kwargs))

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": ""}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.body == b"OK"
    assert [action for action, _ in relay.calls] == [
        "http_proxy", "start_code_server", "http_proxy"]


def test_code_server_http_proxy_waits_after_restart_refusal(tmp_path, monkeypatch):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")
    monkeypatch.setattr(csp.time, "sleep", lambda _seconds: None)

    class FakeRelay:
        def __init__(self):
            self.calls = []
            self.new_port_attempts = 0

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "http_proxy" and kwargs["port"] == 8765:
                return {"ok": False, "error": "Proxy error: [Errno 111] Connection refused"}
            if action == "start_code_server":
                return {"ok": True, "data": {"port": 9876, "upstream_base_path": "/"}}
            if action == "http_proxy" and kwargs["port"] == 9876:
                self.new_port_attempts += 1
                if self.new_port_attempts == 1:
                    return {"ok": False, "error": "Proxy error: [Errno 111] Connection refused"}
                return {"status": 200, "headers": {}, "body": "T0s="}
            raise AssertionError((action, kwargs))

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    req = FakeReq()
    req.path_params = {"session_id": session_id, "token": token, "path": ""}
    csp.code_http_proxy(req)

    assert req.status == 200
    assert req.body == b"OK"
    assert [action for action, _ in relay.calls] == [
        "http_proxy", "start_code_server", "http_proxy", "http_proxy"]


def test_code_server_ws_open_retries_connection_refused(tmp_path, monkeypatch):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")
    monkeypatch.setattr(csp.time, "sleep", lambda _seconds: None)

    class FakeRelay:
        def __init__(self):
            self.calls = []

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "cs_ws_open" and len(self.calls) == 1:
                return {"ok": False, "error": "cs_ws_open error: [Errno 111] Connection refused"}
            if action == "cs_ws_open":
                return {"ok": True}
            raise AssertionError((action, kwargs))

    class FakeSock:
        def __init__(self):
            self.sent = []

        def recv(self, _n):
            raise ConnectionError("WS connection closed (0 bytes)")

        def sendall(self, data):
            self.sent.append(data)

        def close(self):
            pass

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    relay = FakeRelay()
    session_id, token = csp.register_code_server(
        "relay-1", 8765, relay, owner_user_id="alice")

    csp.code_ws_proxy(
        FakeSock(),
        {"session_id": session_id, "token": token, "path": "static/ws"},
        {"headers": {}, "query": ""},
    )

    assert [action for action, _ in relay.calls] == ["cs_ws_open", "cs_ws_open"]


def test_code_server_proxy_serves_missing_vsda_assets_without_upstream(tmp_path):
    from core.capability_auth import init_db
    from services import code_server_proxy as csp

    init_db(tmp_path / "capabilities.json")

    class FakeRelay:
        def _request(self, action, **kwargs):
            raise AssertionError("VSDA compatibility assets must not hit upstream")

    class FakeReq:
        method = "GET"
        query_string = ""
        headers = {}
        body = b""
        auth_user_id = "alice"
        auth_session_id = ""
        remote_addr = "127.0.0.1"

        def complete(self, status, headers, body):
            self.status = status
            self.headers = headers
            self.body = body

    with csp._lock:
        csp._sessions.clear()
        csp._relay_to_session.clear()

    session_id, token = csp.register_code_server(
        "relay-1", 8765, FakeRelay(), owner_user_id="alice")

    js_req = FakeReq()
    js_req.path_params = {"session_id": session_id, "token": token, "path": "vsda.js"}
    csp.code_http_proxy(js_req)
    assert js_req.status == 200
    assert js_req.headers["Content-Type"] == "application/javascript"
    assert b"globalThis.vsda_web" in js_req.body
    assert b"define([], function ()" in js_req.body

    wasm_req = FakeReq()
    wasm_req.path_params = {
        "session_id": session_id,
        "token": token,
        "path": "static/node_modules/vsda/rust/web/vsda_bg.wasm",
    }
    csp.code_http_proxy(wasm_req)
    assert wasm_req.status == 200
    assert wasm_req.headers["Content-Type"] == "application/wasm"
    assert wasm_req.body.startswith(b"\x00asm")


def test_code_server_command_send_uses_relay_ws_frame(monkeypatch):
    import asyncio
    import json

    from services import code_server_proxy as csp
    import services.filesystem_service as fs

    class FakeLock:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class BadListener:
        pass

    loop = object()
    sent = []

    async def fake_ws_send_frame(writer, payload):
        sent.append((writer, json.loads(payload.decode("utf-8"))))

    monkeypatch.setattr(fs, "_ws_send_frame", fake_ws_send_frame)

    class FakeFuture:
        def __init__(self, coro):
            self.coro = coro

        def result(self, timeout=None):
            asyncio.run(self.coro)

    monkeypatch.setattr(
        asyncio, "run_coroutine_threadsafe",
        lambda coro, target_loop: FakeFuture(coro),
    )

    class FakeRelay:
        _connection = BadListener()
        _relay_pool_lock = FakeLock()
        _relay_pool = [{"writer": "writer-1", "loop": loop}]

    csp._send_command_to_relay(FakeRelay(), {"action": "cs_ws_close", "session_id": "sid"})

    assert sent[0][0] == "writer-1"
    assert sent[0][1]["type"] == "command"
    assert sent[0][1]["action"] == "cs_ws_close"
    assert sent[0][1]["session_id"] == "sid"


def test_code_server_ws_recv_preserves_raw_masked_frame():
    from services import code_server_proxy as csp

    class FakeSocket:
        def __init__(self, data):
            self.data = bytearray(data)

        def recv(self, n):
            chunk = self.data[:n]
            del self.data[:n]
            return bytes(chunk)

    raw = b"\x81\x85\x01\x02\x03\x04" + bytes(
        b ^ [1, 2, 3, 4][i % 4] for i, b in enumerate(b"hello"))
    opcode, payload, frame = csp._ws_recv_frame(FakeSocket(raw))

    assert opcode == 1
    assert payload == b"hello"
    assert frame == raw


def test_code_server_dispatch_can_forward_raw_backend_frame():
    import base64
    from services import code_server_proxy as csp

    class FakeBrowserSocket:
        def __init__(self):
            self.sent = b""

        def sendall(self, data):
            self.sent += data

    sock = FakeBrowserSocket()
    frame = b"\x82\x03abc"
    relay_id = "relay-raw"
    session_id = "session-raw"
    csp._relay_to_session[relay_id] = session_id
    csp._sessions[session_id] = {
        "cs_ws_sessions": {"ws-raw": {"browser_sock": sock}},
    }
    try:
        csp.dispatch_cs_ws_data(
            relay_id, "ws-raw", base64.b64encode(frame).decode("ascii"), opcode=-1)
        assert sock.sent == frame
    finally:
        csp._sessions.pop(session_id, None)
        csp._relay_to_session.pop(relay_id, None)


def test_code_server_worker_does_not_pass_base_path_to_process():
    src = open("pawflow_relay/worker.py", encoding="utf-8").read()
    start = src.index('if action == "start_code_server":')
    stop = src.index('# -- Code-server WS tunnel --', start)
    start_block = src[start:stop]
    assert '_public_base_path = msg.get("base_path", "")' in start_block
    assert '"--base-path"' not in start_block
    assert '"--folder-uri"' not in start_block
    assert '_abs_proxy_base_path = _public_base_path.rstrip("/")' in start_block
    assert '"--abs-proxy-base-path", _abs_proxy_base_path' in start_block
    assert 'str(Path(root_dir).resolve())' in start_block
    assert '"--disable-workspace-trust"' in start_block
    assert '"upstream_base_path": _upstream_base_path' in start_block


def test_local_code_server_uses_abs_proxy_base_path_not_base_path():
    src = open("pawflow_relay/thread.py", encoding="utf-8").read()
    start = src.index('def _host_start_local_code_server')
    start_block = src[start:]

    assert 'base_path = req.get("base_path", "")' in start_block
    assert 'abs_proxy_base_path = base_path.rstrip("/")' in start_block
    assert '"--base-path"' not in start_block
    assert '"--abs-proxy-base-path", abs_proxy_base_path' in start_block


def test_code_server_worker_starts_with_isolated_profile_and_no_updates():
    src = open("pawflow_relay/worker.py", encoding="utf-8").read()
    start = src.index('if action == "start_code_server":')
    stop = src.index('# -- Code-server WS tunnel --', start)
    start_block = src[start:stop]

    assert '"--user-data-dir"' in start_block
    assert '"--extensions-dir"' in start_block
    assert '"--disable-extensions"' not in start_block
    assert '"--disable-extension"' not in start_block
    assert '"extensions.autoCheckUpdates": False' in start_block
    assert '"extensions.autoUpdate": False' in start_block
    assert '"github.gitAuthentication": False' in start_block
    assert '"extensions.ignoreRecommendations": True' in start_block
    assert '_cs_env["EXTENSIONS_GALLERY"] = "{}"' in start_block
    assert 'env=_cs_env' in start_block


def test_code_server_worker_keeps_ws_frames_ordered_outside_pool():
    src = open("pawflow_relay/worker.py", encoding="utf-8").read()
    start = src.index('elif msg.get("type") == "command":')
    stop = src.index('with _inflight_lock:', start)
    command_prefix = src[start:stop]

    assert 'msg.get("action") in ("cs_ws_send", "cs_ws_close")' in command_prefix
    assert "_execute_command(msg)" in command_prefix
    assert "continue" in command_prefix
    assert "_pool.submit" not in command_prefix


def test_code_server_worker_forwards_leftover_backend_ws_frames():
    src = open("pawflow_relay/worker.py", encoding="utf-8").read()
    start = src.index('if action == "cs_ws_open":')
    stop = src.index('if action == "cs_ws_send":', start)
    ws_open_block = src[start:stop]

    assert "pass  # Leftover bytes will be read by the reader thread" not in ws_open_block
    assert "def _forward_cs_ws_frame" in ws_open_block
    assert "if _leftover:" in ws_open_block
    assert "_forward_cs_ws_frame(_ws_sid, _leftover" in ws_open_block
    assert ws_open_block.index("if _leftover:") < ws_open_block.index("_t.start()")


def test_code_server_worker_strips_browser_headers_from_backend_ws_handshake():
    src = open("pawflow_relay/worker.py", encoding="utf-8").read()
    start = src.index('if action == "cs_ws_open":')
    stop = src.index('if action == "cs_ws_send":', start)
    ws_open_block = src[start:stop]

    assert "Do not forward browser/proxy headers" in ws_open_block
    assert 'X-Forwarded-*' in ws_open_block
    assert '_hdr_lines.append(f"{_hk}: {_hv}")' not in ws_open_block
    assert '_ws_protocol = (_ws_headers.get("Sec-WebSocket-Protocol")' in ws_open_block
    assert '_hdr_lines.append(f"Sec-WebSocket-Protocol: {_ws_protocol}")' in ws_open_block
