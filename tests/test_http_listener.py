"""Tests for HTTP Listener Service, httpReceiver, handleHTTPResponse, validateHTTPAuth."""

import json
import queue
import socket
import threading
import time
import urllib.request
import urllib.error
import pytest

from services.http_listener_service import (
    HTTPListenerService, PendingRequest, RouteRegistry,
    RouteConflictError, RouteEntry, _HTTPServerWithRegistry,
    _RequestHandler, _emit_timing_summary, _request_action_label, _SECURITY_HEADERS,
    _GlobalRateLimiter, _rate_limit_policy,
)
from services.http_auth_service import HTTPAuthService, AuthValidationResult
from tasks.io.http_receiver import HTTPReceiverTask
from tasks.io.handle_http_response import HandleHTTPResponseTask
from tasks.io.validate_http_auth import ValidateHTTPAuthTask
from tasks.io.serve_file import ServeFileTask
from core import FlowFile


def _create_test_session():
    """Create a test session in SecurityManager and return the token."""
    from core.security import SecurityManager, Role
    sm = SecurityManager.get_instance()
    if "test_user" not in sm._users:
        sm.create_user("test_user", "test", Role.ADMIN)
    user = sm.get_user("test_user")
    session = sm._create_session(user)
    return session.session_id


def _auth_headers(token):
    """Return headers dict with auth cookie."""
    return {"Cookie": f"pawflow_token={token}"}


def _create_expired_test_session():
    """Create an expired SecurityManager session and return the token."""
    from core.security import SecurityManager, Role
    sm = SecurityManager.get_instance()
    if "expired_user" not in sm._users:
        sm.create_user("expired_user", "test", Role.ADMIN)
    user = sm.get_user("expired_user")
    session = sm._create_session(user)
    session.expires_at = time.time() - 10
    sm._save_sessions()
    return session.session_id


def test_sse_timing_summary_uses_respond_not_stream_lifetime(caplog):
    req = PendingRequest(
        request_id="abcdef123456",
        method="GET",
        path="/api/agent/events",
        headers={},
        body=b"",
    )
    req.response_status = 200
    base = time.monotonic()
    req.timing = {
        "recv": base,
        "dispatch": base,
        "enqueue": base,
        "dequeue": base + 0.01,
        "respond": base + 0.25,
        "send": base + 128.0,
    }

    with caplog.at_level("DEBUG", logger="services.http_listener_service"):
        _emit_timing_summary(req)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "total=250ms" in logged
    assert "respond→send" not in logged
    assert "total=128" not in logged



def test_security_headers_and_global_rate_limit_policy_are_present():
    assert "Content-Security-Policy" in _SECURITY_HEADERS
    csp = _SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-src 'self' blob: http: https:" in csp
    assert "https://cdn.jsdelivr.net" in csp
    assert "connect-src 'self' ws: wss: https://cdn.jsdelivr.net https://esm.sh" in csp
    assert "X-Frame-Options" in _SECURITY_HEADERS
    assert _SECURITY_HEADERS["Permissions-Policy"] == "camera=(), microphone=(self), geolocation=()"
    assert _rate_limit_policy("/auth/login") is None
    assert _rate_limit_policy("/auth/login/google") is None
    assert _rate_limit_policy("/auth/callback")[0] == "login"
    assert _rate_limit_policy("/_gateway")[0] == "login"
    assert _rate_limit_policy("/api/ui")[0] == "api"
    assert _rate_limit_policy("/health") is None

    limiter = _GlobalRateLimiter()
    assert limiter.allow("ip", "api", 2, 60.0)[0] is True
    assert limiter.allow("ip", "api", 2, 60.0)[0] is True
    ok, retry_after = limiter.allow("ip", "api", 2, 60.0)
    assert ok is False
    assert retry_after > 0


def test_code_routes_do_not_inject_x_frame_options():
    import io

    class FakeHandler(_RequestHandler):
        def send_header(self, name, value):
            self.sent_headers.append((name, value))
            self._headers_buffer.append(f"{name}: {value}".encode("latin-1"))

    def make_handler(path):
        handler = object.__new__(FakeHandler)
        handler.path = path
        handler._headers_buffer = []
        handler.sent_headers = []
        handler.request_version = "HTTP/1.1"
        handler.wfile = io.BytesIO()
        return handler

    normal = make_handler("/chat")
    normal.end_headers()
    assert ("X-Frame-Options", "SAMEORIGIN") in normal.sent_headers

    code = make_handler("/code/session/token/")
    code.end_headers()
    assert not any(name == "X-Frame-Options" for name, _ in code.sent_headers)
    assert ("Cross-Origin-Embedder-Policy", "require-corp") in code.sent_headers
    assert ("Cross-Origin-Opener-Policy", "same-origin") in code.sent_headers


def test_request_action_label_extracts_api_ui_action_only():
    req = PendingRequest(
        request_id="rid",
        method="POST",
        path="/api/ui",
        headers={},
        body=json.dumps({"action": "list_resources", "message": "secret body"}).encode(),
    )
    assert _request_action_label(req) == "list_resources"

    other = PendingRequest(
        request_id="rid2",
        method="POST",
        path="/api/agent",
        headers={},
        body=json.dumps({"action": "send_message"}).encode(),
    )
    assert _request_action_label(other) == ""


class _CloseTrackingSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_http_dispatch_saturation_closes_without_spawning(monkeypatch):
    monkeypatch.setenv("PAWFLOW_HTTP_MAX_DISPATCH_THREADS", "1")
    server = _HTTPServerWithRegistry(
        ("127.0.0.1", 0), object, RouteRegistry())
    try:
        assert server._dispatch_slots.acquire(blocking=False)
        sock = _CloseTrackingSocket()

        server.process_request(sock, ("127.0.0.1", 12345))

        assert sock.closed is True
        assert server._dispatch_rejected == 1
        assert server._dispatch_active == 0
    finally:
        server._dispatch_slots.release()
        server.server_close()


