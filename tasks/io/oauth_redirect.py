"""OAuthRedirect Task — Redirect user to OAuth2 provider for authentication.

Generates a CSRF state token, builds the authorization URL, and returns
an HTTP 302 redirect response.

Flow pattern:
    httpReceiver (GET /auth/login) → oauthRedirect → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List
from urllib.parse import urlparse

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def is_self_auth_login_url(url: str, host: str = "") -> bool:
    """Return True when an OAuth authorize URL points back to PawFlow login."""
    raw = str(url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    path = parsed.path or raw.split("?", 1)[0]
    if path.rstrip("/") != "/auth/login" and not path.startswith("/auth/login/"):
        return False
    if not parsed.netloc:
        return True
    return bool(host) and parsed.netloc.lower() == host.lower()


def oauth_config_error(flowfile: FlowFile, provider_name: str, authorize_url: str) -> List[FlowFile]:
    """Return an explicit HTTP response for OAuth self-redirect config bugs."""
    import html

    provider = html.escape(str(provider_name or "oauth"))
    target = html.escape(str(authorize_url or ""))
    body = f"""<!DOCTYPE html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><title>PawFlow auth configuration error</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0a0a1a; color:#eee; margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; }}
.box {{ max-width:720px; background:#16213e; border:1px solid #e94560; border-radius:8px; padding:28px; box-shadow:0 8px 32px rgba(0,0,0,.35); }}
h1 {{ margin:0 0 12px; font-size:22px; color:#fff; }}
p {{ line-height:1.5; color:#d8d8e8; }}
code {{ background:#080818; color:#ffb4c2; padding:2px 5px; border-radius:4px; }}
</style></head>
<body><div class=\"box\">
<h1>OAuth configuration error</h1>
<p>Provider <code>{provider}</code> is configured to authorize against PawFlow's own login route:</p>
<p><code>{target}</code></p>
<p>This would create a login redirect loop. Configure this provider's authorization URL to the external identity provider endpoint, or enable the built-in provider for local username/password login.</p>
</div></body></html>"""
    flowfile.set_content(body.encode("utf-8"))
    flowfile.set_attribute("http.response.status", "500")
    flowfile.set_attribute("http.response.header.Content-Type", "text/html; charset=utf-8")
    flowfile.set_attribute("http.response.header.Cache-Control", "no-store")
    return [flowfile]


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

        # Check for relay_callback in query string (before any redirect)
        import urllib.parse as _urlparse
        query_string = flowfile.get_attribute("http.query") or ""
        query_params = _urlparse.parse_qs(query_string)
        relay_callback = query_params.get("relay_callback", [""])[0]

        # PawFlow auth gateway: serve login page instead of redirect
        if getattr(service, 'provider', '') == 'pawflow':
            return self._serve_login_page(flowfile, service, relay_callback=relay_callback)

        metadata = {}
        if relay_callback:
            metadata["relay_callback"] = relay_callback

        state = service.generate_state(metadata=metadata)
        authorize_url = service.get_authorize_url(state)
        host = flowfile.get_attribute("http.header.host") or ""
        if is_self_auth_login_url(authorize_url, host):
            logger.error("OAuth provider %s authorize_url points to PawFlow login: %s",
                         getattr(service, "provider", service_id), authorize_url)
            return oauth_config_error(flowfile, getattr(service, "provider", service_id), authorize_url)

        logger.info(f"OAuth2 redirect to {service.provider} (state={state[:8]}...)")

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", authorize_url)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        return [flowfile]

    def _serve_login_page(self, flowfile, oauth_service, relay_callback=""):
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
            "relay_callback": relay_callback,
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
        user = sm.get_user(result.username)
        session = sm._create_session(user)
        token = session.session_id
        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", "/chat")
        cookie_name = self.config.get("cookie_name", "pawflow_token")
        cookie_max_age = int(self.config.get("cookie_max_age", 86400))
        flowfile.set_attribute("http.response.header.Set-Cookie",
                               f"{cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={cookie_max_age}")
        return [flowfile]

    def _handle_oauth_redirect(self, flowfile, auth_svc, provider_name, ip):
        """Handle GET /auth/login/{provider} — redirect to OAuth provider."""
        provider = auth_svc.get_provider(provider_name)
        if not provider:
            flowfile.set_content(f"Provider '{provider_name}' not available".encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        # Build redirect URI dynamically from request Host header
        host = flowfile.get_attribute("http.header.host") or "localhost:9090"
        scheme = self._detect_scheme(flowfile)
        redirect_uri = f"{scheme}://{host}/auth/callback"

        # Generate state via oauth_service (shared with callback) — not auth_svc
        # because auth_svc may be a different instance after service restart
        for svc in (self._services or {}).values():
            if hasattr(svc, 'generate_state') and hasattr(svc, 'provider'):
                state = svc.generate_state(metadata={"provider": provider_name})
                break
        else:
            state = auth_svc.generate_state(provider_name)
        url = provider.get_authorize_url(state, redirect_uri)
        if is_self_auth_login_url(url, host):
            logger.error("Auth provider %s authorize_url points to PawFlow login: %s",
                         provider_name, url)
            return oauth_config_error(flowfile, provider_name, url)

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", url)
        return [flowfile]

    @staticmethod
    def _detect_scheme(flowfile):
        """Detect HTTP or HTTPS from request attributes."""
        # http_receiver.py lowercases header keys
        if flowfile.get_attribute("http.header.x-forwarded-proto") == "https":
            return "https"
        if flowfile.get_attribute("http.scheme") == "https":
            return "https"
        if flowfile.get_attribute("http.ssl") == "true":
            return "https"
        host = flowfile.get_attribute("http.header.host") or ""
        if host.endswith(":443"):
            return "https"
        return "http"

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
