"""OAuth2 Provider Service — Manages OAuth2 configuration for a provider.

Stores client credentials, endpoint URLs, and provider presets for Google,
GitHub, and Microsoft. Used by oauthRedirect and oauthCallback tasks.

Config:
    provider: str          — "google" | "github" | "microsoft" | "custom"
    client_id: str         — OAuth2 client ID (required)
    client_secret: str     — OAuth2 client secret (required)
    redirect_uri: str      — Callback URL (required, e.g. http://localhost:9090/auth/callback)
    scope: str             — OAuth2 scopes (default: provider-specific)
    authorize_url: str     — Authorization endpoint (auto-set from provider preset)
    token_url: str         — Token endpoint (auto-set from provider preset)
    userinfo_url: str      — UserInfo endpoint (auto-set from provider preset)
    default_role: str      — Default role for new OAuth users (default: "operator")
"""

import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional

from core.base_service import BaseService

logger = logging.getLogger(__name__)

# Provider presets
_PROVIDER_PRESETS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scope": "read:user user:email",
    },
    "microsoft": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me",
        "scope": "openid email profile",
    },
    "pawflow": {
        "authorize_url": "/auth/login",
        "token_url": "",  # handled internally by AuthGateway
        "userinfo_url": "",
        "scope": "",
    },
    "google_drive": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "https://www.googleapis.com/auth/drive",
    },
    "microsoft_onedrive": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me",
        "scope": "Files.ReadWrite.All offline_access",
    },
}


class OAuthProviderService(BaseService):
    """Manages OAuth2 provider configuration and state tokens."""

    TYPE = "oauthProvider"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "provider": {
                "type": "select", "required": False, "default": "google",
                "options": ["pawflow", "google", "github", "microsoft", "google_drive", "microsoft_onedrive", "custom"],
                "description": "OAuth2 provider (preset or custom)",
            },
            "client_id": {
                "type": "string", "required": True,
                "description": "OAuth2 client ID",
            },
            "client_secret": {
                "type": "string", "required": True, "sensitive": True,
                "description": "OAuth2 client secret",
            },
            "redirect_uri": {
                "type": "string", "required": True,
                "description": "OAuth2 redirect URI (e.g. http://localhost:9090/auth/callback)",
            },
            "scope": {
                "type": "string", "required": False,
                "description": "OAuth2 scopes (default: provider-specific)",
            },
            "authorize_url": {
                "type": "string", "required": False,
                "description": "Authorization endpoint URL (auto-set from preset)",
            },
            "token_url": {
                "type": "string", "required": False,
                "description": "Token endpoint URL (auto-set from preset)",
            },
            "userinfo_url": {
                "type": "string", "required": False,
                "description": "UserInfo endpoint URL (auto-set from preset)",
            },
            "default_role": {
                "type": "select", "required": False, "default": "operator",
                "options": ["admin", "editor", "operator", "viewer"],
                "description": "Default role assigned to new OAuth users",
            },
        }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        provider = self.config.get("provider", "google")
        preset = _PROVIDER_PRESETS.get(provider, {})

        self.provider = provider
        self.client_id = self.config.get("client_id", "")
        self.client_secret = self.config.get("client_secret", "")
        self.redirect_uri = self.config.get("redirect_uri", "")
        self.scope = self.config.get("scope", preset.get("scope", "openid email profile"))
        self.authorize_url = self.config.get("authorize_url", preset.get("authorize_url", ""))
        self.token_url = self.config.get("token_url", preset.get("token_url", ""))
        self.userinfo_url = self.config.get("userinfo_url", preset.get("userinfo_url", ""))
        self.default_role = self.config.get("default_role", "operator")

        # CSRF state tokens: {state: expires_at}
        self._states: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _create_connection(self):
        # Re-read config values (may have been resolved by executor after __init__)
        provider = self.config.get("provider", "google")
        preset = _PROVIDER_PRESETS.get(provider, {})
        self.provider = provider
        self.client_id = self.config.get("client_id", "")
        self.client_secret = self.config.get("client_secret", "")
        self.redirect_uri = self.config.get("redirect_uri", "")
        self.scope = self.config.get("scope", preset.get("scope", "openid email profile"))
        self.authorize_url = self.config.get("authorize_url", preset.get("authorize_url", ""))
        self.token_url = self.config.get("token_url", preset.get("token_url", ""))
        self.userinfo_url = self.config.get("userinfo_url", preset.get("userinfo_url", ""))
        self.default_role = self.config.get("default_role", "operator")
        return True

    def _close_connection(self):
        pass

    def generate_state(self, ttl: int = 600, metadata: dict = None) -> str:
        """Generate a CSRF state token with optional metadata."""
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._states[state] = {"expires": time.time() + ttl, "metadata": metadata or {}}
            # Cleanup expired states
            now = time.time()
            self._states = {s: v for s, v in self._states.items()
                            if (v if isinstance(v, (int, float)) else v.get("expires", 0)) > now}
        return state

    def validate_state(self, state: str):
        """Validate and consume a CSRF state token. Returns metadata dict or False."""
        with self._lock:
            entry = self._states.pop(state, None)
            if entry is None:
                return False
            # Handle legacy format (just a float expiry)
            if isinstance(entry, (int, float)):
                return {} if time.time() < entry else False
            expires = entry.get("expires", 0)
            if time.time() >= expires:
                return False
            return entry.get("metadata", {})

    def get_authorize_url(self, state: str) -> str:
        """Build the full authorization URL with query parameters."""
        import urllib.parse
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
        }
        # Google needs access_type=offline for refresh tokens
        if self.provider == "google":
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        return f"{self.authorize_url}?{urllib.parse.urlencode(params)}"


# Auto-register
from core import ServiceFactory
ServiceFactory.register(OAuthProviderService)
