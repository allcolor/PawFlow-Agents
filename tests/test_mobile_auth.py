"""Native Android authentication handoff contracts."""

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from core import FlowFile


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_mobile_handoff_is_pkce_bound_and_single_use(monkeypatch):
    from core.mobile_auth import MobileAuthStore

    now = [1000.0]
    monkeypatch.setattr("core.mobile_auth.time.time", lambda: now[0])
    verifier = "v" * 64
    store = MobileAuthStore(ttl=300)

    flow_id = store.start("github", _challenge(verifier))
    store.bind_state(flow_id, "oauth-state")
    assert store.is_mobile_state("oauth-state") is True

    code = store.complete(flow_id, "session-token", "alice", "user")
    assert store.is_mobile_state("oauth-state") is False
    with pytest.raises(ValueError, match="PKCE"):
        store.consume(code, "wrong" * 16)

    handoff = store.consume(code, verifier)
    assert handoff["session_id"] == "session-token"
    assert handoff["username"] == "alice"
    with pytest.raises(ValueError, match="Invalid or expired"):
        store.consume(code, verifier)


def test_mobile_handoff_expires(monkeypatch):
    from core.mobile_auth import MobileAuthStore

    now = [1000.0]
    monkeypatch.setattr("core.mobile_auth.time.time", lambda: now[0])
    verifier = "x" * 64
    store = MobileAuthStore(ttl=30)
    flow_id = store.start("builtin", _challenge(verifier))
    code = store.complete(flow_id, "session", "alice", "admin")

    now[0] += 31
    with pytest.raises(ValueError, match="Invalid or expired"):
        store.consume(code, verifier)


class _AuthService:
    def get_enabled_providers(self):
        return [
            {"name": "builtin", "display_name": "PawFlow", "icon": "key", "is_oauth": False},
            {"name": "github", "display_name": "GitHub", "icon": "github", "is_oauth": True},
        ]

    def get_provider(self, name):
        if name != "github":
            return None
        return SimpleNamespace(
            get_authorize_url=lambda state, redirect_uri: (
                f"https://github.example/authorize?state={state}&redirect_uri={redirect_uri}"
            )
        )

    def authenticate_builtin(self, username, password, ip=""):
        if username == "alice" and password == "secret":
            return SimpleNamespace(success=True, username="alice", error="")
        return SimpleNamespace(success=False, username="", error="Invalid credentials")


class _OAuthService:
    provider = "pawflow"
    redirect_uri = "https://paw.example/auth/callback"

    def __init__(self):
        self.metadata = None

    def generate_state(self, metadata=None):
        self.metadata = metadata
        return "state-123"


def _mobile_task(monkeypatch):
    from core.mobile_auth import MobileAuthStore
    from tasks.io.mobile_auth import MobileAuthTask

    store = MobileAuthStore(ttl=300)
    monkeypatch.setattr("tasks.io.mobile_auth.mobile_auth_store", store)
    task = MobileAuthTask({"auth_service_id": "auth", "oauth_service_id": "oauth"})
    oauth = _OAuthService()
    task.set_services({"auth": _AuthService(), "oauth": oauth})
    return task, store, oauth


def _request(path: str, method: str = "GET", body=None) -> FlowFile:
    ff = FlowFile(content=json.dumps(body or {}).encode("utf-8"))
    ff.set_attribute("http.path", path)
    ff.set_attribute("http.method", method)
    ff.set_attribute("http.header.host", "paw.example")
    ff.set_attribute("http.header.x-forwarded-proto", "https")
    ff.set_attribute("http.remote.addr", "203.0.113.9")
    return ff


def test_mobile_provider_manifest_is_native_safe(monkeypatch):
    task, _, _ = _mobile_task(monkeypatch)
    result = task.execute(_request("/auth/mobile/providers"))[0]
    payload = json.loads(result.get_content())

    assert result.get_attribute("http.response.status") == "200"
    assert payload == {"providers": [
        {"name": "builtin", "display_name": "PawFlow", "icon": "key", "type": "password"},
        {"name": "github", "display_name": "GitHub", "icon": "github", "type": "oauth2"},
    ]}


def test_mobile_oauth_start_binds_state_and_pkce(monkeypatch):
    task, store, oauth = _mobile_task(monkeypatch)
    verifier = "p" * 64
    result = task.execute(_request("/auth/mobile/start", "POST", {
        "provider": "github", "code_challenge": _challenge(verifier),
    }))[0]
    payload = json.loads(result.get_content())

    assert result.get_attribute("http.response.status") == "200"
    assert payload["authorization_url"].startswith("https://github.example/")
    assert oauth.metadata["provider"] == "github"
    assert oauth.metadata["mobile_flow_id"] == payload["flow_id"]
    assert store.is_mobile_state("state-123") is True


