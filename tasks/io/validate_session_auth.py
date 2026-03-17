"""validateSessionAuth — validates session-based authentication (cookies + Bearer).

Validates auth from SecurityManager sessions. Supports:
1. Cookie-based: reads pyfi2_token from Cookie header
2. Bearer token: reads session_id from Authorization: Bearer <token>

On success, sets http.auth.principal, http.auth.roles, http.auth.valid.
On failure, returns 401 JSON or redirects to login page.

Config:
    cookie_name: str      — cookie name (default "pyfi2_token")
    login_redirect: str   — URL to redirect on failure (default "", returns 401 JSON)
    auto_respond: bool    — auto-send error response (default True)
    listener_service_id: str — HTTPListenerService for auto-response

FlowFile attributes set on success:
    http.auth.valid       — "true"
    http.auth.principal   — username
    http.auth.roles       — role name (admin/editor/operator/viewer)
    http.auth.session_id  — session ID

FlowFile attributes set on failure:
    http.auth.valid       — "false"
    http.auth.error       — error message
    route.relationship    — "failure"
"""

import json
import logging
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ValidateSessionAuthTask(BaseTask):
    """Validate session-based auth using SecurityManager."""

    TYPE = "validateSessionAuth"
    VERSION = "1.0.0"
    NAME = "Validate Session Auth"
    DESCRIPTION = "Validate cookie/bearer session authentication"
    ICON = "shield"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "cookie_name": {
                "type": "string", "required": False, "default": "pyfi2_token",
                "description": "Session cookie name",
            },
            "login_redirect": {
                "type": "string", "required": False, "default": "",
                "description": "Redirect URL on auth failure (empty = return 401 JSON)",
            },
            "auto_respond": {
                "type": "boolean", "required": False, "default": True,
                "description": "Auto-send error response on failure",
            },
            "listener_service_id": {
                "type": "string", "required": False, "default": "",
                "description": "HTTPListenerService ID for auto-response",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        cookie_name = self.config.get("cookie_name", "pyfi2_token")

        # Try to extract session token from multiple sources
        token = self._extract_token(flowfile, cookie_name)

        if not token:
            path = flowfile.get_attribute("http.path") or "?"
            method = flowfile.get_attribute("http.method") or "?"
            logger.warning(f"No auth token for {method} {path}")
            return [self._auth_failed(flowfile, "No authentication token found")]

        # Validate session via SecurityManager
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        session = sm._sessions.get(token)

        if session is None:
            logger.debug(f"Invalid session token: {token[:16]}... "
                         f"(active_sessions={len(sm._sessions)})")
            # Try silent refresh before failing
            refreshed_session = self._try_silent_refresh(flowfile, sm)
            if refreshed_session:
                session = refreshed_session
                # Re-set the cookie with the new session_id
                cookie_name = self.config.get("cookie_name", "pyfi2_token")
                cookie_max_age = 28800
                cookie = (
                    f"{cookie_name}={session.session_id}; "
                    f"Path=/; Max-Age={cookie_max_age}; "
                    f"HttpOnly; SameSite=Lax"
                )
                flowfile.set_attribute("http.response.header.Set-Cookie", cookie)
            else:
                return [self._auth_failed(flowfile, "Invalid session token")]

        if session.is_expired:
            sm._sessions.pop(token, None)
            # Try silent refresh before failing
            refreshed_session = self._try_silent_refresh(flowfile, sm)
            if refreshed_session:
                session = refreshed_session
                cookie_name = self.config.get("cookie_name", "pyfi2_token")
                cookie_max_age = 28800
                cookie = (
                    f"{cookie_name}={session.session_id}; "
                    f"Path=/; Max-Age={cookie_max_age}; "
                    f"HttpOnly; SameSite=Lax"
                )
                flowfile.set_attribute("http.response.header.Set-Cookie", cookie)
            else:
                return [self._auth_failed(flowfile, "Session expired")]

        # Sliding session: renew expiry on each successful validation
        import time as _time
        session.expires_at = _time.time() + sm._session_ttl

        # Auth OK
        flowfile.set_attribute("http.auth.valid", "true")
        flowfile.set_attribute("http.auth.principal", session.username)
        flowfile.set_attribute("http.auth.roles", session.role.value)
        flowfile.set_attribute("http.auth.session_id", session.session_id)
        flowfile.set_attribute("route.relationship", "success")

        logger.debug(f"Session auth OK: {session.username} (role={session.role.value})")
        return [flowfile]

    def _extract_token(self, flowfile: FlowFile, cookie_name: str) -> str:
        """Extract session token from Cookie header or Authorization Bearer."""
        # 1. Try Bearer token
        auth_header = flowfile.get_attribute("http.header.authorization") or ""
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()

        # 2. Try cookie
        cookie_header = flowfile.get_attribute("http.header.cookie") or ""
        if cookie_header:
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(cookie_name + "="):
                    return part[len(cookie_name) + 1:]

        return ""

    def _try_silent_refresh(self, flowfile: FlowFile, sm) -> object:
        """Try to silently refresh the OAuth session using stored refresh tokens.

        Returns a new Session on success, or None on failure.
        """
        try:
            from core.oauth_token_store import OAuthTokenStore
            token_store = OAuthTokenStore.instance()

            # We need to find the user — check all known users with tokens
            # The cookie is invalid/expired, so we can't get user from session.
            # But we can extract user from the old (expired) session if it existed,
            # or from stored tokens.
            cookie_name = self.config.get("cookie_name", "pyfi2_token")
            old_token = self._extract_token(flowfile, cookie_name)

            # Check if there's an expired session we can identify the user from
            username = None
            provider = None
            for sid, sess in list(sm._sessions.items()):
                if sid == old_token:
                    username = sess.username
                    break

            # Also check recently expired sessions that may have been cleaned up
            if not username:
                # Try all users who have OAuth tokens stored
                import os
                users_dir = os.path.join("config", "users")
                if os.path.isdir(users_dir):
                    for user_dir in os.listdir(users_dir):
                        tokens_path = os.path.join(users_dir, user_dir, "oauth_tokens.json")
                        if os.path.exists(tokens_path):
                            # Try refresh for this user
                            for prov in ["google", "github", "microsoft"]:
                                new_access = token_store.get_access_token(user_dir, prov)
                                if new_access:
                                    username = user_dir
                                    provider = prov
                                    break
                        if username:
                            break

            if not username:
                return None

            # Determine provider if not found yet
            if not provider:
                for prov in ["google", "github", "microsoft"]:
                    if token_store.has_tokens(username, prov):
                        new_access = token_store.get_access_token(username, prov)
                        if new_access:
                            provider = prov
                            break

            if not provider:
                return None

            # Re-create session for this user
            user = sm._users.get(username)
            if not user:
                return None

            session = sm.authenticate_oauth(
                provider=provider,
                oauth_id=user.oauth_id or "",
                email=user.email or username,
                display_name=user.display_name or username,
                ip_address=flowfile.get_attribute("http.remote.addr") or "",
            )

            if session:
                logger.info(f"Silent token refresh: renewed session for {username}")
            return session

        except Exception as e:
            logger.debug(f"Silent refresh failed: {e}")
            return None

    def _auth_failed(self, flowfile: FlowFile, error: str) -> FlowFile:
        """Handle authentication failure."""
        logger.debug(f"Session auth failed: {error}")

        flowfile.set_attribute("http.auth.valid", "false")
        flowfile.set_attribute("http.auth.error", error)
        flowfile.set_attribute("route.relationship", "failure")

        auto_respond = self.config.get("auto_respond", True)
        login_redirect = self.config.get("login_redirect", "")

        if auto_respond:
            request_id = flowfile.get_attribute("http.request.id")
            listener_service_id = self.config.get("listener_service_id", "")
            listener_svc = self.get_service(listener_service_id) if listener_service_id else None

            if listener_svc and request_id:
                # API calls (JSON/fetch) get 401; browser navigations get 302 redirect
                content_type = flowfile.get_attribute("http.header.content-type") or ""
                accept = flowfile.get_attribute("http.header.accept") or ""
                is_api_call = ("application/json" in content_type
                               or "application/json" in accept
                               or "text/event-stream" in accept)

                if login_redirect and not is_api_call:
                    # Redirect to login page (browser navigation)
                    headers = {
                        "Location": login_redirect,
                        "Cache-Control": "no-cache, no-store",
                    }
                    listener_svc.submit_response(request_id, 302, headers, b"")
                else:
                    # Return 401 JSON
                    headers = {
                        "Content-Type": "application/json",
                        "WWW-Authenticate": 'Bearer realm="PyFi2"',
                    }
                    body = json.dumps({
                        "error": "Unauthorized",
                        "message": error,
                    }).encode()
                    listener_svc.submit_response(request_id, 401, headers, body)

                flowfile.set_attribute("http.response.sent", "true")
            else:
                # No auto-respond possible, set attributes for downstream
                if login_redirect:
                    flowfile.set_attribute("http.response.status", "302")
                    flowfile.set_attribute("http.response.header.Location", login_redirect)
                else:
                    flowfile.set_content(json.dumps({
                        "error": "Unauthorized", "message": error,
                    }).encode())
                    flowfile.set_attribute("http.response.status", "401")
                    flowfile.set_attribute("http.response.header.Content-Type",
                                          "application/json")

        return flowfile


TaskFactory.register(ValidateSessionAuthTask)