def test_http_dispatch_slot_released_after_request(monkeypatch):
    monkeypatch.setenv("PAWFLOW_HTTP_MAX_DISPATCH_THREADS", "1")
    server = _HTTPServerWithRegistry(
        ("127.0.0.1", 0), object, RouteRegistry())
    done = threading.Event()
    try:
        def _fake_dispatch(_request, _client_address):
            done.set()

        server._dispatch_request = _fake_dispatch
        server.process_request(_CloseTrackingSocket(), ("127.0.0.1", 12345))

        assert done.wait(timeout=2)
        deadline = time.time() + 2
        while time.time() < deadline and server._dispatch_active != 0:
            time.sleep(0.01)
        assert server._dispatch_active == 0
        assert server._dispatch_slots.acquire(blocking=False)
    finally:
        try:
            server._dispatch_slots.release()
        except ValueError:
            pass
        server.server_close()


def test_long_lived_dispatch_transfer_releases_short_slot(monkeypatch):
    monkeypatch.setenv("PAWFLOW_HTTP_MAX_DISPATCH_THREADS", "1")
    monkeypatch.setenv("PAWFLOW_HTTP_MAX_LONG_DISPATCH_THREADS", "1")
    server = _HTTPServerWithRegistry(
        ("127.0.0.1", 0), object, RouteRegistry())
    try:
        assert server._dispatch_slots.acquire(blocking=False)
        with server._dispatch_lock:
            server._dispatch_active += 1
        server._dispatch_context.short_slot_released = False
        server._dispatch_context.long_slot_acquired = False

        assert server.transfer_current_dispatch_to_long_lived("test") is True

        assert server._dispatch_active == 0
        assert server._long_dispatch_active == 1
        assert server._dispatch_slots.acquire(blocking=False)
    finally:
        try:
            server._dispatch_slots.release()
        except ValueError:
            pass
        if getattr(server._dispatch_context, "long_slot_acquired", False):
            server._release_long_dispatch_slot()
        server.server_close()


def test_websocket_only_route_callbacks_complete_plain_http(monkeypatch):
    from services import http_listener_service
    from tasks.ai.actions import service_flow

    class FakeFlowFile:
        def get_attribute(self, key):
            return "19990" if key == "http.listener.port" else ""

    class FakeListener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return []

        def register_route(self, method, pattern, owner_id, callback,
                           ws_handler=None, public=False, private_only=False):
            self.routes.append({
                "method": method,
                "pattern": pattern,
                "owner": owner_id,
                "callback": callback,
                "ws_handler": ws_handler,
                "public": public,
                "private_only": private_only,
            })

    listener = FakeListener()
    monkeypatch.setitem(http_listener_service._instances, 19990, listener)

    service_flow._ensure_vnc_routes(FakeFlowFile())
    service_flow._ensure_terminal_routes(FakeFlowFile())

    ws_only_patterns = {
        "/vnc/{session_id}/{token}/websockify",
        "/audio/{session_id}/{token}/stream",
        "/terminal/{session_id}/{token}",
    }
    callbacks = [r["callback"] for r in listener.routes
                 if r["pattern"] in ws_only_patterns]

    assert len(callbacks) == 3
    for callback in callbacks:
        req = PendingRequest(
            request_id="rid",
            method="GET",
            path="/ws-only",
            headers={},
            body=b"",
        )
        callback(req)
        assert req.completed is True
        assert req.response_status == 426
        assert b"WebSocket upgrade required" in req.response_body


# ---------------------------------------------------------------------------
# RouteRegistry tests
# ---------------------------------------------------------------------------

class TestRouteRegistry:

    def test_register_and_match(self):
        reg = RouteRegistry()
        cb = lambda req: None
        reg.register("GET", "/api/hello", "flow1", cb)
        result = reg.match("GET", "/api/hello")
        assert result is not None
        entry, params = result
        assert entry.method == "GET"
        assert entry.pattern == "/api/hello"
        assert params == {}

    def test_path_params(self):
        reg = RouteRegistry()
        reg.register("GET", "/api/users/{id}", "flow1", lambda r: None)
        result = reg.match("GET", "/api/users/42")
        assert result is not None
        entry, params = result
        assert params == {"id": "42"}

    def test_multiple_path_params(self):
        reg = RouteRegistry()
        reg.register("GET", "/api/{org}/repos/{repo}", "flow1", lambda r: None)
        result = reg.match("GET", "/api/acme/repos/widget")
        assert result is not None
        _, params = result
        assert params == {"org": "acme", "repo": "widget"}

    def test_no_match_returns_none(self):
        reg = RouteRegistry()
        reg.register("GET", "/api/hello", "flow1", lambda r: None)
        assert reg.match("POST", "/api/hello") is None
        assert reg.match("GET", "/api/other") is None

    def test_conflict_error(self):
        reg = RouteRegistry()
        reg.register("GET", "/api/x", "flow1", lambda r: None)
        with pytest.raises(RouteConflictError):
            reg.register("GET", "/api/x", "flow2", lambda r: None)

    def test_idempotent_same_owner(self):
        reg = RouteRegistry()
        cb1 = lambda r: None
        cb2 = lambda r: None
        reg.register("GET", "/api/x", "flow1", cb1)
        reg.register("GET", "/api/x", "flow1", cb2)
        assert len(reg.get_routes()) == 1

    def test_unregister(self):
        reg = RouteRegistry()
        reg.register("GET", "/a", "flow1", lambda r: None)
        reg.register("POST", "/b", "flow1", lambda r: None)
        reg.register("GET", "/c", "flow2", lambda r: None)
        reg.unregister("flow1")
        routes = reg.get_routes()
        assert len(routes) == 1
        assert routes[0]["owner"] == "flow2"

    def test_get_routes(self):
        reg = RouteRegistry()
        reg.register("GET", "/a", "f1", lambda r: None)
        reg.register("POST", "/b", "f2", lambda r: None)
        routes = reg.get_routes()
        assert len(routes) == 2
        methods = {r["method"] for r in routes}
        assert methods == {"GET", "POST"}


