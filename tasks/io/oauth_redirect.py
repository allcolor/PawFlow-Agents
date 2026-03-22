"""OAuthRedirect Task — Redirect user to OAuth2 provider for authentication.

Generates a CSRF state token, builds the authorization URL, and returns
an HTTP 302 redirect response.

Flow pattern:
    httpReceiver (GET /auth/login) → oauthRedirect → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class OAuthRedirectTask(BaseTask):
    """Redirect to OAuth2 provider for authentication."""

    TYPE = "oauthRedirect"
    VERSION = "1.0.0"
    NAME = "OAuth2 Redirect"
    DESCRIPTION = "Redirect user to OAuth2 provider for login"
    ICON = "key"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "oauth_service_id": {
                "type": "string", "required": False, "default": "oauth",
                "description": "ID of the oauthProvider service",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("oauth_service_id", "oauth")

        # Try to get the service from the flow context
        service = getattr(self, '_services', {}).get(service_id)
        if service is None:
            # Fallback: resolve from flowfile attribute (set by flow executor)
            service = flowfile.get_attribute("_service." + service_id)
        if service is None:
            # Last resort: use config directly to build inline
            service = self._build_inline_service()

        if service is None:
            flowfile.set_content(b'{"error": "OAuth service not configured"}')
            flowfile.set_attribute("http.response.status", "500")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        # PawFlow auth gateway: serve login page instead of redirect
        if getattr(service, 'provider', '') == 'pawflow':
            return self._serve_login_page(flowfile, service)

        # Check for relay_callback in query string
        import urllib.parse as _urlparse
        query_string = flowfile.get_attribute("http.query") or ""
        query_params = _urlparse.parse_qs(query_string)
        relay_callback = query_params.get("relay_callback", [""])[0]

        metadata = {}
        if relay_callback:
            metadata["relay_callback"] = relay_callback

        state = service.generate_state(metadata=metadata)
        authorize_url = service.get_authorize_url(state)

        logger.info(f"OAuth2 redirect to {service.provider} (state={state[:8]}...)")

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", authorize_url)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        return [flowfile]

    def _serve_login_page(self, flowfile, oauth_service):
        """Serve PawFlow login page or handle login sub-routes."""
        # Find the AuthGateway service
        auth_svc = None
        for svc in (self._services or {}).values():
            if hasattr(svc, 'get_enabled_providers'):
                auth_svc = svc
                break
        if not auth_svc:
            flowfile.set_content(b'AuthGateway service not configured')
            flowfile.set_attribute("http.response.status", "500")
            return [flowfile]

        path = flowfile.get_attribute("http.path") or "/auth/login"
        method = flowfile.get_attribute("http.method") or "GET"
        ip = flowfile.get_attribute("http.remote.addr") or ""

        # POST /auth/login/builtin — username/password auth
        if method == "POST" and path.endswith("/builtin"):
            return self._handle_builtin_login(flowfile, auth_svc, ip)

        # GET /auth/login/{provider} — OAuth redirect for specific provider
        parts = path.rstrip("/").split("/")
        if len(parts) >= 4 and parts[-1] not in ("login", ""):
            provider_name = parts[-1]
            return self._handle_oauth_redirect(flowfile, auth_svc, provider_name, ip)

        # GET /auth/login — serve the login page
        from tasks.io.serve_login import ServeLoginTask
        # Extract callback path from redirect_uri or use default
        _ruri = oauth_service.redirect_uri
        if "://" in _ruri and "${" not in _ruri:
            from urllib.parse import urlparse as _urlparse_cb
            _cb_path = _urlparse_cb(_ruri).path or "/auth/callback"
        else:
            _cb_path = "/auth/callback"
        login_task = ServeLoginTask({
            "auth_service_id": "auth",
            "callback_path": _cb_path,
        })
        login_task._services = self._services or {}
        return login_task.execute(flowfile)

    def _handle_builtin_login(self, flowfile, auth_svc, ip):
        """Handle POST /auth/login/builtin — username/password."""
        import urllib.parse
        body = flowfile.get_content().decode("utf-8", errors="replace")
        params = urllib.parse.parse_qs(body)
        username = params.get("username", [""])[0]
        password = params.get("password", [""])[0]

        result = auth_svc.authenticate_builtin(username, password, ip=ip)
        if not result.success:
            # Re-serve login page with error
            error_html = f'<div class="error">{result.error}</div>'
            flowfile.set_content(f'<html><body><script>history.back()</script>{error_html}</body></html>'.encode())
            flowfile.set_attribute("http.response.status", "401")
            flowfile.set_attribute("http.response.header.Content-Type", "text/html")
            return [flowfile]

        # Success — create session and redirect to chat
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        token = sm.create_session(result.username, result.roles[0] if result.roles else "viewer",
                                   provider="builtin")
        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", "/chat")
        flowfile.set_attribute("http.response.header.Set-Cookie",
                               f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
        return [flowfile]

    def _handle_oauth_redirect(self, flowfile, auth_svc, provider_name, ip):
        """Handle GET /auth/login/{provider} — redirect to OAuth provider."""
        allowed, wait = auth_svc.check_rate_limit(ip)
        if not allowed:
            flowfile.set_content(f"Too many attempts. Wait {wait}s.".encode())
            flowfile.set_attribute("http.response.status", "429")
            return [flowfile]

        provider = auth_svc.get_provider(provider_name)
        if not provider:
            flowfile.set_content(f"Provider '{provider_name}' not available".encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        # Build redirect URI from the oauth service config
        redirect_uri = ""
        for svc in (self._services or {}).values():
            if hasattr(svc, 'redirect_uri'):
                redirect_uri = svc.redirect_uri
                break
        if not redirect_uri:
            host = flowfile.get_attribute("http.header.Host") or "localhost:9090"
            scheme = "https" if flowfile.get_attribute("http.header.X-Forwarded-Proto") == "https" else "http"
            redirect_uri = f"{scheme}://{host}/auth/callback"

        state = auth_svc.generate_state(provider_name)
        url = provider.get_authorize_url(state, redirect_uri)
        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", url)
        return [flowfile]

    def _build_inline_service(self):
        """Build OAuthProviderService from task config (when service isn't injected)."""
        from services.oauth_provider_service import OAuthProviderService
        # Check if we have oauth config directly in task config
        if self.config.get("client_id"):
            return OAuthProviderService(self.config)
        return None

    def set_services(self, services: Dict[str, Any]):
        """Called by the flow executor to inject services."""
        self._services = services


TaskFactory.register(OAuthRedirectTask)
