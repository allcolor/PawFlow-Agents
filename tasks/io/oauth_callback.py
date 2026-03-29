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
                "type": "string", "required": False, "default": "pawflow_token",
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

        # Extract code and state early — needed for Claude PKCE check
        code = flowfile.get_attribute("http.query.code") or ""
        state = flowfile.get_attribute("http.query.state") or ""
        if not code:
            query = flowfile.get_attribute("http.query") or ""
            params = dict(urllib.parse.parse_qsl(query))
            code = params.get("code", "")
            state = params.get("state", state)

        # Claude Code OAuth PKCE — intercept before any other handler
        from tasks.ai.actions.service_flow import _claude_pkce_states
        if state and state in _claude_pkce_states and code:
            return self._handle_claude_code_callback(flowfile, code, state)

        # PawFlow auth gateway: delegate to gateway for code exchange + provisioning
        if getattr(service, 'provider', '') == 'pawflow':
            return self._handle_pawflow_callback(flowfile, service)

        if not code:
            return [self._error_response(flowfile, 400, "Missing authorization code")]

        # Validate CSRF state
        state_meta = service.validate_state(state)
        if state_meta is False:
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

        # Find or create user via IdentityService
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        username = ids.resolve(service.provider, oauth_id)
        user = sm.get_user(username) if username else None

        if not user:
            # Auto-create user
            username = email.split("@")[0] if email else f"{service.provider}_{oauth_id[:8]}"
            base = username
            counter = 1
            while sm.get_user(username):
                username = f"{base}_{counter}"
                counter += 1
            default_role_str = service.default_role
            default_role = Role(default_role_str) if default_role_str in [r.value for r in Role] else Role.OPERATOR
            user = sm.create_user(username, "", default_role,
                                  email=email, display_name=display_name or username)
            ids.link(username, service.provider, oauth_id)

        if not user.enabled:
            return [self._error_response(flowfile, 403, "Account disabled")]

        session = sm._create_session(user,
            ip_address=flowfile.get_attribute("http.remote.addr") or "")
        logger.info(f"OAuth session created: user={session.username}, role={session.role.value}")

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
        cookie_name = self.config.get("cookie_name", "pawflow_token")
        cookie_max_age = int(self.config.get("cookie_max_age", 28800))
        success_redirect = self.config.get("success_redirect", "/chat")

        # Check if this auth was initiated by a relay
        relay_callback = ""
        if isinstance(state_meta, dict):
            relay_callback = state_meta.get("relay_callback", "")

        cookie = (
            f"{cookie_name}={session.session_id}; "
            f"Path=/; Max-Age={cookie_max_age}; "
            f"HttpOnly; SameSite=Lax"
        )

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        if relay_callback:
            # Redirect to relay callback with token
            cb_url = relay_callback + ("&" if "?" in relay_callback else "?")
            cb_url += urllib.parse.urlencode({
                "token": session.session_id,
                "username": session.username,
                "role": session.role.value,
            })
            flowfile.set_attribute("http.response.header.Location", cb_url)
        else:
            flowfile.set_attribute("http.response.header.Location", success_redirect)
        flowfile.set_attribute("http.response.header.Set-Cookie", cookie)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        # Set auth attributes for downstream tasks
        flowfile.set_attribute("http.auth.valid", "true")
        flowfile.set_attribute("http.auth.principal", session.username)
        flowfile.set_attribute("http.auth.roles", session.role.value)

        return [flowfile]

    def _handle_pawflow_callback(self, flowfile, oauth_service):
        """Handle callback when using PawFlow auth gateway."""
        # Find the AuthGateway service
        auth_svc = None
        for svc in (self._services or {}).values():
            if hasattr(svc, 'authenticate_oauth'):
                auth_svc = svc
                break
        if not auth_svc:
            return [self._error_response(flowfile, 500, "AuthGateway not configured")]

        # Extract code and state
        query = flowfile.get_attribute("http.query") or ""
        params = dict(urllib.parse.parse_qsl(query))
        code = params.get("code", "")
        state = params.get("state", "")

        if not code:
            return [self._error_response(flowfile, 400, "Missing authorization code")]

        # Validate state via oauth_service (same instance that generated it)
        state_data = oauth_service.validate_state(state)
        if state_data is False or state_data is None:
            # Fallback: try auth_svc (in case state was generated there)
            state_data = auth_svc.validate_state(state)
        if not state_data:
            return [self._error_response(flowfile, 403, "Invalid or expired state token")]

        # Extract provider from state metadata
        provider_name = state_data.get("provider", "") if isinstance(state_data, dict) else ""
        if not provider_name:
            return [self._error_response(flowfile, 400, "No provider in state")]

        # Exchange code via the gateway (handles provisioning + rate limiting)
        ip = flowfile.get_attribute("http.remote.addr") or ""
        redirect_uri = oauth_service.redirect_uri
        result = auth_svc.authenticate_oauth(provider_name, code, redirect_uri, ip=ip)

        if not result.success:
            return [self._error_response(flowfile, 403, result.error)]

        # Create session
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        user = sm.get_user(result.username)
        if not user:
            return [self._error_response(flowfile, 500, "User created but not found")]
        session = sm._create_session(user)
        token = session.session_id

        # Store refresh token in session metadata (per-provider)
        if result.refresh_token:
            try:
                from core.oauth_token_store import OAuthTokenStore
                OAuthTokenStore.instance().save_tokens(
                    user_id=result.username,
                    provider=result.provider,
                    access_token=result.access_token,
                    refresh_token=result.refresh_token,
                    expires_in=int(result.token_expires_at - __import__('time').time()) if result.token_expires_at else 3600,
                )
            except Exception as e:
                logger.warning(f"Failed to persist refresh token: {e}")

        # Check if this auth was initiated by a relay (CLI/plugin)
        relay_callback = ""
        if isinstance(state_data, dict):
            relay_callback = state_data.get("relay_callback", "")

        redirect = self.config.get("success_redirect", "/chat")
        cookie = self.config.get("cookie_name", "pawflow_token")
        max_age = int(self.config.get("cookie_max_age", 28800))

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        if relay_callback:
            # Redirect to relay callback (CLI/plugin) with token
            cb_url = relay_callback + ("&" if "?" in relay_callback else "?")
            cb_url += urllib.parse.urlencode({
                "token": token,
                "username": result.username,
                "role": user.role.value if hasattr(user, 'role') else "user",
            })
            flowfile.set_attribute("http.response.header.Location", cb_url)
        else:
            flowfile.set_attribute("http.response.header.Location", redirect)
        flowfile.set_attribute("http.response.header.Set-Cookie",
                               f"{cookie}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}")
        return [flowfile]

    def _handle_claude_code_callback(self, flowfile, code: str, state: str):
        """Handle Anthropic OAuth callback: exchange code for tokens, save to secrets."""
        from tasks.ai.actions.service_flow import _claude_pkce_states

        pkce = _claude_pkce_states.pop(state, None)
        if not pkce:
            return [self._error_response(flowfile, 400, "Invalid or expired state")]

        service_id = pkce["service_id"]
        code_verifier = pkce["code_verifier"]
        redirect_uri = pkce["redirect_uri"]

        _CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
        _TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"

        # Exchange code for tokens
        import urllib.request
        import urllib.error
        payload = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _CLIENT_ID,
            "code_verifier": code_verifier,
        }).encode("utf-8")

        req = urllib.request.Request(
            _TOKEN_URL, data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "claude-code/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            return [self._error_response(flowfile, 502, f"Token exchange failed ({e.code}): {body}")]

        access_token = data.get("accessToken", "")
        refresh_token = data.get("refreshToken", "")
        expires_at = data.get("expiresAt", 0)

        if not access_token:
            return [self._error_response(flowfile, 502, f"No access token: {list(data.keys())}")]

        # Save to secrets
        from pathlib import Path
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        secrets_path = Path("config/global_secrets.json")
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if secrets_path.exists():
            existing = json.loads(secrets_path.read_text(encoding="utf-8"))

        prefix = service_id.replace("-", "_")
        existing[f"{prefix}_access_token"] = sm.encrypt(access_token)
        existing[f"{prefix}_refresh_token"] = sm.encrypt(refresh_token)
        existing[f"{prefix}_expires_at"] = str(int(expires_at))
        secrets_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        # Update service config to reference secrets
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            sdef = greg.get_service(service_id)
            if sdef:
                cfg = getattr(sdef, "config", {}) or {}
                cfg["claude_access_token"] = f"${{secrets.global.{prefix}_access_token}}"
                cfg["claude_refresh_token"] = f"${{secrets.global.{prefix}_refresh_token}}"
                cfg["claude_expires_at"] = f"${{secrets.global.{prefix}_expires_at}}"
                greg.update_service(service_id, config=cfg)
                # Update live instance
                live = greg.get_live_instance(service_id)
                if live and hasattr(live, '_client') and live._client:
                    live._client.claude_access_token = access_token
                    live._client.claude_refresh_token = refresh_token
                    live._client.claude_expires_at = int(expires_at)
        except Exception as e:
            logger.warning("Failed to update service config: %s", e)

        import time as _time
        expires_h = (expires_at - _time.time() * 1000) / 3600000

        # Redirect to chat with success message
        redirect = self.config.get("success_redirect", "/chat")
        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location",
                               f"{redirect}?msg=Claude+login+OK+(expires+in+{expires_h:.0f}h)")
        logger.info("[claude-code] OAuth login OK for service '%s' (expires in %.1fh)",
                    service_id, expires_h)
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