# ---------------------------------------------------------------------------
# PendingRequest tests
# ---------------------------------------------------------------------------

class TestPendingRequest:

    def test_wait_blocks_until_complete(self):
        req = PendingRequest(
            request_id="abc", method="GET", path="/",
            headers={}, body=b"",
        )
        assert req.completed is False
        def respond():
            time.sleep(0.1)
            req.complete(200, {"Content-Type": "text/plain"}, b"OK")
        t = threading.Thread(target=respond)
        t.start()
        req.wait()
        assert req.completed is True
        assert req.response_status == 200
        assert req.response_body == b"OK"
        t.join()

# ---------------------------------------------------------------------------
# HTTPListenerService tests
# ---------------------------------------------------------------------------

class TestHTTPListenerService:

    def test_create_and_connect(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19876})
        svc.connect()
        assert svc.is_connected()
        svc.disconnect()

    def test_register_route(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19877})
        svc.connect()
        try:
            svc.register_route("GET", "/test", "owner1", lambda r: None)
            routes = svc.get_routes()
            assert len(routes) == 1
            assert routes[0]["method"] == "GET"
        finally:
            svc.disconnect()

    def test_unregister_routes(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19878})
        svc.connect()
        try:
            svc.register_route("GET", "/a", "owner1", lambda r: None)
            svc.register_route("POST", "/b", "owner1", lambda r: None)
            svc.unregister_routes("owner1")
            assert len(svc.get_routes()) == 0
        finally:
            svc.disconnect()

    def test_submit_response(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19879})
        svc.connect()
        try:
            results = []
            def on_request(req):
                results.append(req)
            svc.register_route("GET", "/hello", "test", on_request)
            _tok = _create_test_session()

            # Make HTTP request in a thread
            response_holder = [None]
            def make_request():
                try:
                    req = urllib.request.Request(
                        "http://127.0.0.1:19879/hello",
                        method="GET",
                        headers={"Cookie": f"pawflow_token={_tok}"},
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    response_holder[0] = (resp.status, resp.read())
                except Exception as e:
                    response_holder[0] = e

            t = threading.Thread(target=make_request)
            t.start()
            time.sleep(0.3)

            # Submit response
            assert len(results) == 1
            svc.submit_response(
                results[0].request_id, 200,
                {"Content-Type": "text/plain"},
                b"Hello World",
            )
            t.join(timeout=5)
            assert response_holder[0] == (200, b"Hello World")
        finally:
            svc.disconnect()

    def test_404_on_no_match(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19880})
        svc.connect()
        try:
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:19880/nonexistent",
                    method="GET",
                        headers={"Cookie": f"pawflow_token={_create_test_session()}"},
                    )
                urllib.request.urlopen(req, timeout=5)
                assert False, "Should have raised"
            except urllib.error.HTTPError as e:
                assert e.code == 404
                body = json.loads(e.read())
                assert "Not Found" in body["error"]
        finally:
            svc.disconnect()

    def test_no_timeout_blocks(self):
        """PendingRequest.wait() blocks indefinitely (no 504). Verify basic dispatch works."""
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19881})
        svc.connect()
        try:
            svc.register_route("GET", "/fast", "test", lambda r: r.complete(200, {}, b"ok"))
            req = urllib.request.Request(
                "http://127.0.0.1:19881/fast",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            assert resp.status == 200
        finally:
            svc.disconnect()

    def test_flow_response_wait_has_no_configured_timeout(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19915})
        svc.connect()
        try:
            def delayed(req):
                def respond():
                    time.sleep(0.15)
                    req.complete(200, {"Content-Type": "text/plain"}, b"late-ok")
                threading.Thread(target=respond, daemon=True).start()

            svc.register_route("GET", "/delayed", "test", delayed)
            req = urllib.request.Request(
                "http://127.0.0.1:19915/delayed",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            assert resp.status == 200
            assert resp.read() == b"late-ok"
        finally:
            svc.disconnect()

    def test_stalled_flow_response_does_not_occupy_short_dispatch_slot(self, monkeypatch):
        monkeypatch.setenv("PAWFLOW_HTTP_MAX_DISPATCH_THREADS", "1")
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19917})
        svc.connect()
        first_dispatched = threading.Event()
        first_result = []
        try:
            def blocked(req):
                first_dispatched.set()

            def fast(req):
                req.complete(200, {"Content-Type": "text/plain"}, b"fast-ok")

            svc.register_route("GET", "/blocked", "test", blocked)
            svc.register_route("GET", "/fast-after-blocked", "test", fast)
            token = _create_test_session()

            def make_blocked_request():
                try:
                    req = urllib.request.Request(
                        "http://127.0.0.1:19917/blocked",
                        method="GET",
                        headers={"Cookie": f"pawflow_token={token}"},
                    )
                    urllib.request.urlopen(req, timeout=5).read()
                except Exception as exc:
                    first_result.append(exc)

            t = threading.Thread(target=make_blocked_request, daemon=True)
            t.start()
            assert first_dispatched.wait(timeout=2)
            deadline = time.time() + 2
            while time.time() < deadline and svc._server._dispatch_active != 0:
                time.sleep(0.01)
            assert svc._server._dispatch_active == 0
            assert svc._server._long_dispatch_active == 1

            req = urllib.request.Request(
                "http://127.0.0.1:19917/fast-after-blocked",
                method="GET",
                headers={"Cookie": f"pawflow_token={token}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            assert resp.status == 200
            assert resp.read() == b"fast-ok"
        finally:
            svc.disconnect()

    def test_http_listener_schema_does_not_expose_request_timeout(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19916})
        try:
            assert "request_timeout" not in svc.get_parameter_schema()
        finally:
            svc.disconnect()

    def test_chat_js_assets_are_served_without_flow_dispatch(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19911})
        svc.connect()
        try:
            dispatched = []
            svc.register_route(
                "GET", "/chat/js/{path}", "test",
                lambda req: dispatched.append(req),
            )
            req = urllib.request.Request(
                "http://127.0.0.1:19911/chat/js/i18n.js",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)

            assert resp.status == 200
            assert b"function" in resp.read()
            assert resp.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
            assert dispatched == []
        finally:
            svc.disconnect()

    def test_chat_js_nested_assets_are_served_without_flow_dispatch(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19917})
        svc.connect()
        try:
            dispatched = []
            svc.register_route(
                "GET", "/chat/js/{path}", "test",
                lambda req: dispatched.append(req),
            )
            req = urllib.request.Request(
                "http://127.0.0.1:19917/chat/js/assets/favicon.ico",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)

            assert resp.status == 200
            assert resp.read().startswith(b"\x00\x00\x01\x00")
            assert resp.headers.get("Content-Type") in {
                "image/vnd.microsoft.icon", "image/x-icon",
                "application/octet-stream",
            }
            assert dispatched == []
        finally:
            svc.disconnect()

    def test_filestore_download_streams_without_flow_dispatch(self, tmp_path, monkeypatch):
        from core.file_store import FileStore

        store = FileStore(base_dir=str(tmp_path / "files"))
        monkeypatch.setattr(FileStore, "_instance", store)
        size = 2 * 1024 * 1024 + 3
        file_id = store.store(
            "conversation.pfconv.zip",
            b"x" * size,
            "application/zip",
            user_id="test_user",
            conversation_id="conv-1",
        )

        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19912})
        svc.connect()
        try:
            dispatched = []
            svc.register_route(
                "GET", "/files/{file_id}/{filename+}", "test",
                lambda req: dispatched.append(req),
            )
            req = urllib.request.Request(
                f"http://127.0.0.1:19912/files/{file_id}/conversation.pfconv.zip",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)

            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "application/zip"
            assert resp.headers.get("Content-Length") == str(size)
            assert resp.read() == b"x" * size
            assert dispatched == []
        finally:
            svc.disconnect()

    def test_serve_file_flow_streams_without_large_flowfile_body(self, tmp_path, monkeypatch):
        from core.file_store import FileStore

        store = FileStore(base_dir=str(tmp_path / "files"))
        monkeypatch.setattr(FileStore, "_instance", store)
        size = 2 * 1024 * 1024 + 7
        file_id = store.store(
            "large.bin",
            b"y" * size,
            "application/octet-stream",
            user_id="test_user",
            conversation_id="conv-1",
        )

        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19913})
        svc.connect()
        try:
            def on_request(req):
                ff = FlowFile(content=b"")
                ff.set_attribute("http.request.id", req.request_id)
                ff.set_attribute("http.path.file_id", req.path_params["file_id"])
                ff.set_attribute("http.auth.principal", req.auth_user_id)
                served = ServeFileTask({}).execute(ff)[0]
                assert served.get_content() == b""
                assert served.get_attribute("http.response.file_path")
                responder = HandleHTTPResponseTask({"service_id": "http_listener"})
                responder.get_service = lambda service_id: svc
                responder.execute(served)

            svc.register_route("GET", "/files/{file_id}", "test", on_request)
            req = urllib.request.Request(
                f"http://127.0.0.1:19913/files/{file_id}",
                method="GET",
                headers={"Cookie": f"pawflow_token={_create_test_session()}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)

            assert resp.status == 200
            assert resp.headers.get("Content-Length") == str(size)
            assert resp.read() == b"y" * size
        finally:
            svc.disconnect()

    def test_pending_count(self):
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19882})
        svc.connect()
        try:
            assert svc.get_pending_count() == 0
        finally:
            svc.disconnect()


