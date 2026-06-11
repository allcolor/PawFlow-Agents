"""Base classes for authentication providers."""

from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time


@dataclass
class AuthResult:
    """Result of an authentication attempt."""
    success: bool
    user_id: str = ""
    username: str = ""
    email: str = ""
    display_name: str = ""
    roles: List[str] = field(default_factory=list)
    provider: str = ""
    error: str = ""
    # OAuth tokens (for refresh)
    access_token: str = ""
    refresh_token: str = ""
    token_expires_at: float = 0.0
    # Raw claims from JWT/userinfo (for rule matching)
    claims: Dict[str, Any] = field(default_factory=dict)


class AuthProvider(ABC):
    """Interface for an authentication provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider ID (e.g. 'builtin', 'google', 'github')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for the login button."""

    @property
    def icon(self) -> str:
        """Emoji or icon identifier for the login button."""
        return ""

    @property
    def is_oauth(self) -> bool:
        """Whether this provider uses OAuth2 flow (redirect-based)."""
        return True

    @abstractmethod
    def get_config_schema(self) -> Dict[str, Any]:
        """Parameter schema for provider configuration."""

    @abstractmethod
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build the OAuth2 authorization URL. Not used for builtin."""

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str,
                      state: str = "") -> AuthResult:
        """Exchange an OAuth2 authorization code for user info.

        ``state`` is the CSRF state token from the callback; PKCE providers
        use it to look up the code_verifier minted for that authorize URL.
        """

    def validate_credentials(self, username: str, password: str) -> AuthResult:
        """Validate username/password. Only used by builtin provider."""
        return AuthResult(success=False, error="Not supported by this provider")

    def refresh_access_token(self, refresh_token: str) -> AuthResult:
        """Refresh an expired access token. Returns new tokens."""
        return AuthResult(success=False, error="Refresh not supported")


class RateLimiter:
    """In-memory rate limiter with LRU eviction and auto-expiry.

    Tracks failed attempts per key (IP address). Each failure doubles the
    cooldown delay (exponential backoff). Entries expire after TTL.
    Max entries capped to prevent memory growth.
    """

    def __init__(self, max_entries: int = 1000, ttl: int = 3600,
                 base_delay: float = 30.0, max_delay: float = 3600.0):
        self._entries: OrderedDict = OrderedDict()
        self._max = max_entries
        self._ttl = ttl
        self._base_delay = base_delay
        self._max_delay = max_delay

    def _evict_stale(self):
        """Remove expired entries (lazy cleanup)."""
        now = time.time()
        stale = [k for k, v in self._entries.items()
                 if now - v[0] > self._ttl]
        for k in stale:
            self._entries.pop(k, None)

    def check(self, key: str) -> tuple:
        """Check if a key is rate-limited.

        Returns:
            (allowed: bool, wait_seconds: int)
        """
        self._evict_stale()
        entry = self._entries.get(key)
        if not entry:
            return True, 0
        last_attempt, delay, _ = entry
        elapsed = time.time() - last_attempt
        if elapsed < delay:
            return False, int(delay - elapsed)
        return True, 0

    def record_failure(self, key: str):
        """Record a failed attempt — doubles the delay."""
        self._evict_stale()
        entry = self._entries.pop(key, None)
        attempts = (entry[2] + 1) if entry else 1
        delay = min(self._base_delay * (2 ** (attempts - 1)), self._max_delay)
        self._entries[key] = (time.time(), delay, attempts)
        # Evict oldest if over capacity
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def record_success(self, key: str):
        """Clear rate limit on successful auth."""
        self._entries.pop(key, None)
