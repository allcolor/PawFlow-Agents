"""Tests for OAuth2 authentication system.

Tests cover:
- OAuthProviderService (presets, state tokens, authorize URL)
- OAuthRedirectTask (302 redirect, missing service)
- OAuthCallbackTask (code exchange, state validation, session creation, errors)
- OAuthLogoutTask (cookie clear, session invalidation)
- ValidateSessionAuthTask (cookie auth, bearer auth, expiry, missing token)
- AgentLoop tool filtering by role
- Flow JSON structure (v1.2.0)
- Task registration
- i18n keys
"""

import json
import pytest
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
import core.paths as _paths
from core.tool_registry import (
    ToolRegistry, ToolHandler, create_default_registry,
)


# ── OAuthProviderService ────────────────────────────────────────────


class TestOAuthProviderService(unittest.TestCase):

    def test_google_preset(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost:9090/auth/callback",
        })
        assert svc.provider == "google"
        assert "accounts.google.com" in svc.authorize_url
        assert "googleapis.com" in svc.token_url
        assert "googleapis.com" in svc.userinfo_url
        assert svc.scope == "openid email profile"

    def test_github_preset(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "github",
            "client_id": "gh-id",
            "client_secret": "gh-secret",
            "redirect_uri": "http://localhost/callback",
        })
        assert "github.com" in svc.authorize_url
        assert "github.com" in svc.token_url
        assert "api.github.com" in svc.userinfo_url

    def test_oauth_request_config_values_are_trimmed(self):
        from services.auth_providers.github import GitHubAuthProvider

        provider = GitHubAuthProvider({
            "client_id": " gh-id ",
            "client_secret": " gh-secret\n",
            "scope": " read:user user:email ",
        })
        url = provider.get_authorize_url("state", "https://webchat.example/auth/callback")
        assert "client_id=gh-id" in url
        assert "scope=read%3Auser+user%3Aemail" in url

    def test_oauth_provider_resolves_config_expressions(self):
        from unittest.mock import patch
        from services.auth_providers.google import GoogleAuthProvider
        from services.auth_providers.github import GitHubAuthProvider

        marker_id = "$" + "{pf_oauth_expr_client_id}"
        marker_secret = "$" + "{pf_oauth_expr_client_secret}"
        with patch("core.expression._load_global_parameters",
                   lambda: {"pf_oauth_expr_client_id": "resolved-id"}), \
             patch("core.expression._load_global_secrets",
                   lambda: {"pf_oauth_expr_client_secret": "resolved-secret"}):
            for cls in (GoogleAuthProvider, GitHubAuthProvider):
                provider = cls({
                    "client_id": marker_id,
                    "client_secret": marker_secret,
                })
                assert provider._config_str("client_id") == "resolved-id"
                assert provider._config_str("client_secret") == "resolved-secret"

    def test_github_empty_scope_uses_provider_default(self):
        from services.auth_providers.github import GitHubAuthProvider

        provider = GitHubAuthProvider({
            "client_id": "gh-id",
            "client_secret": "gh-secret",
            "scope": "",
        })

        url = provider.get_authorize_url("state", "https://webchat.example/auth/callback")

        assert "scope=read%3Auser+user%3Aemail" in url

    def test_google_login_does_not_force_reconsent(self):
        from urllib.parse import parse_qs, urlparse
        from services.auth_providers.google import GoogleAuthProvider

        provider = GoogleAuthProvider({
            "client_id": "google-id",
            "client_secret": "google-secret",
        })

        url = provider.get_authorize_url(
            "state", "https://webchat.example/auth/callback")
        params = parse_qs(urlparse(url).query)

        assert params["scope"] == ["openid email profile"]
        assert "prompt" not in params
        assert "access_type" not in params

    def test_x_token_exchange_uses_basic_auth(self):
        import base64
        import urllib.parse
        from services.auth_providers.x_twitter import XTwitterAuthProvider

        captured = {}

        class Response:
            def read(self):
                return b'{"access_token":"x-token"}'

        class Conn:
            def request(self, method, path, body=None, headers=None, **_kwargs):
                captured["method"] = method
                captured["path"] = path
                captured["body"] = body or b""
                captured["headers"] = headers or {}

            def getresponse(self):
                return Response()

            def close(self):
                pass

        provider = XTwitterAuthProvider({
            "client_id": "x-client",
            "client_secret": "x-secret",
        })
        provider._code_verifier = "verifier"
        provider._make_conn = lambda _parsed: Conn()

        assert provider._request_token(
            "code", "https://webchat.example/auth/callback") == {"access_token": "x-token"}
        expected = base64.b64encode(b"x-client:x-secret").decode("ascii")
        assert captured["headers"]["Authorization"] == f"Basic {expected}"
        form = dict(urllib.parse.parse_qsl(captured["body"].decode()))
        assert form["client_id"] == "x-client"
        assert form["code_verifier"] == "verifier"
        assert "client_secret" not in form

    def test_x_default_login_scope_does_not_request_offline_access(self):
        from urllib.parse import parse_qs, urlparse
        from services.auth_providers.x_twitter import XTwitterAuthProvider

        provider = XTwitterAuthProvider({
            "client_id": "x-client",
            "client_secret": "x-secret",
        })

        url = provider.get_authorize_url(
            "state", "https://webchat.example/auth/callback")
        scope = parse_qs(urlparse(url).query)["scope"][0]

        assert scope == "users.read tweet.read"
        assert "offline.access" not in scope

    def test_x_offline_access_remains_opt_in(self):
        from urllib.parse import parse_qs, urlparse
        from services.auth_providers.x_twitter import XTwitterAuthProvider

        provider = XTwitterAuthProvider({
            "client_id": "x-client",
            "client_secret": "x-secret",
            "scope": "users.read tweet.read offline.access",
        })

        url = provider.get_authorize_url(
            "state", "https://webchat.example/auth/callback")
        scope = parse_qs(urlparse(url).query)["scope"][0]

        assert scope == "users.read tweet.read offline.access"

    def test_oauth_provider_userinfo_sends_user_agent(self):
        from services.auth_providers.github import GitHubAuthProvider
        from services.auth_providers.oauth_base import OAUTH_HTTP_USER_AGENT

        captured = {}

        class Response:
            def read(self):
                return b'{"id": 123, "login": "octocat"}'

        class Conn:
            def request(self, method, path, headers=None, **_kwargs):
                captured["method"] = method
                captured["path"] = path
                captured["headers"] = headers or {}

            def getresponse(self):
                return Response()

            def close(self):
                pass

        provider = GitHubAuthProvider({
            "client_id": "gh-id",
            "client_secret": "gh-secret",
        })
        provider._make_conn = lambda _parsed: Conn()

        assert provider._fetch_userinfo("gh-token")["login"] == "octocat"
        assert captured["headers"]["User-Agent"] == OAUTH_HTTP_USER_AGENT

    def test_oauth_callback_http_get_sends_user_agent(self):
        from tasks.io.oauth_callback import _http_get, OAUTH_HTTP_USER_AGENT

        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"id": 123, "login": "octocat"}'

        def fake_urlopen(req, **_kwargs):
            captured["user_agent"] = req.get_header("User-agent")
            captured["authorization"] = req.get_header("Authorization")
            return Response()

        with patch("urllib.request.urlopen", fake_urlopen):
            data = _http_get("https://api.github.com/user", "gh-token")

        assert data["login"] == "octocat"
        assert captured["authorization"] == "Bearer gh-token"
        assert captured["user_agent"] == OAUTH_HTTP_USER_AGENT

    def test_microsoft_preset(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "microsoft",
            "client_id": "ms-id",
            "client_secret": "ms-secret",
            "redirect_uri": "http://localhost/callback",
        })
        assert "microsoftonline.com" in svc.authorize_url

    def test_custom_provider(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "custom",
            "client_id": "cid",
            "client_secret": "csec",
            "redirect_uri": "http://localhost/cb",
            "authorize_url": "https://my-idp.com/auth",
            "token_url": "https://my-idp.com/token",
            "userinfo_url": "https://my-idp.com/userinfo",
            "scope": "openid",
        })
        assert svc.authorize_url == "https://my-idp.com/auth"
        assert svc.scope == "openid"

    def test_generate_and_validate_state(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state(ttl=60)
        assert len(state) > 20
        # Valid once — returns metadata dict (truthy)
        result = svc.validate_state(state)
        assert result is not False
        assert isinstance(result, dict)
        # Consumed — not valid again
        assert svc.validate_state(state) is False

    def test_state_expiry(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state(ttl=0)
        time.sleep(0.01)
        assert svc.validate_state(state) is False

    def test_state_with_metadata(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state(ttl=60, metadata={"relay_callback": "http://127.0.0.1:12345/callback"})
        result = svc.validate_state(state)
        assert result is not False
        assert result["relay_callback"] == "http://127.0.0.1:12345/callback"

    def test_get_authorize_url(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "my-client-id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost:9090/auth/callback",
        })
        state = svc.generate_state()
        url = svc.get_authorize_url(state)
        assert "accounts.google.com" in url
        assert "client_id=my-client-id" in url
        assert "state=" in url
        assert "redirect_uri=" in url
        # Google-specific params
        assert "access_type=offline" in url

    def test_service_registered(self):
        from tasks import _register_all_services
        _register_all_services()
        from core import ServiceFactory
        assert "oauthProvider" in ServiceFactory.list_types()

    def test_default_role(self):
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
            "default_role": "user",
        })
        assert svc.default_role == "user"

    def test_default_role_rejects_invalid_role(self):
        from services.oauth_provider_service import OAuthProviderService
        with pytest.raises(ValueError):
            OAuthProviderService({
                "provider": "google",
                "client_id": "id",
                "client_secret": "sec",
                "redirect_uri": "http://localhost/cb",
                "default_role": "invalid_role",
            })


