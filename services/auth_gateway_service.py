"""AuthGateway Service — Multi-provider authentication gateway.

Orchestrates multiple auth providers (builtin, Google, GitHub, X, etc.).
Handles controlled OAuth account onboarding, rate limiting, and session management.

Configured as a global service — flows point their oauthProvider to "pawflow"
which delegates to this gateway.

Config:
    providers: dict of provider configs (keyed by provider name)
    OAuth invitation tokens: admin-created, temporary, one-time tokens for
        creating or linking users after an external provider validates them
    session_ttl: session duration in seconds (default: 86400)
"""

import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from core.base_service import BaseService
from core import ServiceFactory
from services.auth_providers.base import AuthProvider, AuthResult, RateLimiter

logger = logging.getLogger(__name__)

# Registry of available provider classes
_PROVIDER_CLASSES: Dict[str, type] = {}


def register_provider(cls: type):
    """Register an auth provider class."""
    instance = cls.__new__(cls)
    _PROVIDER_CLASSES[instance.name] = cls
    return cls


# Register built-in providers
def _register_all_providers():
    from services.auth_providers.builtin import BuiltinAuthProvider
    from services.auth_providers.google import GoogleAuthProvider
    from services.auth_providers.github import GitHubAuthProvider
    from services.auth_providers.microsoft import MicrosoftAuthProvider
    from services.auth_providers.x_twitter import XTwitterAuthProvider
    from services.auth_providers.facebook import FacebookAuthProvider
    from services.auth_providers.amazon import AmazonAuthProvider
    from services.auth_providers.telegram import TelegramAuthProvider
    _PROVIDER_CLASSES["builtin"] = BuiltinAuthProvider
    _PROVIDER_CLASSES["google"] = GoogleAuthProvider
    _PROVIDER_CLASSES["github"] = GitHubAuthProvider
    _PROVIDER_CLASSES["microsoft"] = MicrosoftAuthProvider
    _PROVIDER_CLASSES["x"] = XTwitterAuthProvider
    _PROVIDER_CLASSES["facebook"] = FacebookAuthProvider
    _PROVIDER_CLASSES["amazon"] = AmazonAuthProvider
    _PROVIDER_CLASSES["telegram"] = TelegramAuthProvider
    from services.auth_providers.generic_oauth import GenericOAuthProvider
    _PROVIDER_CLASSES["generic"] = GenericOAuthProvider


_register_all_providers()


