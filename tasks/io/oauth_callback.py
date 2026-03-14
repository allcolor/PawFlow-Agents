"""OAuthCallback Task — Handle OAuth2 callback and create session.

Receives the authorization code from the OAuth2 provider, exchanges it for
an access token, fetches user info, creates/updates a SecurityManager session,
and redirects to the chat UI with a session cookie.

Flow pattern:
    httpReceiver (GET /auth/callback) → oauthCallback → handleHTTPResponse
"""

import json
import logging
import urllib.parse
from typing import Dict, Any, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _http_post(url: str, data: Dict, headers: Optional[Dict] = None,
               timeout: int = 15) -> Dict:
    """HTTP POST using urllib (no external deps)."""
    import urllib.request
    import ssl

    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read().decode("utf-8")
        # GitHub returns form-encoded by default, try JSON first
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return dict(urllib.parse.parse_qsl(body))


def _http_get(url: str, token: str, timeout: int = 15) -> Dict:
    """HTTP GET with Bearer token."""
    import urllib.request
    import ssl

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OAuthCallbackTask(BaseTask):
    """Handle OAuth2 callback: exchange code, create session, redirect."""

    TYPE = "oauthCallback"
    VERSION = "1.0.0"
    NAME = "OAuth2 Callback"
    DESCRIPTION = "Handle OAuth2 provider callback and create user session"
    ICON = "key"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "oauth_service_id": {
                "type": "string", "required": False, "default": "oauth",
                "description": "ID of the oauthProvider service",
            },
            "success_redirect": {
                "type": "string", "required": False, "default": "/chat",
                "description": "URL to redirect after successful login",
            },
            "cookie_name": {
                "type": "string", "required": False, "default": "pyfi2_token",
                "description": "Name of the session cookie",
            },
            "cookie_max_age": {
                "type": "integer", "required": False, "default": 28800,
                "description": "Cookie max-age in seconds (default 8h)",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service = self._get_oauth_service(flowfile)
        if service is None:
            return [self._error_response(flowfile, 500, "OAuth service not configured")]

        # Extract code and state from query parameters
        code = flowfile.get_attribute("http.query.code") or ""
        state = flowfile.get_attribute("http.query.state") or ""

        # Parse from query string if attributes not set individually
        if not code:
            query = flowfile.get_attribute("http.query") or ""
            params = dict(urllib.parse.parse_qsl(query))
            code = params.get("code", "")
            state = params.get("state", state)

        if not code:
            return [self._error_response(flowfile, 400, "Missing authorization code")]

        # Validate CSRF state
        if not service.validate_state(state):
            return [self._error_response(flowfile, 403, "Invalid or expired state token")]

        # Exchange code for access token
        try:
            token_data = _http_post(service.token_url, {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": service.redirect_uri,
                "client_id": service.client_id,
                "client_secret": service.client_secret,
            })
        except Exception as e:
            logger.error(f"OAuth token exchange failed: {e}")
            return [self._error_response(flowfile, 502, f"Token exchange failed: {e}")]

        access_token = token_data.get("access_token", "")
        if not access_token:
            error = token_data.get("error", "unknown")
            logger.error(f"OAuth token exchange returned error: {error}")
            return [self._error_response(flowfile, 502, f"No access token: {error}")]

        # Persist tokens (including refresh_token) for filesystem services
        refresh_token = token_data.get("refresh_token", "")
        expires_in = int(token_data.get("expires_in", 3600))
        if refresh_token:
            try:
                from core.oauth_token_store import OAuthTokenStore
                # user_id will be determined after userinfo fetch;
                # save with provider for now, re-save with proper user below
                self._pending_token_data = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": expires_in,
                    "token_url": service.token_url,
                    "client_id": service.client_id,
                    "client_secret": service.client_secret,
                }
            except ImportError:
                pass

        # Fetch user info
        try:
            if service.provider == "github":
                userinfo = _http_get(service.userinfo_url, access_token)
                # GitHub: need separate call for email if not public
                oauth_id = str(userinfo.get("id", ""))
                email = userinfo.get("email", "")
                display_name = userinfo.get("name", "") or userinfo.get("login", "")
                if not email:
                    try:
                        emails = _http_get(
                            "https://api.github.com/user/emails", access_token)
                        for e in emails:
                            if isinstance(e, dict) and e.get("primary"):
                                email = e.get("email", "")
                                break
                    except Exception:
                        pass
            else:
                # Google, Microsoft, custom OIDC
                userinfo = _http_get(service.userinfo_url, access_token)
                oauth_id = userinfo.get("sub", "") or str(userinfo.get("id", ""))
                email = userinfo.get("email", "")
                display_name = userinfo.get("name", "") or userinfo.get("displayName", "")
        except Exception as e:
            logger.error(f"OAuth userinfo fetch failed: {e}")
            return [self._error_response(flowfile, 502, f"UserInfo fetch failed: {e}")]

        if not oauth_id:
            return [self._error_response(flowfile, 502, "No user ID from provider")]

        # Create session via SecurityManager
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()

        # Override default role from service config
        default_role_str = service.default_role
        default_role = Role(default_role_str) if default_role_str in [r.value for r in Role] else Role.OPERATOR

        # Temporarily override the default OAuth role
        original_code = None
        session = sm.authenticate_oauth(
            provider=service.provider,
            oauth_id=oauth_id,
            email=email,
            display_name=display_name,
            ip_address=flowfile.get_attribute("http.remote.addr") or "",
        )

        if not session:
            return [self._error_response(flowfile, 403, "OAuth authentication denied")]

        logger.info(f"OAuth session created: user={session.username}, role={session.role.value}")

        # Update role for new users (authenticate_oauth defaults to VIEWER)
        user = sm._users.get(session.username)
        if user and user.oauth_id == oauth_id and user.role == Role.VIEWER:
            # Only upgrade if this is likely a new user (still at default VIEWER)
            if default_role != Role.VIEWER:
                user.role = default_role
                session.role = default_role
                sm._save_users()

        logger.info(f"OAuth2 login successful: {session.username} "
                    f"(provider={service.provider}, role={session.role.value})")

        # Save OAuth tokens with resolved username
        pending = getattr(self, "_pending_token_data", None)
        if pending:
            try:
                from core.oauth_token_store import OAuthTokenStore
                OAuthTokenStore.instance().save_tokens(
                    user_id=session.username,
                    provider=service.provider,
                    **pending,
                )
            except Exception as e:
                logger.warning(f"Failed to persist OAuth tokens: {e}")
            self._pending_token_data = None

        # Build redirect response with session cookie
        cookie_name = self.config.get("cookie_name", "pyfi2_token")
        cookie_max_age = int(self.config.get("cookie_max_age", 28800))
        success_redirect = self.config.get("success_redirect", "/chat")

        cookie = (
            f"{cookie_name}={session.session_id}; "
            f"Path=/; Max-Age={cookie_max_age}; "
            f"HttpOnly; SameSite=Lax"
        )

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", success_redirect)
        flowfile.set_attribute("http.response.header.Set-Cookie", cookie)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        # Set auth attributes for downstream tasks
        flowfile.set_attribute("http.auth.valid", "true")
        flowfile.set_attribute("http.auth.principal", session.username)
        flowfile.set_attribute("http.auth.roles", session.role.value)

        return [flowfile]

    def _get_oauth_service(self, flowfile: FlowFile):
        """Resolve the OAuth provider service."""
        service = getattr(self, '_services', {}).get(
            self.config.get("oauth_service_id", "oauth"))
        if service is not None:
            return service
        # Build inline from config
        if self.config.get("client_id"):
            from services.oauth_provider_service import OAuthProviderService
            return OAuthProviderService(self.config)
        return None

    def _error_response(self, flowfile: FlowFile, status: int, message: str) -> FlowFile:
        """Build an error response."""
        logger.warning(f"OAuth callback error ({status}): {message}")
        body = json.dumps({"error": message}).encode("utf-8")
        flowfile.set_content(body)
        flowfile.set_attribute("http.response.status", str(status))
        flowfile.set_attribute("http.response.header.Content-Type", "application/json")
        return flowfile

    def set_services(self, services: Dict[str, Any]):
        """Called by the flow executor to inject services."""
        self._services = services


TaskFactory.register(OAuthCallbackTask)