# ---------------------------------------------------------------------------
# HTTPReceiverTask tests
# ---------------------------------------------------------------------------

class TestHTTPReceiverTask:

    def test_has_pending_input_default_false(self):
        task = HTTPReceiverTask({"service_id": "svc1", "routes": []})
        assert task.has_pending_input() is False

    def test_on_request_enqueues(self):
        task = HTTPReceiverTask({"service_id": "svc1", "routes": []})
        task._registered = True  # Skip service registration
        req = PendingRequest(
            request_id="r1", method="GET", path="/test",
            headers={"Host": "localhost"}, body=b"body",
            path_params={"id": "42"}, query_string="foo=bar",
        )
        task._on_request(req, "GET:/test")
        assert task.has_pending_input() is True

        results = task.execute(None)
        assert len(results) == 1
        ff = results[0]
        assert ff.get_attribute("http.request.id") == "r1"
        assert ff.get_attribute("http.method") == "GET"
        assert ff.get_attribute("http.path") == "/test"
        assert ff.get_attribute("http.query") == "foo=bar"
        assert ff.get_attribute("http.path.id") == "42"
        assert ff.get_attribute("route.relationship") == "GET:/test"
        assert ff.get_content() == b"body"

    def test_execute_empty_queue(self):
        task = HTTPReceiverTask({"service_id": "svc1", "routes": []})
        # Shouldn't raise, returns empty
        task._registered = True  # Skip registration
        results = task.execute(None)
        assert results == []


