"""validateSessionAuth — validates session-based authentication (cookies + Bearer).

Validates auth from SecurityManager sessions. Supports:
1. Cookie-based: reads pawflow_token from Cookie header
2. Bearer token: reads session_id from Authorization: Bearer <token>

On success, sets http.auth.principal, http.auth.roles, http.auth.valid.
On failure, returns 401 JSON or redirects to login page.

Config:
    cookie_name: str      — cookie name (default "pawflow_token")
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
                "type": "string", "required": False, "default": "pawflow_token",
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
        cookie_name = self.config.get("cookie_name", "pawflow_token")

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
                cookie_name = self.config.get("cookie_name", "pawflow_token")
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
            expired_username = session.username
            sm._sessions.pop(token, None)
            # Try silent refresh before failing
            refreshed_session = self._try_silent_refresh(
                flowfile, sm, username=expired_username)
            if refreshed_session:
                session = refreshed_session
                cookie_name = self.config.get("cookie_name", "pawflow_token")
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

    def _try_silent_refresh(self, flowfile: FlowFile, sm,
                            username: str = "") -> object:
        """Try to silently refresh the OAuth session using stored refresh tokens.

        Uses OAuthTokenStore.get_access_token which auto-refreshes if expired.
        If the OAuth token is still valid (or was refreshed), create a new
        PawFlow session.

        Returns a new Session on success, or None on failure.
        """
        try:
            if not username:
                return None

            # Find which OAuth provider this user is linked to
            from core.identity_service import IdentityService
            links = IdentityService.instance().get_links(username)
            if not links:
                return None

            from core.oauth_token_store import OAuthTokenStore
            token_store = OAuthTokenStore.instance()

            for provider_name in links:
                if provider_name in ("builtin",):
                    continue

                # Ensure token entry has token_url/client_id for refresh
                # (tokens saved before this fix may be missing these fields)
                self._backfill_token_entry(token_store, username, provider_name)

                # get_access_token auto-refreshes if expired
                access_token = token_store.get_access_token(username, provider_name)
                if not access_token:
                    continue
                # OAuth token is valid — create new PawFlow session
                user = sm.get_user(username)
                if user:
                    session = sm._create_session(user,
                        ip_address=flowfile.get_attribute("http.remote.addr") or "")
                    logger.info(f"Silent refresh: renewed session for {username} via {provider_name}")
                    return session

            return None

        except Exception as e:
            logger.debug(f"Silent refresh failed: {e}")
            return None

    @staticmethod
    def _backfill_token_entry(token_store, username: str, provider_name: str):
        """Ensure stored tokens have token_url/client_id needed for refresh.

        Older tokens may have been saved without these fields. Backfill
        from the AuthGateway service config.
        """
        key = token_store._key(username, provider_name)
        entry = token_store._tokens.get(key)
        if entry is None:
            data = token_store._load(username)
            entry = data.get(provider_name)
            if entry:
                token_store._tokens[key] = entry
        if not entry:
            return
        if entry.get("token_url") and entry.get("client_id"):
            return  # already has the info

        try:
            from services.auth_gateway_service import AuthGatewayService
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") != "authGateway":
                    continue
                svc = greg.get_live_instance(sid)
                if not svc:
                    continue
                provider = svc.get_provider(provider_name) if hasattr(svc, 'get_provider') else None
                if not provider:
                    continue
                token_url = getattr(provider, '_token_url', '') or getattr(provider, 'token_url', '')
                client_id = getattr(provider, '_client_id', '') or getattr(provider, 'client_id', '')
                client_secret = getattr(provider, '_client_secret', '') or getattr(provider, 'client_secret', '')
                if token_url and client_id:
                    from core.secrets import get_secrets_manager
                    sm = get_secrets_manager()
                    entry["token_url"] = token_url
                    entry["client_id"] = client_id
                    if client_secret:
                        entry["client_secret"] = sm.encrypt(client_secret)
                    # Persist
                    with token_store._file_lock:
                        data = token_store._load(username)
                        data[provider_name] = entry
                        token_store._save(username, data)
                    logger.info(f"Backfilled token_url/client_id for {username}/{provider_name}")
                    return
        except Exception as e:
            logger.debug(f"Backfill failed for {username}/{provider_name}: {e}")

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
                        "WWW-Authenticate": 'Bearer realm="PawFlow"',
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
