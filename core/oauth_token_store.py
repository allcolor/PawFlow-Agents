"""OAuth Token Store — Persistent, encrypted storage for OAuth tokens.

Stores access_token, refresh_token, and expiry per (user_id, provider).
Tokens are encrypted at rest using SecretsManager.

Persistence: data/config/users/{user_id}/oauth_tokens.json
"""

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OAuthTokenStore:
    """Singleton. Stores and manages OAuth tokens per user/provider."""

    _instance: Optional["OAuthTokenStore"] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "OAuthTokenStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._tokens: Dict[str, Dict[str, Any]] = {}  # key: "user_id:provider"
        self._file_lock = threading.Lock()

    def _key(self, user_id: str, provider: str) -> str:
        return f"{user_id}:{provider}"

    def _tokens_path(self, user_id: str) -> str:
        from core.paths import USER_CONFIG_DIR
        return str(USER_CONFIG_DIR / user_id / "oauth_tokens.json")

    def _load(self, user_id: str) -> Dict[str, Any]:
        """Load tokens for a user from disk."""
        path = self._tokens_path(user_id)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load OAuth tokens for {user_id}: {e}")
            return {}

    def _save(self, user_id: str, data: Dict[str, Any]):
        """Save tokens for a user to disk."""
        path = self._tokens_path(user_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_tokens(self, user_id: str, provider: str,
                    access_token: str, refresh_token: str = "",
                    expires_in: int = 3600,
                    token_url: str = "", client_id: str = "",
                    client_secret: str = ""):
        """Store tokens for a user/provider. Encrypts at rest."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        key = self._key(user_id, provider)
        entry = {
            "access_token": sm.encrypt(access_token),
            "refresh_token": sm.encrypt(refresh_token) if refresh_token else "",
            "expires_at": time.time() + expires_in,
            "token_url": token_url,
            "client_id": client_id,
            "client_secret": sm.encrypt(client_secret) if client_secret else "",
        }

        with self._file_lock:
            data = self._load(user_id)
            data[provider] = entry
            self._save(user_id, data)

        self._tokens[key] = entry
        logger.info(f"OAuth tokens saved: user={user_id}, provider={provider}")

    def get_access_token(self, user_id: str, provider: str) -> Optional[str]:
        """Get a valid access token. Auto-refreshes if expired."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        key = self._key(user_id, provider)
        entry = self._tokens.get(key)

        # Load from disk if not in memory
        if entry is None:
            with self._file_lock:
                data = self._load(user_id)
            entry = data.get(provider)
            if entry:
                self._tokens[key] = entry

        if entry is None:
            return None

        # Check expiry (with 60s buffer)
        if entry.get("expires_at", 0) < time.time() + 60:
            # Try to refresh
            refreshed = self._refresh(user_id, provider, entry)
            if refreshed:
                entry = self._tokens[key]
            else:
                return None

        access_token = entry.get("access_token", "")
        return sm.decrypt(access_token) if access_token else None

    def _refresh(self, user_id: str, provider: str,
                 entry: Dict[str, Any]) -> bool:
        """Refresh the access token using the refresh token."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        refresh_token = entry.get("refresh_token", "")
        if not refresh_token:
            return False

        refresh_token = sm.decrypt(refresh_token)
        token_url = entry.get("token_url", "")
        client_id = entry.get("client_id", "")
        client_secret = entry.get("client_secret", "")
        if client_secret:
            client_secret = sm.decrypt(client_secret)

        if not all([refresh_token, token_url, client_id]):
            return False

        try:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
            if client_secret:
                data["client_secret"] = client_secret

            encoded = urllib.parse.urlencode(data).encode("utf-8")
            req = urllib.request.Request(token_url, data=encoded, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", "PawFlow/1.0")

            import ssl
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            new_access = result.get("access_token", "")
            new_refresh = result.get("refresh_token", refresh_token)
            expires_in = result.get("expires_in", 3600)

            if not new_access:
                return False

            self.save_tokens(
                user_id, provider, new_access, new_refresh,
                expires_in, token_url, client_id,
                client_secret,  # already decrypted
            )
            logger.info(f"OAuth token refreshed: user={user_id}, provider={provider}")
            return True

        except Exception as e:
            logger.error(f"OAuth refresh failed for {user_id}/{provider}: {e}")
            return False

    def revoke(self, user_id: str, provider: str):
        """Remove stored tokens for a user/provider."""
        key = self._key(user_id, provider)
        self._tokens.pop(key, None)

        with self._file_lock:
            data = self._load(user_id)
            data.pop(provider, None)
            self._save(user_id, data)

        logger.info(f"OAuth tokens revoked: user={user_id}, provider={provider}")

    def get_refresh_token(self, user_id: str, provider: str) -> Optional[str]:
        """Get the raw refresh token (decrypted). Does NOT auto-refresh."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        key = self._key(user_id, provider)
        entry = self._tokens.get(key)
        if entry is None:
            with self._file_lock:
                data = self._load(user_id)
            entry = data.get(provider)
            if entry:
                self._tokens[key] = entry

        if entry is None:
            return None
        rt = entry.get("refresh_token", "")
        return sm.decrypt(rt) if rt else None

    def has_tokens(self, user_id: str, provider: str) -> bool:
        """Check if tokens exist for a user/provider."""
        key = self._key(user_id, provider)
        if key in self._tokens:
            return True
        with self._file_lock:
            data = self._load(user_id)
        return provider in data