# ---------------------------------------------------------------------------
# HandleHTTPResponseTask tests
# ---------------------------------------------------------------------------

class TestHandleHTTPResponseTask:

    def test_missing_request_id(self):
        task = HandleHTTPResponseTask({"service_id": "svc1"})
        ff = FlowFile(content=b"test")
        with pytest.raises(RuntimeError, match="Missing http.request.id"):
            task.execute(ff)

    def test_status_code_from_config(self):
        """Verify config status_code is used when no attribute override."""
        task = HandleHTTPResponseTask({
            "service_id": "svc1",
            "status_code": 201,
        })
        # We need to test the logic without a real service
        # Just verify the config is parsed
        assert task.config["status_code"] == 201

    def test_headers_override_from_attributes(self):
        task = HandleHTTPResponseTask({
            "service_id": "svc1",
            "content_type": "text/html",
        })
        ff = FlowFile(content=b"<h1>Hi</h1>")
        ff.set_attribute("http.request.id", "req1")
        ff.set_attribute("http.response.header.X-Custom", "value")
        ff.set_attribute("http.response.status", "201")
        # Without service, this will raise — but we verified the attribute parsing logic
        # in the integration test above


# ---------------------------------------------------------------------------
# HTTPAuthService tests
# ---------------------------------------------------------------------------

class TestHTTPAuthService:

    def test_bearer_valid(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["abc123"]})
        result = svc.validate("Bearer abc123")
        assert result.valid is True
        assert "abc123" in result.principal

    def test_bearer_invalid(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["abc123"]})
        result = svc.validate("Bearer wrong")
        assert result.valid is False
        assert result.status_code == 401

    def test_no_auth_header(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["t"]})
        result = svc.validate(None)
        assert result.valid is False
        assert result.status_code == 401

    def test_malformed_header(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["t"]})
        result = svc.validate("malformed")
        assert result.valid is False

    def test_basic_valid(self):
        import base64
        svc = HTTPAuthService({
            "auth_type": "basic",
            "users": {"admin": "secret"},
        })
        creds = base64.b64encode(b"admin:secret").decode()
        result = svc.validate(f"Basic {creds}")
        assert result.valid is True
        assert result.principal == "admin"

    def test_basic_invalid_password(self):
        import base64
        svc = HTTPAuthService({
            "auth_type": "basic",
            "users": {"admin": "secret"},
        })
        creds = base64.b64encode(b"admin:wrong").decode()
        result = svc.validate(f"Basic {creds}")
        assert result.valid is False

    def test_basic_unknown_user(self):
        import base64
        svc = HTTPAuthService({
            "auth_type": "basic",
            "users": {"admin": "secret"},
        })
        creds = base64.b64encode(b"unknown:pass").decode()
        result = svc.validate(f"Basic {creds}")
        assert result.valid is False

    def test_add_remove_token(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": []})
        result = svc.validate("Bearer tok1")
        assert result.valid is False
        svc.add_token("tok1")
        result = svc.validate("Bearer tok1")
        assert result.valid is True
        svc.remove_token("tok1")
        result = svc.validate("Bearer tok1")
        assert result.valid is False

    def test_add_remove_user(self):
        import base64
        svc = HTTPAuthService({"auth_type": "basic", "users": {}})
        svc.add_user("bob", "pass")
        creds = base64.b64encode(b"bob:pass").decode()
        result = svc.validate(f"Basic {creds}")
        assert result.valid is True
        svc.remove_user("bob")
        result = svc.validate(f"Basic {creds}")
        assert result.valid is False

    def test_custom_validator(self):
        svc = HTTPAuthService({"auth_type": "custom"})
        svc.set_custom_validator(
            lambda scheme, creds: AuthValidationResult(
                valid=(creds == "magic"), principal="wizard"
            )
        )
        assert svc.validate("Bearer magic").valid is True
        assert svc.validate("Bearer other").valid is False

    def test_unsupported_scheme(self):
        svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["t"]})
        result = svc.validate("Digest abc")
        assert result.valid is False

    def test_realm_property(self):
        svc = HTTPAuthService({"realm": "TestRealm"})
        assert svc.realm == "TestRealm"


# ---------------------------------------------------------------------------
# ValidateHTTPAuthTask tests
# ---------------------------------------------------------------------------

class TestValidateHTTPAuthTask:

    def _make_task_with_services(self, auth_config, listener_svc=None):
        task = ValidateHTTPAuthTask({
            "auth_service_id": "auth",
            "listener_service_id": "listener",
            "auto_respond": bool(listener_svc),
        })
        services = {"auth": HTTPAuthService(auth_config)}
        if listener_svc:
            services["listener"] = listener_svc
        task.set_services(services)
        return task

    def test_valid_bearer(self):
        task = self._make_task_with_services(
            {"auth_type": "bearer", "tokens": ["tok1"]}
        )
        ff = FlowFile(content=b"data")
        ff.set_attribute("http.header.authorization", "Bearer tok1")
        results = task.execute(ff)
        assert len(results) == 1
        assert results[0].get_attribute("http.auth.valid") == "true"

    def test_invalid_bearer_routes_to_failure(self):
        task = self._make_task_with_services(
            {"auth_type": "bearer", "tokens": ["tok1"]}
        )
        ff = FlowFile(content=b"data")
        ff.set_attribute("http.header.authorization", "Bearer wrong")
        results = task.execute(ff)
        assert len(results) == 1
        assert results[0].get_attribute("http.auth.valid") == "false"
        assert results[0].get_attribute("route.relationship") == "failure"

    def test_no_auth_header(self):
        task = self._make_task_with_services(
            {"auth_type": "bearer", "tokens": ["t"]}
        )
        ff = FlowFile(content=b"data")
        ff.set_attribute("http.request.id", "r1")
        results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "false"


