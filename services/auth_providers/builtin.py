"""Builtin authentication provider — username/password via SecurityManager."""

import logging
from typing import Any, Dict

from services.auth_providers.base import AuthProvider, AuthResult

logger = logging.getLogger(__name__)


class BuiltinAuthProvider(AuthProvider):
    """Username/password authentication using PawFlow's SecurityManager.

    Users are created via the GUI (Settings > Security) or CLI.
    No self-registration — unknown users get 403.
    Passwords are hashed with PBKDF2-HMAC-SHA256 (600K iterations).
    """

    @property
    def name(self) -> str:
        return "builtin"

    @property
    def display_name(self) -> str:
        return "Sign in"

    @property
    def icon(self) -> str:
        return "🔑"

    @property
    def is_oauth(self) -> bool:
        return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {}  # No config needed — uses SecurityManager

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        return ""  # Not OAuth-based

    def exchange_code(self, code: str, redirect_uri: str,
                      state: str = "") -> AuthResult:
        return AuthResult(success=False, error="Builtin provider does not use OAuth")

    def validate_credentials(self, username: str, password: str) -> AuthResult:
        """Validate username/password against SecurityManager."""
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        user = sm.get_user(username)
        if not user:
            logger.warning(f"[auth:builtin] Unknown user: {username}")
            return AuthResult(success=False, error="Invalid credentials")
        if not user.enabled:
            logger.warning(f"[auth:builtin] Disabled user: {username}")
            return AuthResult(success=False, error="Account disabled")
        if not user.check_password(password):
            logger.warning(f"[auth:builtin] Bad password for: {username}")
            return AuthResult(success=False, error="Invalid credentials")

        # Update last_login timestamp
        from datetime import datetime
        user.last_login = datetime.now().isoformat()
        sm._save_users()
        logger.info(f"[auth:builtin] Login success: {username}")
        return AuthResult(
            success=True,
            user_id=username,
            username=username,
            email=user.email,
            display_name=user.display_name or username,
            roles=[user.role.value],
            provider="builtin",
            claims={"provider": "builtin", "username": username,
                    "email": user.email, "role": user.role.value},
        )
