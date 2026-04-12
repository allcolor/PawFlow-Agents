"""AuthGateway Service — Multi-provider authentication gateway.

Orchestrates multiple auth providers (builtin, Google, GitHub, X, etc.).
Handles user provisioning, rate limiting, and session management.

Configured as a global service — flows point their oauthProvider to "pawflow"
which delegates to this gateway.

Config:
    providers: dict of provider configs (keyed by provider name)
    auto_provision: rules for creating users from OAuth (expression-based)
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
            max_entries=int(config.get("rate_limit_max_entries", 1000)),
            ttl=int(config.get("rate_limit_ttl", 3600)),
            base_delay=float(config.get("rate_limit_base_delay", 30)),
        )
        # CSRF state tokens: {state: {expires, provider, metadata}}
        self._states: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "providers": {
                "type": "map", "required": True,
                "description": "Provider configs: {name: {enabled: true, ...provider-specific}}",
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
        # Rate limit check
        if ip:
            allowed, wait = self._rate_limiter.check(ip)
            if not allowed:
                return AuthResult(success=False,
                                  error=f"Too many attempts. Wait {wait}s.")

        provider = self._providers.get(provider_name)
        if not provider:
            return AuthResult(success=False, error=f"Provider '{provider_name}' not enabled")

        result = provider.exchange_code(code, redirect_uri)
        if not result.success:
            if ip:
                self._rate_limiter.record_failure(ip)
            return result

        # Provision user
        return self._provision_user(result, ip)

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

    def _provision_user(self, auth_result: AuthResult, ip: str = "") -> AuthResult:
        """Check provisioning rules and create/update user if allowed."""
        from core.security import SecurityManager, Role
        from services.auth_providers.rule_evaluator import evaluate_rule

        sm = SecurityManager.get_instance()
        claims = auth_result.claims

        # Check if user already exists (by oauth_id or email)
        existing = self._find_existing_user(sm, auth_result)
        if existing:
            if not existing.enabled:
                if ip:
                    self._rate_limiter.record_failure(ip)
                return AuthResult(success=False, error="Account disabled")
            from datetime import datetime
            existing.last_login = datetime.now().isoformat()
            sm._save_users()
            if ip:
                self._rate_limiter.record_success(ip)
            auth_result.user_id = existing.username
            auth_result.username = existing.username
            auth_result.roles = [existing.role.value]
            return auth_result

        # New user — evaluate provisioning rules (from security.json, not flow config)
        auto_provision = sm.get_auto_provision()
        rules = auto_provision.get("rules", [])
        default_action = auto_provision.get("default_action", "deny")

        role_str = None
        for rule in rules:
            expr = rule.get("match", "")
            if evaluate_rule(expr, claims):
                role_str = rule.get("role", "viewer")
                logger.info(f"[auth_gateway] Rule matched: {expr} -> role={role_str}")
                break

        if role_str is None:
            # No rule matched — apply default action
            if default_action == "deny":
                logger.warning(f"[auth_gateway] Access denied for {auth_result.email} "
                               f"(no matching rule, default=deny)")
                if ip:
                    self._rate_limiter.record_failure(ip)
                return AuthResult(success=False,
                                  error="Access denied — no matching provisioning rule")
            elif default_action.startswith("create"):
                # "create" or "create_viewer" etc.
                role_str = default_action.replace("create_", "") or "viewer"
                if role_str == "create":
                    role_str = ""  # no role = no permissions

        # Create the user
        role = Role(role_str) if role_str and role_str in [r.value for r in Role] else Role.VIEWER
        username = self._derive_username(auth_result)
        try:
            user = sm.create_user(
                username=username,
                password="",  # OAuth users don't have passwords
                role=role if role_str else Role.VIEWER,
                email=auth_result.email,
                display_name=auth_result.display_name or username,
            )
            # Link identity via IdentityService (generic multi-provider)
            from core.identity_service import IdentityService
            IdentityService.instance().link(username, auth_result.provider,
                                             auth_result.user_id)
            logger.info(f"[auth_gateway] Created user {username} "
                        f"(provider={auth_result.provider}, role={role.value})")
        except ValueError:
            # User already exists (race condition) — fetch it
            user = sm.get_user(username)
            if not user:
                return AuthResult(success=False, error="User creation failed")

        if ip:
            self._rate_limiter.record_success(ip)
        auth_result.user_id = username
        auth_result.username = username
        auth_result.roles = [user.role.value]
        return auth_result

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

        # 2. Search by username (OAuth username or derived)
        if auth_result.username:
            user = sm.get_user(auth_result.username)
            if user:
                return user

        # 3. Search by email
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
