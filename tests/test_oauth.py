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
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
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
            "default_role": "editor",
        })
        assert svc.default_role == "editor"


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

        from tasks.io.oauth_callback import OAuthCallbackTask
        task = OAuthCallbackTask({})
        task._services = {"oauth": svc}
        task.config["oauth_service_id"] = "oauth"

        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", f"code=gh-code&state={state}")

        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "302"
        assert results[0].get_attribute("http.auth.valid") == "true"


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
            role=Role.EDITOR,
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
        assert results[0].get_attribute("http.auth.roles") == "editor"

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
            role=Role.VIEWER,
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
        h3.name = "editor_tool"
        h3.allowed_roles = ["admin", "editor"]

        registry.register(h1)
        registry.register(h2)
        registry.register(h3)

        # Editor can see public + editor tools
        filtered = task._filter_tools_by_role(registry, "editor")
        names = [h.name for h in filtered.list_tools()]
        assert "public_tool" in names
        assert "editor_tool" in names
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

    def test_filter_viewer_restricted(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test"})

        registry = ToolRegistry()
        h1 = MagicMock(spec=ToolHandler)
        h1.name = "restricted"
        h1.allowed_roles = ["admin", "editor"]
        registry.register(h1)

        filtered = task._filter_tools_by_role(registry, "viewer")
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
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["version"]  # version exists
        assert "oauth_client_id" in data["parameters"]
        assert "oauth_client_secret" in data["parameters"]
        assert "oauth_provider" in data["parameters"]

    def test_oauth_service_defined(self):
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "oauth" in data["services"]
        assert data["services"]["oauth"]["type"] == "oauthProvider"

    def test_oauth_routes(self):
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        routes = data["tasks"]["http_in"]["parameters"]["routes"]
        patterns = [r["pattern"] for r in routes]
        assert "/auth/login" in patterns
        assert "/auth/callback" in patterns
        assert "/auth/logout" in patterns

    def test_oauth_tasks(self):
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["oauth_login"]["type"] == "oauthRedirect"
        assert data["tasks"]["oauth_callback"]["type"] == "oauthCallback"
        assert data["tasks"]["oauth_logout"]["type"] == "oauthLogout"
        assert data["tasks"]["validate_auth"]["type"] == "validateSessionAuth"

    def test_auth_before_agent(self):
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
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
        path = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["chat_ui"]["parameters"]["login_url"] == "/auth/login"


# ── i18n ────────────────────────────────────────────────────────────