def test_mobile_builtin_login_creates_pkce_handoff(monkeypatch):
    task, store, _ = _mobile_task(monkeypatch)
    verifier = "b" * 64
    user = SimpleNamespace(role=SimpleNamespace(value="user"))
    session = SimpleNamespace(
        session_id="builtin-session",
        username="alice",
        role=SimpleNamespace(value="user"),
    )
    security = SimpleNamespace(
        get_user=lambda username: user if username == "alice" else None,
        _create_session=lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        "core.security.SecurityManager.get_instance", lambda: security)

    result = task.execute(_request("/auth/mobile/builtin", "POST", {
        "username": "alice",
        "password": "secret",
        "code_challenge": _challenge(verifier),
    }))[0]
    payload = json.loads(result.get_content())

    assert result.get_attribute("http.response.status") == "200"
    assert store.consume(payload["handoff_code"], verifier) == {
        "session_id": "builtin-session",
        "username": "alice",
        "role": "user",
    }


def test_mobile_consume_sets_gateway_and_session_cookies(monkeypatch):
    task, store, _ = _mobile_task(monkeypatch)
    verifier = "c" * 64
    flow_id = store.start("builtin", _challenge(verifier))
    code = store.complete(flow_id, "session-token", "alice", "admin")

    monkeypatch.setattr(
        "services.private_gateway._active_gateway_config",
        lambda: {
            "cookie_name": "_pf_gw",
            "cookie_max_age": 3600,
            "secret_refs": "gateway-secret",
        },
    )
    monkeypatch.setattr(
        "services.private_gateway._effective_client_ip",
        lambda *_args, **_kwargs: "203.0.113.9",
    )
    monkeypatch.setattr(
        "services.private_gateway._make_cookie_value",
        lambda *_args, **_kwargs: "gateway-cookie",
    )

    result = task.execute(_request("/auth/mobile/consume", "POST", {
        "code": code,
        "code_verifier": verifier,
    }))[0]
    cookies = result.get_attribute("http.response.header.Set-Cookie")

    assert result.get_attribute("http.response.status") == "302"
    assert result.get_attribute("http.response.header.Location") == "/chat"
    assert "_pf_gw=gateway-cookie; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax; Secure" in cookies
    assert "pawflow_token=session-token; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax; Secure" in cookies


def test_http_receiver_propagates_gateway_exempt():
    from tasks.io.http_receiver import HTTPReceiverTask

    captured = []

    class Listener:
        def ensure_connected(self):
            pass

        def register_route(self, method, pattern, owner, callback, **kwargs):
            captured.append((method, pattern, kwargs))

    task = HTTPReceiverTask({
        "service_id": "http",
        "routes": [{
            "method": "POST",
            "pattern": "/auth/mobile/consume",
            "public": True,
            "gateway_exempt": True,
        }],
    })
    task.set_services({"http": Listener()})
    task.initialize()

    assert captured[0][2]["gateway_exempt"] is True


def test_private_gateway_only_bypasses_live_mobile_callback(monkeypatch):
    from core.mobile_auth import MobileAuthStore
    from services import private_gateway

    verifier = "m" * 64
    store = MobileAuthStore(ttl=300)
    flow_id = store.start("github", _challenge(verifier))
    store.bind_state(flow_id, "mobile-state")
    monkeypatch.setattr("core.mobile_auth.mobile_auth_store", store)

    class Registry:
        def match(self, method, path):
            return None

    class Handler:
        command = "GET"
        path = "/auth/callback?code=x&state=mobile-state"
        client_address = ("203.0.113.9", 1234)
        server = SimpleNamespace(_route_registry=Registry())

        def __init__(self):
            self.headers = {}

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

        wfile = SimpleNamespace(write=lambda _value: None, flush=lambda: None)

    handler = Handler()
    assert private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": "missing"}) is False

    store.complete(flow_id, "session", "alice", "user")
    assert private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": "missing"}) is True


def test_oauth_callback_returns_mobile_handoff_instead_of_session_cookie(monkeypatch):
    from core.mobile_auth import MobileAuthStore
    from tasks.io.oauth_callback import OAuthCallbackTask

    verifier = "q" * 64
    store = MobileAuthStore(ttl=300)
    flow_id = store.start("github", _challenge(verifier))
    store.bind_state(flow_id, "state")
    monkeypatch.setattr("core.mobile_auth.mobile_auth_store", store)

    user = SimpleNamespace(role=SimpleNamespace(value="user"))
    session = SimpleNamespace(
        session_id="mobile-session", username="alice",
        role=SimpleNamespace(value="user"),
    )
    security = SimpleNamespace(
        get_user=lambda username: user if username == "alice" else None,
        _create_session=lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        "core.security.SecurityManager.get_instance", lambda: security)

    result = SimpleNamespace(
        username="alice", refresh_token="", provider="github",
        access_token="", token_expires_at=0,
        groups=[],
    )
    ff = FlowFile(content=b"")
    task = OAuthCallbackTask({})
    output = task._finish_pawflow_auth(
        ff, result, "github", {"mobile_flow_id": flow_id})[0]

    assert output.get_attribute("http.response.status") == "302"
    location = output.get_attribute("http.response.header.Location")
    assert location.startswith("pawflow://oauth/callback?")
    assert output.get_attribute("http.response.header.Set-Cookie") is None
    code = parse_qs(urlparse(location).query)["code"][0]
    assert store.consume(code, verifier)["session_id"] == "mobile-session"