class AuthGatewayService(BaseService):
    """Multi-provider authentication gateway."""

    TYPE = "authGateway"
    VERSION = "1.0.0"
    NAME = "Auth Gateway"
    DESCRIPTION = "Multi-provider authentication (builtin, Google, GitHub, X, etc.)"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._providers: Dict[str, AuthProvider] = {}
        self._rate_limiter = RateLimiter(
            max_entries=int(self.config.get("rate_limit_max_entries", 1000)),
            ttl=int(self.config.get("rate_limit_ttl", 3600)),
            base_delay=float(self.config.get("rate_limit_base_delay", 30)),
        )
        # CSRF state tokens: {state: {expires, provider, metadata}}
        self._states: Dict[str, Dict] = {}
        # Provider-validated OAuth results waiting for an admin-issued token.
        self._pending_oauth: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "providers": {
                "type": "map", "required": True,
                "description": """Provider configs keyed by provider name.

All string values may use `${...}` expressions. They are resolved recursively at runtime, including nested provider secrets.

```json
{
  "builtin": {"enabled": true},
  "google": {
    "enabled": true,
    "client_id": "${auth.google.client_id}",
    "client_secret": "${auth.google.client_secret}",
    "scope": "openid email profile"
  },
  "github": {
    "enabled": true,
    "client_id": "${auth.github.client_id}",
    "client_secret": "${auth.github.client_secret}",
    "scope": "read:user user:email"
  },
  "microsoft": {"enabled": true, "tenant": "common", "client_id": "...", "client_secret": "..."},
  "x": {"enabled": true, "client_id": "...", "client_secret": "..."},
  "facebook": {"enabled": true, "client_id": "...", "client_secret": "..."},
  "amazon": {"enabled": true, "client_id": "...", "client_secret": "..."},
  "telegram": {
    "enabled": true,
    "bot_token": "${auth.telegram.bot_token}",
    "bot_username": "YourBot"
  },
  "my_sso": {
    "enabled": true,
    "authorize_url": "https://idp/auth",
    "token_url": "https://idp/token",
    "userinfo_url": "https://idp/userinfo",
    "client_id": "...",
    "client_secret": "...",
    "scope": "openid email profile"
  }
}
```""",
                "default": {"builtin": {"enabled": True}},
            },
            "session_ttl": {
                "type": "integer", "required": False, "default": 86400,
                "description": "Session TTL in seconds (default: 24h)",
            },
            "rate_limit_base_delay": {
                "type": "integer", "required": False, "default": 30,
                "description": "Base delay in seconds after a failed login attempt",
            },
        }

    def _create_connection(self):
        """Initialize providers from config."""
        providers_config = self.config.get("providers", {})
        for pname, pconfig in providers_config.items():
            if not isinstance(pconfig, dict):
                continue
            if not pconfig.get("enabled", True):
                continue
            cls = _PROVIDER_CLASSES.get(pname)
            if not cls:
                # Unknown provider name → try generic OAuth
                if pconfig.get("authorize_url") and pconfig.get("token_url"):
                    cls = _PROVIDER_CLASSES.get("generic")
                    pconfig.setdefault("name", pname)
                else:
                    logger.warning(f"[auth_gateway] Unknown provider: {pname}")
                    continue
            try:
                if pname == "builtin":
                    self._providers[pname] = cls()
                else:
                    self._providers[pname] = cls(pconfig)
                logger.info(f"[auth_gateway] Provider enabled: {pname}")
            except Exception as e:
                logger.error(f"[auth_gateway] Failed to init provider {pname}: {e}")
        return True

    def _close_connection(self):
        self._providers.clear()

    # ── Public API ──────────────────────────────────────────────────

    def get_enabled_providers(self) -> List[Dict[str, Any]]:
        """List enabled providers (for login page rendering)."""
        result = []
        for name, provider in self._providers.items():
            result.append({
                "name": name,
                "display_name": provider.display_name,
                "icon": provider.icon,
                "is_oauth": provider.is_oauth,
            })
        return result

    def get_provider(self, name: str) -> Optional[AuthProvider]:
        """Get a provider by name."""
        return self._providers.get(name)

    def check_rate_limit(self, ip: str) -> tuple:
        """Check if IP is rate-limited. Returns (allowed, wait_seconds)."""
        return self._rate_limiter.check(ip)

    def generate_state(self, provider: str, ttl: int = 600,
                       metadata: dict = None) -> str:
        """Generate a CSRF state token bound to a provider."""
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._states[state] = {
                "expires": time.time() + ttl,
                "provider": provider,
                "metadata": metadata or {},
            }
            # Cleanup expired
            now = time.time()
            self._states = {s: v for s, v in self._states.items()
                            if v["expires"] > now}
        return state

    def validate_state(self, state: str) -> Optional[Dict]:
        """Validate and consume a CSRF state token.

        Returns: {"provider": "google", "metadata": {...}} or None
        """
        with self._lock:
            entry = self._states.pop(state, None)
        if not entry:
            return None
        if time.time() >= entry["expires"]:
            return None
        return entry

    def authenticate_oauth(self, provider_name: str, code: str,
                            redirect_uri: str, ip: str = "") -> AuthResult:
        """Complete OAuth flow: exchange code, provision user, check rules."""
        provider = self._providers.get(provider_name)
        if not provider:
            return AuthResult(success=False, error=f"Provider '{provider_name}' not enabled")

        result = provider.exchange_code(code, redirect_uri)
        if not result.success:
            result.error = self._format_oauth_exchange_error(provider_name, result.error)
            return result

        # Provision user
        return self._provision_user(result, ip)

    def complete_pending_oauth(self, pending_id: str, invite_token: str,
                               ip: str = "") -> AuthResult:
        """Complete a provider-validated OAuth login with an admin token."""
        pending_id = str(pending_id or "").strip()
        with self._lock:
            entry = self._pending_oauth.get(pending_id)
            if entry and time.time() >= float(entry.get("expires", 0)):
                self._pending_oauth.pop(pending_id, None)
                entry = None
        if not entry:
            return AuthResult(success=False, error="OAuth onboarding session expired")

        result = self._auth_result_from_pending(entry.get("auth_result") or {})
        completed = self._provision_user(result, ip, invite_token=invite_token)
        if completed.success:
            with self._lock:
                self._pending_oauth.pop(pending_id, None)
        return completed

    @staticmethod
    def _format_oauth_exchange_error(provider_name: str, error: str) -> str:
        raw = (error or "OAuth token exchange failed").strip()
        provider_label = provider_name.title() if provider_name else "OAuth provider"
        lowered = raw.lower()
        if provider_name == "github" and "client_id" in lowered and "client_secret" in lowered:
            return (
                "GitHub accepted the browser authorization, but PawFlow's callback token exchange was rejected. "
                "This usually means PawFlow sent stale or mismatched OAuth app data at callback time: "
                "auth gateway service not reloaded, wrong public callback URL, or invalid GitHub OAuth scope/config."
            )
        return f"{provider_label} OAuth callback failed during token exchange: {raw}"

    def authenticate_builtin(self, username: str, password: str,
                              ip: str = "") -> AuthResult:
        """Authenticate with username/password."""
        if ip:
            allowed, wait = self._rate_limiter.check(ip)
            if not allowed:
                return AuthResult(success=False,
                                  error=f"Too many attempts. Wait {wait}s.")

        provider = self._providers.get("builtin")
        if not provider:
            return AuthResult(success=False, error="Builtin auth not enabled")

        result = provider.validate_credentials(username, password)
        if not result.success:
            if ip:
                self._rate_limiter.record_failure(ip)
            return result

        if ip:
            self._rate_limiter.record_success(ip)
        return result

    def refresh_token(self, provider_name: str, refresh_token: str) -> AuthResult:
        """Refresh an access token using the provider that issued it."""
        provider = self._providers.get(provider_name)
        if not provider:
            return AuthResult(success=False, error=f"Provider '{provider_name}' not available")
        return provider.refresh_access_token(refresh_token)

    # ── User provisioning ──────────────────────────────────────────

    def _provision_user(self, auth_result: AuthResult, ip: str = "",
                        invite_token: str = "") -> AuthResult:
        """Resolve, link, or create an OAuth user.

        External OAuth never auto-creates users. A provider identity must match
        an existing PawFlow user, or an admin-created temporary token must be
        supplied to create/link exactly once.
        """
        from core.security import SecurityManager, Role

        sm = SecurityManager.get_instance()

        # Check if user already exists (by oauth_id or email)
        existing = self._find_existing_user(sm, auth_result)
        if existing:
            if not existing.enabled:
                return AuthResult(success=False, error="Account disabled")
            from datetime import datetime
            existing.last_login = datetime.now().isoformat()
            sm._save_users()
            auth_result.user_id = existing.username
            auth_result.username = existing.username
            auth_result.roles = [existing.role.value]
            return auth_result

        if not invite_token:
            pending_id = self._store_pending_oauth(auth_result)
            logger.warning("[auth_gateway] OAuth identity %s:%s requires onboarding token",
                           auth_result.provider, auth_result.user_id)
            denied = AuthResult(
                success=False,
                error="OAuth account is not linked to a PawFlow user. Enter an OAuth onboarding token.",
            )
            setattr(denied, "pending_oauth_id", pending_id)
            return denied

        from core import oauth_invite_tokens
        invite = oauth_invite_tokens.consume_token(
            invite_token,
            used_by=f"{auth_result.provider}:{auth_result.user_id}",
        )
        if not invite:
            return AuthResult(success=False, error="Invalid or expired OAuth onboarding token")

        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        link_username = str(invite.get("link_username") or "").strip()
        if link_username:
            user = sm.get_user(link_username)
            if not user:
                return AuthResult(success=False, error="OAuth onboarding token targets a missing user")
            if not user.enabled:
                return AuthResult(success=False, error="Account disabled")
            if not ids.link(link_username, auth_result.provider, auth_result.user_id):
                return AuthResult(success=False, error="OAuth identity is already linked to another user")
            user.last_login = __import__('datetime').datetime.now().isoformat()
            sm._save_users()
            auth_result.user_id = link_username
            auth_result.username = link_username
            auth_result.roles = [user.role.value]
            return auth_result

        # Create the user only when an admin-issued token explicitly grants a role.
        role_str = str(invite.get("role") or "viewer")
        role = Role(role_str) if role_str in [r.value for r in Role] else Role.VIEWER
        username = self._derive_username(auth_result)
        try:
            user = sm.create_user(
                username=username,
                password="",  # OAuth users don't have passwords  # nosec B106
                role=role,
                email=auth_result.email,
                display_name=auth_result.display_name or username,
            )
            # Link identity via IdentityService (generic multi-provider)
            ids.link(username, auth_result.provider, auth_result.user_id)
            logger.info(f"[auth_gateway] Created user {username} "
                        f"(provider={auth_result.provider}, role={role.value})")
        except ValueError:
            # User already exists (race condition) — fetch it
            user = sm.get_user(username)
            if not user:
                return AuthResult(success=False, error="User creation failed")

        auth_result.user_id = username
        auth_result.username = username
        auth_result.roles = [user.role.value]
        return auth_result

    def _store_pending_oauth(self, auth_result: AuthResult, ttl: int = 600) -> str:
        pending_id = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._pending_oauth[pending_id] = {
                "expires": now + ttl,
                "auth_result": self._auth_result_to_pending(auth_result),
            }
            self._pending_oauth = {
                key: value for key, value in self._pending_oauth.items()
                if float(value.get("expires", 0)) > now
            }
        return pending_id

    @staticmethod
    def _auth_result_to_pending(auth_result: AuthResult) -> Dict[str, Any]:
        return {
            "success": True,
            "user_id": auth_result.user_id,
            "username": auth_result.username,
            "email": auth_result.email,
            "display_name": auth_result.display_name,
            "roles": list(auth_result.roles or []),
            "provider": auth_result.provider,
            "access_token": auth_result.access_token,
            "refresh_token": auth_result.refresh_token,
            "token_expires_at": auth_result.token_expires_at,
            "claims": dict(auth_result.claims or {}),
        }

    @staticmethod
    def _auth_result_from_pending(data: Dict[str, Any]) -> AuthResult:
        return AuthResult(
            success=True,
            user_id=str(data.get("user_id") or ""),
            username=str(data.get("username") or ""),
            email=str(data.get("email") or ""),
            display_name=str(data.get("display_name") or ""),
            roles=list(data.get("roles") or []),
            provider=str(data.get("provider") or ""),
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            token_expires_at=float(data.get("token_expires_at") or 0),
            claims=dict(data.get("claims") or {}),
        )

    def _find_existing_user(self, sm, auth_result: AuthResult):
        """Find existing user by linked identity, username, or email."""
        from core.identity_service import IdentityService
        ids = IdentityService.instance()

        # 1. Search by linked identity (IdentityService)
        username = ids.resolve(auth_result.provider, auth_result.user_id)
        if username:
            user = sm.get_user(username)
            if user:
                return user

        # 2. Installer/admin-configured link by provider claim.
        admin_link = (self.config.get("admin_links", {}) or {}).get(auth_result.provider)
        if isinstance(admin_link, dict):
            claim = str(admin_link.get("claim") or "user_id")
            expected = str(admin_link.get("value") or "").strip().lower()
            actual = ""
            if claim == "user_id":
                actual = auth_result.user_id
            elif claim == "email":
                actual = auth_result.email
            else:
                actual = str((auth_result.claims or {}).get(claim) or "")
            if expected and actual.strip().lower() == expected:
                linked_username = str(admin_link.get("username") or "").strip()
                user = sm.get_user(linked_username) if linked_username else None
                if user:
                    ids.link(linked_username, auth_result.provider, auth_result.user_id)
                    return user

        # 3. Search by username (OAuth username or derived)
        if auth_result.username:
            user = sm.get_user(auth_result.username)
            if user:
                return user

        # 4. Search by email
        if auth_result.email:
            for udict in sm.list_users():
                if udict.get("email", "").lower() == auth_result.email.lower():
                    return sm.get_user(udict["username"])
            # Also match username == email-derived name
            email_user = auth_result.email.split("@")[0]
            user = sm.get_user(email_user)
            if user:
                return user

        return None

    def _derive_username(self, auth_result: AuthResult) -> str:
        """Generate a username from auth result."""
        if auth_result.username:
            return auth_result.username
        if auth_result.email:
            return auth_result.email.split("@")[0]
        return auth_result.user_id.replace(":", "_")


ServiceFactory.register(AuthGatewayService)
