"""OAuthLogout Task — Invalidate session and clear cookie.

Flow pattern:
    httpReceiver (POST /auth/logout) → oauthLogout → handleHTTPResponse
"""

import json
import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class OAuthLogoutTask(BaseTask):
    """Invalidate session and redirect to login."""

    TYPE = "oauthLogout"
    VERSION = "1.0.0"
    NAME = "OAuth2 Logout"
    DESCRIPTION = "Invalidate session and clear authentication cookie"
    ICON = "key"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "cookie_name": {
                "type": "string", "required": False, "default": "pawflow_token",
                "description": "Name of the session cookie to clear",
            },
            "redirect_to": {
                "type": "string", "required": False, "default": "/chat",
                "description": "URL to redirect after logout",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        cookie_name = self.config.get("cookie_name", "pawflow_token")
        redirect_to = self.config.get("redirect_to", "/chat")

        # Try to invalidate the session in SecurityManager
        token = flowfile.get_attribute("http.cookie." + cookie_name) or ""
        if not token:
            # Parse from cookie header
            cookie_header = flowfile.get_attribute("http.header.cookie") or ""
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(cookie_name + "="):
                    token = part[len(cookie_name) + 1:]
                    break

        if token:
            try:
                from core.security import SecurityManager
                sm = SecurityManager.get_instance()
                sm._sessions.pop(token, None)
                logger.info(f"Session invalidated: {token[:8]}...")
            except Exception:
                pass

        # Clear cookie and redirect
        clear_cookie = (
            f"{cookie_name}=; Path=/; Max-Age=0; "
            f"HttpOnly; SameSite=Lax"
        )

        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", redirect_to)
        flowfile.set_attribute("http.response.header.Set-Cookie", clear_cookie)
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache, no-store")

        return [flowfile]


TaskFactory.register(OAuthLogoutTask)