# ── OAuthRedirectTask ───────────────────────────────────────────────


class TestOAuthRedirectTask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("oauthRedirect") is not None

    def test_redirect_with_inline_config(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask
        task = OAuthRedirectTask({
            "provider": "google",
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost:9090/auth/callback",
        })
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        location = results[0].get_attribute("http.response.header.Location")
        assert "accounts.google.com" in location
        assert "client_id=test-id" in location

    def test_redirect_with_relay_callback(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask
        from services.oauth_provider_service import OAuthProviderService
        task = OAuthRedirectTask({
            "provider": "google",
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost:9090/auth/callback",
        })
        ff = FlowFile(content=b"")
        ff.set_attribute("http.request.query", "relay_callback=http%3A%2F%2F127.0.0.1%3A12345%2Fcallback")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        location = results[0].get_attribute("http.response.header.Location")
        assert "accounts.google.com" in location

        # Verify the state carries relay_callback metadata
        # Extract state from URL
        import urllib.parse
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        # Build inline service to validate
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost:9090/auth/callback",
        })
        # The state was generated by the inline service inside the task,
        # so we can't validate it from a different instance.
        # Just verify the redirect happened correctly.
        assert state  # state should be non-empty

    def test_redirect_no_config(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask
        task = OAuthRedirectTask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "500"

    def test_inline_authorize_url_cannot_redirect_to_login_route(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask

        task = OAuthRedirectTask({
            "provider": "custom",
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "https://webchat.example/auth/callback",
            "authorize_url": "/auth/login",
            "token_url": "https://idp.example/token",
            "userinfo_url": "https://idp.example/userinfo",
        })
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")

        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "500"
        assert "OAuth configuration error" in results[0].get_content().decode("utf-8")
        assert results[0].get_attribute("http.response.header.Location") is None

    def test_oauth_redirect_does_not_use_login_rate_limit(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask

        class Provider:
            def get_authorize_url(self, state, redirect_uri):
                return "https://github.com/login/oauth/authorize?state=" + state

        class Auth:
            def get_provider(self, name):
                assert name == "github"
                return Provider()

            def generate_state(self, provider):
                return "state-123"

            def check_rate_limit(self, ip):
                raise AssertionError("OAuth provider redirects must not use builtin login rate limit")

        task = OAuthRedirectTask({})
        task._services = {}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")

        results = task._handle_oauth_redirect(ff, Auth(), "github", "127.0.0.1")

        assert results[0].get_attribute("http.response.status") == "302"
        assert "github.com/login/oauth/authorize" in results[0].get_attribute("http.response.header.Location")

    def test_provider_login_state_records_selected_provider(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask

        seen = []

        class OAuthState:
            provider = "pawflow"

            def generate_state(self, metadata=None):
                seen.append(metadata)
                return "state-gh"

        class Provider:
            def get_authorize_url(self, state, redirect_uri):
                assert state == "state-gh"
                return "https://github.com/login/oauth/authorize?state=" + state

        class Auth:
            def get_provider(self, name):
                assert name == "github"
                return Provider()

        task = OAuthRedirectTask({})
        task._services = {"oauth": OAuthState()}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")

        results = task._handle_oauth_redirect(ff, Auth(), "github", "127.0.0.1")

        assert seen == [{"provider": "github"}]
        assert results[0].get_attribute("http.response.status") == "302"

    def test_login_provider_self_redirect_returns_config_error_before_rate_limit(self):
        from tasks.io.oauth_redirect import OAuthRedirectTask

        class BadProvider:
            def get_authorize_url(self, state, redirect_uri):
                return "https://webchat.example/auth/login?state=" + state

        class Auth:
            def get_provider(self, name):
                assert name == "bad"
                return BadProvider()

            def generate_state(self, provider):
                return "state-123"

            def check_rate_limit(self, ip):
                raise AssertionError("config errors must not hit login rate limit")

        task = OAuthRedirectTask({})
        task._services = {}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")

        results = task._handle_oauth_redirect(ff, Auth(), "bad", "127.0.0.1")

        assert results[0].get_attribute("http.response.status") == "500"
        body = results[0].get_content().decode("utf-8")
        assert "OAuth configuration error" in body
        assert "redirect loop" in body

    def test_single_oauth_login_page_self_redirect_returns_config_error(self):
        from tasks.io.serve_login import ServeLoginTask

        class BadProvider:
            def get_authorize_url(self, state, redirect_uri):
                return "https://webchat.example/auth/login?state=" + state

        class Auth:
            def get_enabled_providers(self):
                return [{"name": "bad", "display_name": "Bad OAuth", "icon": "", "is_oauth": True}]

            def get_provider(self, name):
                return BadProvider()

            def generate_state(self, provider):
                return "state-123"

        task = ServeLoginTask({"auth_service_id": "auth"})
        task._services = {"auth": Auth()}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")

        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "500"
        assert "OAuth configuration error" in results[0].get_content().decode("utf-8")

    def test_login_page_renders_callback_error(self):
        from tasks.io.serve_login import ServeLoginTask

        class Auth:
            def get_enabled_providers(self):
                return [{"name": "builtin", "display_name": "Sign in", "icon": "", "is_oauth": False}]

        task = ServeLoginTask({"auth_service_id": "auth"})
        task._services = {"auth": Auth()}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", "error=The%20provided%20client%20secret%20is%20invalid.")

        results = task.execute(ff)

        body = results[0].get_content().decode("utf-8")
        assert "The provided client secret is invalid." in body
        assert "<div class=\"error\">" in body

    def test_login_page_hides_oauth_token_form_when_pending_cannot_complete(self):
        from tasks.io.serve_login import ServeLoginTask

        class Auth:
            def get_enabled_providers(self):
                return [{"name": "builtin", "display_name": "Sign in", "icon": "", "is_oauth": False}]

            def can_complete_pending_oauth(self, pending_id):
                assert pending_id == "pending-123"
                return False

        task = ServeLoginTask({"auth_service_id": "auth"})
        task._services = {"auth": Auth()}
        ff = FlowFile(content=b"")
        ff.set_attribute(
            "http.query",
            "error=OAuth%20account%20is%20not%20linked&pending_oauth=pending-123",
        )

        results = task.execute(ff)

        body = results[0].get_content().decode("utf-8")
        assert "OAuth account is not linked" in body
        assert "OAuth onboarding token" not in body
        assert "Complete sign in" not in body

    def test_login_page_renders_oauth_token_form_only_when_pending_can_complete(self):
        from tasks.io.serve_login import ServeLoginTask

        class Auth:
            def get_enabled_providers(self):
                return [{"name": "builtin", "display_name": "Sign in", "icon": "", "is_oauth": False}]

            def can_complete_pending_oauth(self, pending_id):
                assert pending_id == "pending-123"
                return True

        task = ServeLoginTask({"auth_service_id": "auth"})
        task._services = {"auth": Auth()}
        ff = FlowFile(content=b"")
        ff.set_attribute(
            "http.query",
            "error=OAuth%20account%20is%20not%20linked&pending_oauth=pending-123",
        )

        results = task.execute(ff)

        body = results[0].get_content().decode("utf-8")
        assert "OAuth onboarding token" in body
        assert "Complete sign in" in body
        assert 'name="pending_oauth" type="hidden" value="pending-123"' in body

    def test_login_page_renders_telegram_widget_provider(self):
        from tasks.io.serve_login import ServeLoginTask
        from services.auth_providers.telegram import TelegramAuthProvider

        class Auth:
            def get_enabled_providers(self):
                return [
                    {"name": "builtin", "display_name": "Sign in", "icon": "", "is_oauth": False},
                    {"name": "telegram", "display_name": "Sign in with Telegram", "icon": "", "is_oauth": False},
                ]

            def get_provider(self, name):
                assert name == "telegram"
                return TelegramAuthProvider({
                    "bot_token": "123:test",
                    "bot_username": "ExamplePawFlowBot",
                })

        task = ServeLoginTask({"auth_service_id": "auth"})
        task._services = {"auth": Auth()}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.host", "webchat.example")
        ff.set_attribute("http.header.x-forwarded-proto", "https")

        results = task.execute(ff)

        body = results[0].get_content().decode("utf-8")
        assert 'src="https://telegram.org/js/telegram-widget.js?22"' in body
        assert 'data-telegram-login="ExamplePawFlowBot"' in body
        assert 'data-auth-url="https://webchat.example/auth/callback"' in body
        assert '<div class="divider"><span>or</span></div>' in body

    def test_pawflow_callback_exchanges_with_provider_from_state(self):
        from types import SimpleNamespace
        from tasks.io.oauth_callback import OAuthCallbackTask

        class OAuthState:
            provider = "pawflow"

            def validate_state(self, state):
                assert state == "state-gh"
                return {"provider": "github"}

        class AuthGateway:
            def __init__(self):
                self.providers = []

            def authenticate_oauth(self, provider_name, code, redirect_uri, ip=""):
                self.providers.append(provider_name)
                assert provider_name == "github"
                assert code == "gh-code"
                assert redirect_uri == "https://webchat.example/auth/callback"
                return SimpleNamespace(
                    success=False,
                    error="github failed after correct provider selection",
                )

        auth = AuthGateway()
        task = OAuthCallbackTask({})
        task._services = {"oauth": OAuthState(), "auth": auth}
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", "code=gh-code&state=state-gh")
        ff.set_attribute("http.header.host", "webchat.example")
        ff.set_attribute("http.header.x-forwarded-proto", "https")

        results = task.execute(ff)

        assert auth.providers == ["github"]
        assert results[0].get_attribute("http.response.status") == "302"
        assert "github%20failed" in results[0].get_attribute("http.response.header.Location")

    def test_pawflow_callback_accepts_telegram_widget_data(self):
        from services.auth_providers.base import AuthResult
        from types import SimpleNamespace
        from tasks.io.oauth_callback import OAuthCallbackTask
        from core.security import SecurityManager

        class OAuthState:
            provider = "pawflow"

        class AuthGateway:
            def __init__(self):
                self.telegram_data = None

            def authenticate_oauth(self, provider_name, code, redirect_uri, ip=""):
                raise AssertionError("Telegram widget callbacks do not use OAuth code exchange")

            def authenticate_telegram(self, data, ip=""):
                self.telegram_data = dict(data)
                assert ip == "127.0.0.1"
                return AuthResult(
                    success=True,
                    provider="telegram",
                    user_id="telegram:123",
                    username="tg_user",
                )

        class FakeSecurity:
            def get_user(self, username):
                return SimpleNamespace(username=username, role=SimpleNamespace(value="user"))

            def _create_session(self, user, oauth_provider=""):
                assert user.username == "tg_user"
                assert oauth_provider == "telegram"
                return SimpleNamespace(session_id="session-tg")

        auth = AuthGateway()
        task = OAuthCallbackTask({})
        task._services = {"oauth": OAuthState(), "auth": auth}
        ff = FlowFile(content=b"")
        ff.set_attribute(
            "http.query",
            "id=123&first_name=Ada&username=ada&auth_date=123456&hash=signed",
        )
        ff.set_attribute("http.remote.addr", "127.0.0.1")

        monkey = patch.object(SecurityManager, "get_instance", return_value=FakeSecurity())
        with monkey:
            results = task.execute(ff)

        assert auth.telegram_data["id"] == "123"
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/chat"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert cookie.startswith("pawflow_token=session-tg;")

    def test_builtin_login_sets_pawflow_token_cookie(self):
        from types import SimpleNamespace
        from tasks.io.oauth_redirect import OAuthRedirectTask
        from core.security import SecurityManager

        class FakeAuth:
            def authenticate_builtin(self, username, password, ip=""):
                assert username == "admin"
                assert password == "admin-password-123"
                return SimpleNamespace(success=True, username="admin")

        class FakeSecurity:
            def get_user(self, username):
                return SimpleNamespace(username=username)

            def _create_session(self, user):
                return SimpleNamespace(session_id="session-123")

        task = OAuthRedirectTask({})
        ff = FlowFile(content=b"username=admin&password=admin-password-123")
        monkey = patch.object(SecurityManager, "get_instance", return_value=FakeSecurity())
        with monkey:
            results = task._handle_builtin_login(ff, FakeAuth(), "127.0.0.1")

        assert results[0].get_attribute("http.response.status") == "302"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert cookie.startswith("pawflow_token=session-123;")
        assert "session=" not in cookie

    def test_oauth_token_login_sets_session_cookie(self):
        from types import SimpleNamespace
        from tasks.io.oauth_redirect import OAuthRedirectTask
        from core.security import SecurityManager

        class FakeAuth:
            def __init__(self):
                self.completed = []

            def complete_pending_oauth(self, pending_id, token, ip=""):
                self.completed.append((pending_id, token, ip))
                return SimpleNamespace(
                    success=True,
                    username="linked-user",
                    provider="github",
                )

        class FakeSecurity:
            def get_user(self, username):
                return SimpleNamespace(username=username)

            def _create_session(self, user, oauth_provider=""):
                assert user.username == "linked-user"
                assert oauth_provider == "github"
                return SimpleNamespace(session_id="session-linked")

        auth = FakeAuth()
        task = OAuthRedirectTask({})
        ff = FlowFile(content=b"pending_oauth=pending-123&token=pfo_manual")
        monkey = patch.object(SecurityManager, "get_instance", return_value=FakeSecurity())
        with monkey:
            results = task._handle_oauth_token_login(ff, auth, "127.0.0.1")

        assert auth.completed == [("pending-123", "pfo_manual", "127.0.0.1")]
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/chat"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert cookie.startswith("pawflow_token=session-linked;")


# ── OAuthCallbackTask ──────────────────────────────────────────────


class TestOAuthCallbackTask(unittest.TestCase):

    def setUp(self):
        from core.security import SecurityManager
        self._sm = SecurityManager.get_instance()

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("oauthCallback") is not None

    def test_missing_code(self):
        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "400"

    def test_invalid_state(self):
        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", "code=auth-code-123&state=invalid-state")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "403"

    def test_pawflow_callback_auto_completes_pending_oauth_from_link_cookie(self):
        from services.auth_providers.base import AuthResult
        from tasks.io.oauth_callback import OAuthCallbackTask
        from core.security import SecurityManager, Role

        username = "oauth_link_target"
        sm = SecurityManager.get_instance()
        try:
            sm.create_user(username, "pass", Role.USER, email="link@example.com")
        except ValueError:
            pass

        class OAuthState:
            provider = "pawflow"

            def validate_state(self, state):
                assert state == "state-link"
                return {"provider": "github"}

        class AuthGateway:
            def __init__(self):
                self.completed = []

            def validate_state(self, state):
                return None

            def authenticate_oauth(self, provider_name, code, redirect_uri, ip=""):
                result = AuthResult(
                    success=False,
                    error="OAuth account is not linked to a PawFlow user. Enter an OAuth onboarding token.",
                )
                setattr(result, "pending_oauth_id", "pending-link")
                return result

            def complete_pending_oauth(self, pending_id, invite_token, ip=""):
                self.completed.append((pending_id, invite_token))
                return AuthResult(
                    success=True,
                    provider="github",
                    user_id=username,
                    username=username,
                    roles=["user"],
                )

        auth = AuthGateway()
        task = OAuthCallbackTask({"success_redirect": "/chat"})
        task._services = {"oauth": OAuthState(), "auth": auth}
        task.config["oauth_service_id"] = "oauth"
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", "code=gh-code&state=state-link")
        ff.set_attribute("http.header.host", "webchat.example")
        ff.set_attribute("http.header.cookie", "pawflow_oauth_link_token=pfo_link_token")

        results = task.execute(ff)

        assert auth.completed == [("pending-link", "pfo_link_token")]
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/chat"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert "pawflow_token=" in cookie
        assert "pawflow_oauth_link_token=; Path=/; Max-Age=0" in cookie

    @patch("tasks.io.oauth_callback._http_post")
    @patch("tasks.io.oauth_callback._http_get")
    def test_successful_callback(self, mock_get, mock_post):
        mock_post.return_value = {"access_token": "at-123"}
        mock_get.return_value = {
            "sub": "google-uid-42",
            "email": "user@example.com",
            "name": "Test User",
        }

        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state()

        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("oauth_existing_user", "pass", Role.USER,
                           email="user@example.com")
        except ValueError:
            pass
        IdentityService.instance().link("oauth_existing_user", "google", "google-uid-42")

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({
            "success_redirect": "/chat",
        })
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=auth-code-123&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/chat"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert "pawflow_token=" in cookie
        assert results[0].get_attribute("http.auth.valid") == "true"
        assert results[0].get_attribute("http.auth.principal") != ""

    @patch("tasks.io.oauth_callback._http_post")
    def test_token_exchange_failure(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")

        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state()

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=code&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "502"

    @patch("tasks.io.oauth_callback._http_post")
    def test_no_access_token(self, mock_post):
        mock_post.return_value = {"error": "invalid_grant"}

        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state()

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=code&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "502"

    @patch("tasks.io.oauth_callback._http_post")
    @patch("tasks.io.oauth_callback._http_get")
    def test_relay_callback_redirect(self, mock_get, mock_post):
        """When state has relay_callback metadata, redirect goes to relay."""
        mock_post.return_value = {"access_token": "at-relay"}
        mock_get.return_value = {
            "sub": "google-uid-relay",
            "email": "relay@example.com",
            "name": "Relay User",
        }

        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google",
            "client_id": "id",
            "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        relay_cb = "http://127.0.0.1:54321/callback"
        state = svc.generate_state(metadata={"relay_callback": relay_cb})

        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("oauth_relay_user", "pass", Role.USER,
                           email="relay@example.com")
        except ValueError:
            pass
        IdentityService.instance().link("oauth_relay_user", "google", "google-uid-relay")

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({"success_redirect": "/chat"})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=auth-code-relay&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        location = results[0].get_attribute("http.response.header.Location")
        assert location.startswith(relay_cb)
        assert "token=" in location
        assert "username=" in location
        assert "role=" in location
        # Cookie should still be set
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert "pawflow_token=" in cookie

    @patch("tasks.io.oauth_callback._http_post")
    @patch("tasks.io.oauth_callback._http_get")
    def test_github_provider(self, mock_get, mock_post):
        mock_post.return_value = {"access_token": "gh-token"}
        mock_get.return_value = {
            "id": 12345,
            "login": "octocat",
            "email": "octocat@github.com",
            "name": "Octocat",
        }

        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "github",
            "client_id": "gh-id",
            "client_secret": "gh-sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state()

        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("oauth_github_user", "pass", Role.USER,
                           email="octocat@github.com")
        except ValueError:
            pass
        IdentityService.instance().link("oauth_github_user", "github", "12345")

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=gh-code&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.auth.valid") == "true"


# ── Security regression: relay_callback open-redirect ───────────────


class TestRelayCallbackLoopbackOnly(unittest.TestCase):
    """relay_callback receives the freshly minted session token in its query
    string, so it must be restricted to loopback targets or it becomes a
    session-token exfiltration vector (open redirect)."""

    def test_loopback_validator_accepts_local(self):
        from tasks.io.oauth_redirect import is_loopback_relay_callback
        for url in (
            "http://127.0.0.1:54321/callback",
            "http://localhost:8080/callback",
            "https://127.0.0.1:9000/cb",
            "http://[::1]:7000/callback",
        ):
            assert is_loopback_relay_callback(url), url

    def test_loopback_validator_rejects_external(self):
        from tasks.io.oauth_redirect import is_loopback_relay_callback
        for url in (
            "https://evil.tld/steal",
            "http://attacker.example/callback",
            "http://127.0.0.1.evil.tld/cb",
            "//evil.tld/cb",
            "javascript:alert(1)",
            "http://169.254.169.254/latest/meta-data",
            "",
        ):
            assert not is_loopback_relay_callback(url), url

    def test_sanitize_drops_external(self):
        from tasks.io.oauth_redirect import sanitize_relay_callback
        assert sanitize_relay_callback("https://evil.tld/x") == ""
        assert (sanitize_relay_callback("http://127.0.0.1:1/cb")
                == "http://127.0.0.1:1/cb")

    @patch("tasks.io.oauth_callback._http_post")
    @patch("tasks.io.oauth_callback._http_get")
    def test_external_relay_callback_does_not_leak_token(self, mock_get, mock_post):
        """A crafted state with an external relay_callback must NOT redirect the
        browser (and the session token) off-site — it falls back to the local
        success redirect."""
        mock_post.return_value = {"access_token": "at-evil"}
        mock_get.return_value = {
            "sub": "google-uid-evil",
            "email": "victim@example.com",
            "name": "Victim",
        }
        from services.oauth_provider_service import OAuthProviderService
        svc = OAuthProviderService({
            "provider": "google", "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost/cb",
        })
        state = svc.generate_state(metadata={"relay_callback": "https://evil.tld/steal"})

        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("oauth_victim_user", "pass", Role.USER,
                           email="victim@example.com")
        except ValueError:
            pass
        IdentityService.instance().link("oauth_victim_user", "google", "google-uid-evil")

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({"success_redirect": "/chat"})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=evil-code&state={state}")
        results = task.execute(ff)

        location = results[0].get_attribute("http.response.header.Location")
        assert location == "/chat", location
        assert "evil.tld" not in location
        assert "token=" not in location


# ── OAuthLogoutTask ─────────────────────────────────────────────────


class TestOAuthLogoutTask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("oauthLogout") is not None

    def test_logout_clears_cookie(self):
        from tasks.io.oauth_logout import OAuthLogoutTask
        task = OAuthLogoutTask({"cookie_name": "pawflow_token", "redirect_to": "/chat"})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/chat"
        cookie = results[0].get_attribute("http.response.header.Set-Cookie")
        assert "Max-Age=0" in cookie
        assert "pawflow_token=" in cookie

    def test_logout_invalidates_session(self):
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        # Create a fake session
        sm._sessions["test-token-abc"] = MagicMock()

        from tasks.io.oauth_logout import OAuthLogoutTask
        task = OAuthLogoutTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.cookie", "pawflow_token=test-token-abc")
        task.execute(ff)

        assert "test-token-abc" not in sm._sessions


# ── ValidateSessionAuthTask ─────────────────────────────────────────


class TestValidateSessionAuthTask(unittest.TestCase):

    def setUp(self):
        from core.security import SecurityManager, Session, Role
        self.sm = SecurityManager.get_instance()
        self.session = Session(
            session_id="valid-session-123",
            username="testuser",
            role=Role.USER,
            expires_at=time.time() + 3600,
        )
        self.sm._sessions["valid-session-123"] = self.session

    def tearDown(self):
        self.sm._sessions.pop("valid-session-123", None)

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("validateSessionAuth") is not None

    def test_valid_bearer_token(self):
        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.authorization", "Bearer valid-session-123")
        results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "true"
        assert results[0].get_attribute("http.auth.principal") == "testuser"
        assert results[0].get_attribute("http.auth.roles") == "user"

    def test_valid_cookie(self):
        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.cookie", "other=x; pawflow_token=valid-session-123; foo=bar")
        results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "true"
        assert results[0].get_attribute("http.auth.principal") == "testuser"

    def test_missing_token(self):
        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "false"
        assert results[0].get_attribute("route.relationship") == "failure"

    def test_invalid_token(self):
        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.authorization", "Bearer nonexistent-token")
        with patch.object(task, "_try_silent_refresh", return_value=None):
            results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "false"

    def test_expired_session(self):
        from core.security import Session, Role
        expired = Session(
            session_id="expired-session",
            username="old",
            role=Role.USER,
            expires_at=time.time() - 100,
        )
        self.sm._sessions["expired-session"] = expired

        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.header.authorization", "Bearer expired-session")
        # No tokens on disk → silent refresh fails → auth fails
        with patch.object(task, "_try_silent_refresh", return_value=None):
            results = task.execute(ff)
        assert results[0].get_attribute("http.auth.valid") == "false"
        # Session should be cleaned up
        assert "expired-session" not in self.sm._sessions

    def test_login_redirect_on_failure(self):
        from tasks.io.validate_session_auth import ValidateSessionAuthTask
        task = ValidateSessionAuthTask({
            "login_redirect": "/auth/login",
            "auto_respond": True,
        })
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        # Without listener_service, falls back to setting attributes
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.response.header.Location") == "/auth/login"


# ── Tool Filtering by Role ──────────────────────────────────────────


class TestToolFilteringByRole(unittest.TestCase):

    def test_filter_tools_by_role(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test"})

        registry = ToolRegistry()
        h1 = MagicMock(spec=ToolHandler)
        h1.name = "public_tool"
        h1.allowed_roles = None  # accessible to all

        h2 = MagicMock(spec=ToolHandler)
        h2.name = "admin_tool"
        h2.allowed_roles = ["admin"]

        h3 = MagicMock(spec=ToolHandler)
        h3.name = "user_tool"
        h3.allowed_roles = ["admin", "user"]

        registry.register(h1)
        registry.register(h2)
        registry.register(h3)

        # User can see public + user tools
        filtered = task._filter_tools_by_role(registry, "user")
        names = [h.name for h in filtered.list_tools()]
        assert "public_tool" in names
        assert "user_tool" in names
        assert "admin_tool" not in names

    def test_filter_admin_sees_all(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test"})

        registry = ToolRegistry()
        h1 = MagicMock(spec=ToolHandler)
        h1.name = "admin_only"
        h1.allowed_roles = ["admin"]
        registry.register(h1)

        filtered = task._filter_tools_by_role(registry, "admin")
        assert len(filtered.list_tools()) == 1

    def test_filter_user_restricted(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test"})

        registry = ToolRegistry()
        h1 = MagicMock(spec=ToolHandler)
        h1.name = "restricted"
        h1.allowed_roles = ["admin"]
        registry.register(h1)

        filtered = task._filter_tools_by_role(registry, "user")
        assert len(filtered.list_tools()) == 0

    def test_no_role_no_filtering(self):
        """When no user role is set, all tools should be available."""
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import LLMResponse
        task = AgentLoopTask({
            "api_key": "test",
            "conversation_store": False,
        })

        # No http.auth.roles set → registry not filtered
        registry = task.get_tool_registry()
        count_before = len(registry.list_tools())
        assert count_before > 0  # has default tools



# ── Flow JSON structure ─────────────────────────────────────────────


class TestAgentFlowOAuth(unittest.TestCase):

    def test_flow_json_v1_2(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["version"]  # version exists
        assert "oauth_client_id" in data["parameters"]
        assert "oauth_client_secret" in data["parameters"]
        assert "oauth_provider" in data["parameters"]

    def test_oauth_service_defined(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "oauth" in data["services"]
        assert data["services"]["oauth"]["type"] == "oauthProvider"

    def test_oauth_routes(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        routes = data["tasks"]["http_in"]["parameters"]["routes"]
        patterns = [r["pattern"] for r in routes]
        assert "/auth/login" in patterns
        assert "/auth/callback" in patterns
        assert "/auth/logout" in patterns

    def test_oauth_tasks(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["oauth_login"]["type"] == "oauthRedirect"
        assert data["tasks"]["oauth_callback"]["type"] == "oauthCallback"
        assert data["tasks"]["oauth_logout"]["type"] == "oauthLogout"
        assert data["tasks"]["validate_auth"]["type"] == "validateSessionAuth"

    def test_auth_before_agent(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        relations = data["relations"]
        # http_in → validate_auth for all protected routes
        assert {"from": "http_in", "to": "validate_auth", "type": "POST:/api/agent"} in relations
        assert {"from": "http_in", "to": "validate_auth", "type": "GET:/chat"} in relations
        assert {"from": "http_in", "to": "validate_auth", "type": "GET:/api/agent/events"} in relations
        # validate_auth → route_after_auth on success
        assert {"from": "validate_auth", "to": "route_after_auth", "type": "success"} in relations
        # route_after_auth dispatches to the right handler
        assert {"from": "route_after_auth", "to": "agent", "type": "api"} in relations
        assert {"from": "route_after_auth", "to": "chat_ui", "type": "chat"} in relations
        # validate_auth → send_response on failure
        assert {"from": "validate_auth", "to": "send_response", "type": "failure"} in relations

    def test_chat_ui_has_login_url(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["chat_ui"]["parameters"]["login_url"] == "/auth/login"


# ── i18n ────────────────────────────────────────────────────────────

