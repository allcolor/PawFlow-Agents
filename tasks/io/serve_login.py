"""ServeLogin Task — Serve a dynamic login page.

Queries the AuthGateway service for enabled providers and renders
appropriate login buttons/forms. Single provider + OAuth = auto-redirect.

Flow pattern:
    httpReceiver (GET /auth/login) → serveLogin → handleHTTPResponse
"""

import json
import logging
import urllib.parse
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ServeLoginTask(BaseTask):
    """Serve a dynamic login page based on enabled auth providers."""

    TYPE = "serveLogin"
    VERSION = "1.0.0"
    NAME = "Serve Login"
    DESCRIPTION = "Dynamic login page with multi-provider support"
    ICON = "login"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "auth_service_id": {
                "type": "string", "required": True, "default": "auth",
                "description": "ID of the AuthGateway service in the flow",
            },
            "callback_path": {
                "type": "string", "required": False, "default": "/auth/callback",
                "description": "OAuth callback path",
            },
            "chat_path": {
                "type": "string", "required": False, "default": "/chat",
                "description": "Redirect path after successful login",
            },
            "title": {
                "type": "string", "required": False, "default": "PawFlow",
                "description": "Login page title",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        auth_svc = self.get_service(self.config.get("auth_service_id", "auth"))
        if not auth_svc or not hasattr(auth_svc, 'get_enabled_providers'):
            flowfile.set_content(b'Auth gateway not configured')
            flowfile.set_attribute("http.response.status", "500")
            return [flowfile]

        providers = auth_svc.get_enabled_providers()
        callback = self.config.get("callback_path", "/auth/callback")
        chat_path = self.config.get("chat_path", "/chat")
        title = self.config.get("title", "PawFlow")
        relay_callback = self.config.get("relay_callback", "")

        # If single OAuth provider and no builtin → auto-redirect
        oauth_providers = [p for p in providers if p["is_oauth"]]
        has_builtin = any(p["name"] == "builtin" for p in providers)
        # Find oauth_service for state generation (shared with callback)
        _oauth_svc = None
        for svc in self._services.values():
            if hasattr(svc, 'generate_state') and hasattr(svc, 'provider'):
                _oauth_svc = svc
                break

        if len(oauth_providers) == 1 and not has_builtin:
            provider = oauth_providers[0]
            _meta = {"provider": provider["name"]}
            if relay_callback:
                _meta["relay_callback"] = relay_callback
            state = _oauth_svc.generate_state(metadata=_meta) if _oauth_svc else auth_svc.generate_state(provider["name"])
            redirect_uri = self._build_redirect_uri(flowfile, callback)
            p = auth_svc.get_provider(provider["name"])
            url = p.get_authorize_url(state, redirect_uri)
            flowfile.set_attribute("http.response.status", "302")
            flowfile.set_attribute("http.response.header.Location", url)
            flowfile.set_content(b'Redirecting...')
            return [flowfile]

        # Render login page
        html = self._render_page(providers, auth_svc, flowfile,
                                  callback, chat_path, title,
                                  relay_callback=relay_callback)
        flowfile.set_content(html.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type",
                               "text/html; charset=utf-8")
        return [flowfile]

    def _build_redirect_uri(self, flowfile, callback_path):
        """Build the full redirect URI from the current request."""
        host = flowfile.get_attribute("http.header.Host") or "localhost:9090"
        scheme = "https" if flowfile.get_attribute("http.header.X-Forwarded-Proto") == "https" else "http"
        return f"{scheme}://{host}{callback_path}"

    def _render_page(self, providers, auth_svc, flowfile,
                      callback, chat_path, title, relay_callback=""):
        """Render the login page HTML with provider buttons."""
        redirect_uri = self._build_redirect_uri(flowfile, callback)
        has_builtin = any(p["name"] == "builtin" for p in providers)
        oauth_providers = [p for p in providers if p["is_oauth"]]

        # Find oauth_service for state generation (shared with callback)
        oauth_svc = None
        for svc in self._services.values():
            if hasattr(svc, 'generate_state') and hasattr(svc, 'provider'):
                oauth_svc = svc
                break

        # Build OAuth buttons
        buttons_html = ""
        for p in oauth_providers:
            _meta = {"provider": p["name"]}
            if relay_callback:
                _meta["relay_callback"] = relay_callback
            if oauth_svc:
                state = oauth_svc.generate_state(metadata=_meta)
            else:
                state = auth_svc.generate_state(p["name"])
            provider_obj = auth_svc.get_provider(p["name"])
            url = provider_obj.get_authorize_url(state, redirect_uri)
            icon = p.get("icon", "")
            buttons_html += (
                f'<a href="{url}" class="provider-btn">'
                f'<span class="provider-icon">{icon}</span> {p["display_name"]}'
                f'</a>\n'
            )

        # Build builtin form
        builtin_html = ""
        if has_builtin:
            builtin_html = '''
<form method="POST" action="/auth/login/builtin" class="login-form">
  <input name="username" type="text" placeholder="Username" required autocomplete="username">
  <input name="password" type="password" placeholder="Password" required autocomplete="current-password">
  <button type="submit">Sign in</button>
</form>
'''

        divider = '<div class="divider"><span>or</span></div>' if has_builtin and oauth_providers else ''

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - Login</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0a0a1a; color: #e0e0e0; display: flex; align-items: center;
       justify-content: center; min-height: 100vh; }}
.login-container {{ background: #16213e; border-radius: 12px; padding: 40px;
                    width: 380px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }}
h1 {{ text-align: center; margin-bottom: 8px; font-size: 24px; color: #fff; }}
.subtitle {{ text-align: center; color: #888; font-size: 13px; margin-bottom: 24px; }}
.login-form input {{ width: 100%; padding: 10px 14px; margin-bottom: 10px;
                     background: #0f0f23; border: 1px solid #333; border-radius: 6px;
                     color: #e0e0e0; font-size: 14px; }}
.login-form input:focus {{ border-color: #6c5ce7; outline: none; }}
.login-form button {{ width: 100%; padding: 10px; background: #6c5ce7; color: white;
                      border: none; border-radius: 6px; font-size: 14px;
                      cursor: pointer; font-weight: 500; }}
.login-form button:hover {{ background: #5a4bd1; }}
.divider {{ display: flex; align-items: center; margin: 20px 0; }}
.divider::before, .divider::after {{ content: ''; flex: 1; border-top: 1px solid #333; }}
.divider span {{ padding: 0 12px; color: #666; font-size: 12px; }}
.provider-btn {{ display: flex; align-items: center; justify-content: center; gap: 10px;
                 width: 100%; padding: 10px; margin-bottom: 8px; background: #1a1a3e;
                 border: 1px solid #333; border-radius: 6px; color: #e0e0e0;
                 text-decoration: none; font-size: 14px; transition: background 0.2s; }}
.provider-btn:hover {{ background: #252550; border-color: #6c5ce7; }}
.provider-icon {{ font-size: 18px; }}
.error {{ background: #3d1f1f; border: 1px solid #e94560; color: #e94560;
          padding: 8px 12px; border-radius: 6px; margin-bottom: 16px; font-size: 13px;
          text-align: center; }}
</style>
</head>
<body>
<div class="login-container">
  <h1>{title}</h1>
  <p class="subtitle">Sign in to continue</p>
  {builtin_html}
  {divider}
  {buttons_html}
</div>
</body>
</html>'''


TaskFactory.register(ServeLoginTask)
