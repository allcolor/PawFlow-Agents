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

        # Bypass auth for /files/ with public or gateway_key access
        path = flowfile.get_attribute("http.path") or ""
        if path.startswith("/files/"):
            _file_id = path.split("/")[2] if len(path.split("/")) >= 3 else ""
            if _file_id:
                try:
                    from core.file_store import (
                        FileStore, ACCESS_PUBLIC, ACCESS_GATEWAY_KEY)
                    _level = FileStore.instance().get_access_level(_file_id)
                    if _level == ACCESS_PUBLIC:
                        flowfile.set_attribute("http.auth.principal", "")
                        flowfile.set_attribute("http.auth.roles", "viewer")
                        return [flowfile]
                    if _level == ACCESS_GATEWAY_KEY:
                        _key = flowfile.get_attribute("http.query.k") or ""
                        if _key and FileStore.instance().check_access(
                                _file_id, gateway_key=_key):
                            flowfile.set_attribute("http.auth.principal", "")
                            flowfile.set_attribute("http.auth.roles", "viewer")
                            return [flowfile]
                except Exception:
                    pass

        # Try to extract session token from multiple sources
        token = self._extract_token(flowfile, cookie_name)

        if not token:
            method = flowfile.get_attribute("http.method") or "?"
            logger.warning(f"No auth token for {method} {path}")
            return [self._auth_failed(flowfile, "No authentication token found")]

        # Validate session via SecurityManager
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        session = sm.get_session(token)

        if session is None:
            logger.debug(f"Unknown session token: {token[:16]}... "
                         f"(active_sessions={len(sm._sessions)})")
            return [self._auth_failed(flowfile, "Invalid session token")]

        if session.is_expired:
            # Session exists but expired — try silent refresh using the
            # specific OAuth provider that created this session
            logger.info(f"Session expired for {session.username}, "
                        f"trying silent refresh (provider={session.oauth_provider})")
            refreshed = self._try_silent_refresh(
                flowfile, sm,
                username=session.username,
                oauth_provider=session.oauth_provider)
            if refreshed:
                # Renew the EXISTING session (same session_id) instead of
                # creating a new one — client keeps using the same token
                import time as _time
                session.expires_at = _time.time() + sm._session_ttl
                sm._save_sessions()
                # Tell client about the (same) token in case it needs to update expiry
                self._set_refreshed_token(flowfile, session)
                logger.info(f"Silent refresh OK: {session.username} via {session.oauth_provider}")
            else:
                # Refresh failed — hard-delete this session
                sm.delete_session(token)
                return [self._auth_failed(flowfile, "Session expired")]

        # Sliding session: renew expiry on each successful validation
        import time as _time
        new_expiry = _time.time() + sm._session_ttl
        if new_expiry - session.expires_at > 300:
            session.expires_at = new_expiry
            sm._save_sessions()
        else:
            session.expires_at = new_expiry

        # Renew cookie max-age so the browser doesn't expire it
        cookie_name = self.config.get("cookie_name", "pawflow_token")
        cookie_max_age = int(self.config.get("cookie_max_age", 28800))
        flowfile.set_attribute(
            "http.response.header.Set-Cookie",
            f"{cookie_name}={session.session_id}; Path=/; Max-Age={cookie_max_age}; "
            f"HttpOnly; SameSite=Lax")

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

    def _set_refreshed_token(self, flowfile: FlowFile, session):
        """Set cookie + X-Session-Token header for transparent refresh."""
        cookie_name = self.config.get("cookie_name", "pawflow_token")
        cookie_max_age = int(self.config.get("cookie_max_age", 28800))
        flowfile.set_attribute(
            "http.response.header.Set-Cookie",
            f"{cookie_name}={session.session_id}; Path=/; Max-Age={cookie_max_age}; "
            f"HttpOnly; SameSite=Lax")
        # For Bearer token clients (relay CLI) — pick up the new token
        flowfile.set_attribute("http.response.header.X-Session-Token", session.session_id)

    def _try_silent_refresh(self, flowfile: FlowFile, sm,
                            username: str = "",
                            oauth_provider: str = "") -> bool:
        """Try to silently refresh using the session's OAuth provider.

        Args:
            username: The user who owns the session
            oauth_provider: The specific OAuth provider to use for refresh.
                If empty, tries all providers for this user.

        Returns True on success (session is renewed), False on failure.
        """
        if not username:
            return False

        try:
            from core.oauth_token_store import OAuthTokenStore
            token_store = OAuthTokenStore.instance()

            providers_to_try = []
            if oauth_provider and oauth_provider != "builtin":
                providers_to_try = [oauth_provider]
            else:
                # No specific provider — try all linked providers
                from core.identity_service import IdentityService
                id_svc = IdentityService.instance()
                links = id_svc.get_links(username)
                if not links:
                    logger.info(f"Silent refresh: {username} has no OAuth links")
                    return False
                providers_to_try = [p for p in links if p != "builtin"]

            for provider_name in providers_to_try:
                # Ensure token entry has token_url/client_id for refresh
                self._backfill_token_entry(token_store, username, provider_name)

                # get_access_token auto-refreshes if expired
                access_token = token_store.get_access_token(username, provider_name)
                if access_token:
                    logger.info(f"Silent refresh: {username} via {provider_name} — OK")
                    return True
                logger.debug(f"Silent refresh: {username} via {provider_name} — no token")

            logger.info(f"Silent refresh: all providers failed for {username}")
            return False

        except Exception as e:
            logger.warning(f"Silent refresh failed for {username}: {e}", exc_info=True)
            return False

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
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            for sid, sdef in greg.get_all("global", "").items():
                if getattr(sdef, "service_type", "") != "authGateway":
                    continue
                svc = greg.get_live_instance("global", "", sid)
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
