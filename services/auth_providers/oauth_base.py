"""Base OAuth2 provider — shared logic for Google, GitHub, X, etc."""

import http.client
import json
import logging
import ssl
import urllib.parse
from typing import Any, Dict

from services.auth_providers.base import AuthProvider, AuthResult

logger = logging.getLogger(__name__)


class OAuthBaseProvider(AuthProvider):
    """Base class for OAuth2 providers with shared code exchange logic."""

    def __init__(self, config: Dict[str, Any]):
        from core.expression import LazyResolveDict

        if not isinstance(config, LazyResolveDict):
            config = LazyResolveDict(config or {})
        self.config = config
        self._authorize_url = ""
        self._token_url = ""  # nosec B105
        self._userinfo_url = ""

    @property
    def is_oauth(self) -> bool:
        return True

    def _config_str(self, key: str) -> str:
        """Return a normalized string config value for provider requests."""
        value = str(self.config.get(key, "") or "").strip()
        if key == "scope" and not value:
            return str(getattr(self, "DEFAULT_SCOPE", "") or "").strip()
        return value

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build the OAuth2 authorization URL."""
        params = {
            "client_id": self._config_str("client_id"),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self._config_str("scope"),
            "state": state,
        }
        self._customize_authorize_params(params)
        return f"{self._authorize_url}?{urllib.parse.urlencode(params)}"

    def _customize_authorize_params(self, params: dict):
        """Override in subclasses to add provider-specific params."""
        pass

    def exchange_code(self, code: str, redirect_uri: str) -> AuthResult:
        """Exchange authorization code for tokens and user info."""
        # 1. Exchange code for tokens
        token_data = self._request_token(code, redirect_uri)
        if "error" in token_data:
            return AuthResult(success=False,
                              error=token_data.get("error_description",
                                                    token_data.get("error", "Token exchange failed")))

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in = int(token_data.get("expires_in", 3600))

        if not access_token:
            return AuthResult(success=False, error="No access token received")

        # 2. Fetch user info
        userinfo = self._fetch_userinfo(access_token)
        if not userinfo:
            return AuthResult(success=False, error="Failed to fetch user info")

        import time
        return self._build_result(userinfo, access_token, refresh_token,
                                   time.time() + expires_in)

    def refresh_access_token(self, refresh_token: str) -> AuthResult:
        """Refresh an expired access token."""
        if not refresh_token:
            return AuthResult(success=False, error="No refresh token")

        parsed = urllib.parse.urlparse(self._token_url)
        body = urllib.parse.urlencode({
            "client_id": self._config_str("client_id"),
            "client_secret": self._config_str("client_secret"),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode()

        try:
            conn = self._make_conn(parsed)
            conn.request("POST", parsed.path, body=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()

            if "error" in data:
                return AuthResult(success=False, error=data.get("error_description", "Refresh failed"))

            import time
            return AuthResult(
                success=True,
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", refresh_token),
                token_expires_at=time.time() + int(data.get("expires_in", 3600)),
                provider=self.name,
            )
        except Exception as e:
            return AuthResult(success=False, error=f"Refresh failed: {e}")

    def _request_token(self, code: str, redirect_uri: str) -> dict:
        """Exchange authorization code for tokens."""
        parsed = urllib.parse.urlparse(self._token_url)
        body = urllib.parse.urlencode({
            "client_id": self._config_str("client_id"),
            "client_secret": self._config_str("client_secret"),
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()

        try:
            conn = self._make_conn(parsed)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            conn.request("POST", parsed.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()
            return data
        except Exception as e:
            return {"error": str(e)}

    def _fetch_userinfo(self, access_token: str) -> dict:
        """Fetch user info from the provider's userinfo endpoint."""
        parsed = urllib.parse.urlparse(self._userinfo_url)
        try:
            conn = self._make_conn(parsed)
            conn.request("GET", parsed.path,
                         headers={"Authorization": f"Bearer {access_token}",
                                  "Accept": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()
            return data
        except Exception as e:
            logger.error(f"[auth:{self.name}] userinfo failed: {e}")
            return {}

    def _make_conn(self, parsed):
        """Create HTTP(S) connection."""
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                context=ctx, timeout=30)
        return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        """Override in subclasses to extract provider-specific fields."""
        return AuthResult(
            success=True,
            provider=self.name,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims=userinfo,
        )