# ---------------------------------------------------------------------------
# Integration: full HTTP request-response cycle
# ---------------------------------------------------------------------------

class TestHTTPListenerIntegration:

    def test_listener_rejects_expired_http_session_before_route_dispatch(self):
        """The central listener auth must not accept expired sessions.

        SecurityManager.get_session() returns expired Session objects so
        callers can try OAuth refresh; HTTPListenerService does not run
        that refresh flow, so it must reject and revoke them instead of
        stamping auth_user_id on sensitive route requests.
        """
        port = 19931
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc.connect()
        try:
            token = _create_expired_test_session()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/secure",
                method="GET",
                headers={"Cookie": f"pawflow_token={token}",
                         "Accept": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req, timeout=5)
            assert exc.value.code == 401
        finally:
            svc.disconnect()

    def test_listener_rejects_expired_websocket_session_before_upgrade(self):
        port = 19932
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc.connect()
        try:
            token = _create_expired_test_session()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall((
                    "GET /ws/secure HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Cookie: pawflow_token={token}\r\n"
                    "\r\n"
                ).encode("ascii"))
                data = sock.recv(1024)
            assert b"401 Unauthorized" in data
        finally:
            svc.disconnect()

    def test_public_websocket_route_skips_private_gateway_and_session_auth(self):
        port = 19934
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})

        class _Gateway:
            def is_enabled(self):
                return True

            def is_banned(self, _ip):
                return False

            def check_ws(self, *_args, **_kwargs):
                return True

        def _ws_handler(sock, _path_params, _meta):
            sock.close()

        svc.connect()
        try:
            svc._server._private_gateway = _Gateway()
            svc.register_route(
                "GET", "/ws/public", "test", callback=None,
                ws_handler=_ws_handler, public=True, private_only=True)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall((
                    "GET /ws/public HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "\r\n"
                ).encode("ascii"))
                data = sock.recv(1024)
            assert b"101 Switching Protocols" in data
        finally:
            svc.disconnect()

    def test_websocket_headers_are_case_insensitive(self):
        port = 19936
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})

        def _ws_handler(sock, _path_params, _meta):
            sock.close()

        svc.connect()
        try:
            svc.register_route(
                "GET", "/ws/caddy", "test", callback=None,
                ws_handler=_ws_handler, public=True)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall((
                    "GET /ws/caddy HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-Websocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-Websocket-Version: 13\r\n"
                    "\r\n"
                ).encode("ascii"))
                data = sock.recv(1024)
            assert b"101 Switching Protocols" in data
        finally:
            svc.disconnect()

    def test_relay_websocket_accepts_internal_cookie_with_private_gateway(self, monkeypatch):
        from core import internal_auth

        port = 19935
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})

        class _Gateway:
            def is_enabled(self):
                return True

            def is_banned(self, _ip):
                return False

            def check_ws(self, *_args, **_kwargs):
                return True

        def _ws_handler(sock, _path_params, _meta):
            sock.close()

        token = internal_auth.mint_token()
        svc.connect()
        try:
            svc._server._private_gateway = _Gateway()
            svc.register_route(
                "GET", "/ws/relay/MyWorkspace", "test", callback=None,
                ws_handler=_ws_handler, private_only=True)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall((
                    "GET /ws/relay/MyWorkspace HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Cookie: pawflow_internal={token}\r\n"
                    "\r\n"
                ).encode("ascii"))
                data = sock.recv(1024)
            assert b"101 Switching Protocols" in data
        finally:
            internal_auth.revoke_token(token)
            svc.disconnect()

    def test_builtin_health_endpoint_is_public(self):
        port = 19933
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc.connect()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=5) as resp:
                assert resp.status == 200
                assert json.loads(resp.read().decode()) == {"ok": True}
        finally:
            svc.disconnect()

    def test_full_cycle(self):
        """Test: HTTP request -> httpReceiver -> handleHTTPResponse -> HTTP response."""
        port = 19883
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc.connect()

        try:
            # Setup httpReceiver
            receiver = HTTPReceiverTask({
                "service_id": "listener",
                "routes": [{"method": "GET", "pattern": "/api/greet/{name}"}],
            })
            receiver.set_services({"listener": svc})
            receiver._ensure_routes_registered()

            # Setup handleHTTPResponse
            responder = HandleHTTPResponseTask({
                "service_id": "listener",
                "content_type": "text/html",
            })
            responder.set_services({"listener": svc})

            # Make HTTP request in background
            response_holder = [None]
            def make_request():
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/greet/World",
                        method="GET",
                        headers={"Cookie": f"pawflow_token={_create_test_session()}"},
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    response_holder[0] = (resp.status, resp.read().decode())
                except Exception as e:
                    response_holder[0] = e

            t = threading.Thread(target=make_request)
            t.start()
            time.sleep(0.3)

            # Receiver picks up the request
            assert receiver.has_pending_input() is True
            ffs = receiver.execute(None)
            assert len(ffs) == 1
            ff = ffs[0]
            assert ff.get_attribute("http.path.name") == "World"

            # Simulate flow processing: build response
            name = ff.get_attribute("http.path.name")
            ff.set_content(f"<h1>Hello {name}!</h1>".encode())

            # Send response
            results = responder.execute(ff)
            assert len(results) == 1
            assert results[0].get_attribute("http.response.sent") == "true"

            t.join(timeout=5)
            assert response_holder[0] == (200, "<h1>Hello World!</h1>")
        finally:
            svc.disconnect()

    def test_full_cycle_with_auth(self):
        """HTTP request with auth validation."""
        port = 19884
        listener_svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        auth_svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["validtoken"]})
        listener_svc.connect()

        try:
            # Setup receiver
            receiver = HTTPReceiverTask({
                "service_id": "listener",
                "routes": [{"method": "GET", "pattern": "/secure"}],
            })
            receiver.set_services({"listener": listener_svc})
            receiver._ensure_routes_registered()

            # Setup auth validator
            validator = ValidateHTTPAuthTask({
                "auth_service_id": "auth",
                "listener_service_id": "listener",
                "auto_respond": True,
            })
            validator.set_services({"auth": auth_svc, "listener": listener_svc})

            # Request WITH valid session cookie (HTTP listener auth) AND
            # valid bearer token (flow-level HTTPAuthService auth)
            _real_token = _create_test_session()
            response_holder = [None]
            def make_authed_request():
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/secure",
                        method="GET",
                        headers={
                            "Cookie": f"pawflow_token={_real_token}",
                            "Authorization": "Bearer validtoken",
                        },
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    response_holder[0] = (resp.status, resp.read())
                except urllib.error.HTTPError as e:
                    response_holder[0] = ("error", e.code)
                except Exception as e:
                    response_holder[0] = e

            t = threading.Thread(target=make_authed_request)
            t.start()
            time.sleep(0.3)

            ffs = receiver.execute(None)
            ff = ffs[0]
            results = validator.execute(ff)
            assert results[0].get_attribute("http.auth.valid") == "true"

            # Send success response
            responder = HandleHTTPResponseTask({"service_id": "listener"})
            responder.set_services({"listener": listener_svc})
            results[0].set_content(b'{"ok": true}')
            responder.execute(results[0])

            t.join(timeout=5)
            assert response_holder[0] == (200, b'{"ok": true}')

        finally:
            listener_svc.disconnect()

    def test_auth_rejection_auto_response(self):
        """Auth failure auto-responds with 401."""
        port = 19885
        listener_svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        auth_svc = HTTPAuthService({"auth_type": "bearer", "tokens": ["validtoken"]})
        listener_svc.connect()

        try:
            receiver = HTTPReceiverTask({
                "service_id": "listener",
                "routes": [{"method": "GET", "pattern": "/protected"}],
            })
            receiver.set_services({"listener": listener_svc})
            receiver._ensure_routes_registered()

            validator = ValidateHTTPAuthTask({
                "auth_service_id": "auth",
                "listener_service_id": "listener",
                "auto_respond": True,
            })
            validator.set_services({"auth": auth_svc, "listener": listener_svc})

            # Request with WRONG auth token (but valid session cookie to pass listener auth)
            _real_token = _create_test_session()
            response_holder = [None]
            def make_bad_request():
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/protected",
                        method="GET",
                        headers={
                            "Cookie": f"pawflow_token={_real_token}",
                            "Authorization": "Bearer wrongtoken",
                        },
                    )
                    urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as e:
                    response_holder[0] = e.code

            t = threading.Thread(target=make_bad_request)
            t.start()
            time.sleep(0.3)

            ffs = receiver.execute(None)
            ff = ffs[0]
            # Auth validation should auto-respond 401
            results = validator.execute(ff)
            assert results[0].get_attribute("http.auth.valid") == "false"
            assert results[0].get_attribute("http.response.sent") == "true"

            t.join(timeout=5)
            assert response_holder[0] == 401

        finally:
            listener_svc.disconnect()

    def test_custom_status_and_headers(self):
        """Test custom status code and headers in response."""
        port = 19886
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc.connect()

        try:
            receiver = HTTPReceiverTask({
                "service_id": "listener",
                "routes": [{"method": "POST", "pattern": "/create"}],
            })
            receiver.set_services({"listener": svc})
            receiver._ensure_routes_registered()

            responder = HandleHTTPResponseTask({
                "service_id": "listener",
                "status_code": 200,  # default, will be overridden
            })
            responder.set_services({"listener": svc})

            _tok = _create_test_session()
            response_holder = [None]
            def make_request():
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/create",
                        method="POST",
                        data=b'{"name": "test"}',
                        headers={
                            "Content-Type": "application/json",
                            "Cookie": f"pawflow_token={_tok}",
                        },
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    response_holder[0] = (resp.status, resp.read(), dict(resp.headers))
                except urllib.error.HTTPError as e:
                    response_holder[0] = (e.code, e.read(), dict(e.headers))

            t = threading.Thread(target=make_request)
            t.start()
            time.sleep(0.3)

            ffs = receiver.execute(None)
            ff = ffs[0]
            # Override status and add custom header
            ff.set_attribute("http.response.status", "201")
            ff.set_attribute("http.response.header.X-Custom-Id", "42")
            ff.set_attribute("http.response.header.Content-Type", "application/json")
            ff.set_content(b'{"id": 42, "created": true}')

            responder.execute(ff)
            t.join(timeout=5)

            status, body, headers = response_holder[0]
            assert status == 201
            assert json.loads(body)["id"] == 42
            assert headers.get("X-Custom-Id") == "42"

        finally:
            svc.disconnect()