def test_oauth_callback_returns_mobile_provider_error_to_app(monkeypatch):
    from core.mobile_auth import MobileAuthStore
    from tasks.io.oauth_callback import OAuthCallbackTask

    verifier = "e" * 64
    store = MobileAuthStore(ttl=300)
    flow_id = store.start("github", _challenge(verifier))
    store.bind_state(flow_id, "error-state")
    monkeypatch.setattr("core.mobile_auth.mobile_auth_store", store)

    oauth = SimpleNamespace(
        provider="pawflow",
        validate_state=lambda state: {
            "provider": "github",
            "mobile_flow_id": flow_id,
        } if state == "error-state" else False,
    )

    class Auth:
        def authenticate_oauth(self, *_args, **_kwargs):
            raise AssertionError("Provider errors must not attempt token exchange")

    ff = _request("/auth/callback")
    ff.set_attribute(
        "http.query",
        "error=access_denied&error_description=User+cancelled&state=error-state",
    )
    task = OAuthCallbackTask({})
    task.set_services({"auth": Auth()})
    output = task._handle_pawflow_callback(ff, oauth)[0]

    assert output.get_attribute("http.response.status") == "302"
    location = urlparse(output.get_attribute("http.response.header.Location"))
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "pawflow://oauth/callback"
    )
    assert parse_qs(location.query) == {
        "flow_id": [flow_id],
        "error": ["User cancelled"],
    }
    assert output.get_attribute("http.response.header.Set-Cookie") is None
    assert store.is_mobile_state("error-state") is False


def test_default_flow_exposes_mobile_auth_routes_and_task():
    path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
    flow = json.loads(path.read_text(encoding="utf-8"))
    routes = flow["tasks"]["http_in"]["parameters"]["routes"]
    indexed = {(route["method"], route["pattern"]): route for route in routes}

    assert flow["tasks"]["mobile_auth"]["type"] == "mobileAuth"
    assert ("GET", "/auth/mobile/providers") in indexed
    assert ("POST", "/auth/mobile/start") in indexed
    assert ("POST", "/auth/mobile/builtin") in indexed
    consume = indexed[("POST", "/auth/mobile/consume")]
    assert consume["public"] is True
    assert consume["gateway_exempt"] is True
    relations = {(item["from"], item["to"], item["type"])
                 for item in flow["relations"]}
    assert ("mobile_auth", "send_response", "success") in relations


def test_release_workflow_builds_versioned_android_apk():
    workflow = Path(".github/workflows/release-assets.yml").read_text(
        encoding="utf-8")
    gradle = Path("pawflow-android/app/build.gradle").read_text(
        encoding="utf-8")

    assert "\n  android-apk:\n" in workflow
    assert "android-actions/setup-android@v3" in workflow
    assert "lintDebug testDebugUnitTest assembleDebug" in workflow
    assert '-PpawflowVersion="$VERSION"' in workflow
    assert '-PpawflowVersionCode="$VERSION_CODE"' in workflow
    assert "dist/android/pawflow-android-${VERSION}-debug.apk" in workflow
    assert "name: android-apk" in workflow
    publish = workflow.split("\n  publish:\n", 1)[1]
    assert "android-apk" in publish.split("\n    steps:\n", 1)[0]
    assert 'providers.gradleProperty("pawflowVersion")' in gradle
    assert 'providers.gradleProperty("pawflowVersionCode")' in gradle


def test_code_signing_plan_covers_android_ci_and_recovery():
    plan = Path("docs/CODE_SIGNING_PLAN.md").read_text(encoding="utf-8")

    assert "## 5. Android (.apk / .aab)" in plan
    assert "ANDROID_SIGNING_KEYSTORE_B64" in plan
    assert "ANDROID_SIGNING_CERT_SHA256" in plan
    assert "protected `android-release` GitHub Environment" in plan
    assert "Never fall back to the debug signing config" in plan
    assert "apksigner verify --verbose --print-certs" in plan
    assert "two encrypted offline backups" in plan
    assert "Play App Signing" in plan
    assert "`-debug.apk` suffix" in plan