# ---------------------------------------------------------------------------
# Shared port (singleton) tests
# ---------------------------------------------------------------------------

class TestSharedPort:

    def setup_method(self):
        """Ensure singleton map is clean before each test."""
        from services.http_listener_service import _instances, _instances_lock
        with _instances_lock:
            _instances.clear()

    def teardown_method(self):
        from services.http_listener_service import _instances, _instances_lock
        with _instances_lock:
            _instances.clear()

    def test_shared_port_same_singleton(self):
        """Two instances on the same port return the exact same object."""
        svc_a = HTTPListenerService({"host": "127.0.0.1", "port": 19890})
        svc_b = HTTPListenerService({"host": "127.0.0.1", "port": 19890})
        assert svc_a is svc_b

    def test_different_ports_different_instances(self):
        """Different ports create different instances."""
        svc_a = HTTPListenerService({"host": "127.0.0.1", "port": 19891})
        svc_b = HTTPListenerService({"host": "127.0.0.1", "port": 19892})
        assert svc_a is not svc_b

    def test_shared_port_different_routes(self):
        """Two flows sharing a port can register different routes."""
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19893})
        svc.connect()
        try:
            svc.register_route("GET", "/api/users", "flow_A", lambda r: None)
            svc.register_route("POST", "/api/orders", "flow_B", lambda r: None)
            routes = svc.get_routes()
            assert len(routes) == 2
            methods = {r["method"] for r in routes}
            assert methods == {"GET", "POST"}
        finally:
            svc.disconnect()

    def test_shared_port_collision(self):
        """Same route registered by different owners raises RouteConflictError."""
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19894})
        svc.connect()
        try:
            svc.register_route("GET", "/api/data", "flow_A", lambda r: None)
            with pytest.raises(RouteConflictError, match="flow_A"):
                svc.register_route("GET", "/api/data", "flow_B", lambda r: None)
        finally:
            svc.disconnect()

    def test_shared_port_ref_counting(self):
        """Flow A disconnect -> server stays up; Flow B disconnect -> server stops."""
        port = 19895
        svc = HTTPListenerService({"host": "127.0.0.1", "port": port})
        svc._ref_count = 0  # ensure clean state

        # Flow A connects
        svc.connect()
        assert svc.is_connected()
        assert svc._ref_count == 1

        # Flow B connects (same singleton)
        svc.connect()
        assert svc._ref_count == 2
        assert svc._server is not None

        # Flow A disconnects — server still up
        svc.disconnect()
        assert svc._ref_count == 1
        assert svc._server is not None

        # Flow B disconnects — server stops
        svc.disconnect()
        assert svc._ref_count == 0
        assert svc._server is None

    def test_shared_port_cleanup_unregisters_routes(self):
        """Unregistering one flow's routes leaves the other's intact."""
        svc = HTTPListenerService({"host": "127.0.0.1", "port": 19896})
        svc.connect()
        try:
            svc.register_route("GET", "/a", "flow_A", lambda r: None)
            svc.register_route("POST", "/b", "flow_A", lambda r: None)
            svc.register_route("GET", "/c", "flow_B", lambda r: None)

            # Flow A stops — unregister its routes
            svc.unregister_routes("flow_A")
            routes = svc.get_routes()
            assert len(routes) == 1
            assert routes[0]["owner"] == "flow_B"

            # Flow B's route still matches
            result = svc.registry.match("GET", "/c")
            assert result is not None
        finally:
            svc.disconnect()

    def test_singleton_survives_init_call(self):
        """Second __init__ call (via __new__ returning existing) doesn't reset state."""
        svc_a = HTTPListenerService({"host": "127.0.0.1", "port": 19897})
        svc_a.connect()
        try:
            svc_a.register_route("GET", "/test", "owner1", lambda r: None)
            assert len(svc_a.get_routes()) == 1

            # Simulate FlowParser creating "new" instance for same port
            svc_b = HTTPListenerService({"host": "127.0.0.1", "port": 19897})
            assert svc_b is svc_a
            # Routes still intact
            assert len(svc_b.get_routes()) == 1
            # Server still running
            assert svc_b._server is not None
        finally:
            svc_a.disconnect()

    def test_singleton_runtime_config_updates_tls_context(self, monkeypatch):
        """Reusing a port with final TLS config updates new connections."""
        svc_a = HTTPListenerService({
            "host": "127.0.0.1",
            "port": 19898,
            "ssl_certfile": "bootstrap.crt",
            "ssl_keyfile": "bootstrap.key",
        })
        fake_server = type("FakeServer", (), {})()
        fake_server._ssl_ctx = "ctx:bootstrap.crt"
        fake_server._sni_certs = {}
        fake_server._private_gateway = None
        svc_a._server = fake_server

        def fake_build_ssl_context(self):
            return f"ctx:{self._ssl_certfile}:{self._ssl_keyfile}"

        monkeypatch.setattr(HTTPListenerService, "_build_ssl_context", fake_build_ssl_context)
        monkeypatch.setattr(HTTPListenerService, "_resolve_private_gateway", lambda self: "gateway")

        svc_b = HTTPListenerService({
            "host": "127.0.0.1",
            "port": 19898,
            "ssl_certfile": "final.crt",
            "ssl_keyfile": "final.key",
            "private_gateway_service_id": "_private_gateway",
        })

        assert svc_b is svc_a
        assert svc_a._ssl_certfile == "final.crt"
        assert svc_a._ssl_keyfile == "final.key"
        assert fake_server._ssl_ctx == "ctx:final.crt:final.key"
        assert fake_server._private_gateway == "gateway"


# ---------------------------------------------------------------------------
# BaseTask.has_pending_input tests
# ---------------------------------------------------------------------------

class TestBaseTaskPendingInput:

    def test_default_false(self):
        """BaseTask.has_pending_input() returns False by default."""
        from core.base_task import BaseTask
        from core import FlowFile

        class DummyTask(BaseTask):
            TYPE = "dummy"
            def execute(self, ff):
                return [ff]

        task = DummyTask({})
        assert task.has_pending_input() is False
