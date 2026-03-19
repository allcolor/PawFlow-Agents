"""HTTP Auth Validator Service — validates Bearer/Basic auth tokens.

Supports:
- Bearer token validation (static tokens, or callback-based OAuth2)
- Basic auth (username:password pairs)
- Custom validator functions

Config:
    auth_type: str         — "bearer" | "basic" | "custom"
    tokens: list[str]      — valid bearer tokens (for static validation)
    users: dict            — username: password mapping (for basic auth)
    realm: str             — HTTP auth realm (default "PawFlow")
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.base_service import BaseService

logger = logging.getLogger(__name__)


class AuthValidationResult:
    """Result of an auth validation attempt."""

    def __init__(self, valid: bool, principal: str = "",
                 roles: Optional[List[str]] = None,
                 error: str = "", status_code: int = 200):
        self.valid = valid
        self.principal = principal  # username or token subject
        self.roles = roles or []
        self.error = error
        self.status_code = status_code  # 401 or 403

    def __bool__(self):
        return self.valid


class HTTPAuthService(BaseService):
    """Validates HTTP authentication headers."""

    TYPE = "httpAuthValidator"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "auth_type": {"type": "select", "required": False, "default": "bearer", "options": ["bearer", "basic", "custom"], "description": "Authentication type"},
            "realm": {"type": "string", "required": False, "default": "PawFlow", "description": "HTTP auth realm"},
            "tokens": {"type": "list", "required": False, "default": [], "description": "Valid bearer tokens (for static validation)"},
            "users": {"type": "map", "required": False, "default": {}, "description": "Username:password mapping (for basic auth)"},
        }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._auth_type = self.config.get("auth_type", "bearer")
        self._realm = self.config.get("realm", "PawFlow")

        # Static bearer tokens
        self._valid_tokens: set = set(self.config.get("tokens", []))

        # Basic auth users
        self._users: Dict[str, str] = dict(self.config.get("users", {}))

        # Custom validator (set programmatically)
        self._custom_validator: Optional[Callable] = None

    def _create_connection(self):
        """No external connection needed."""
        return True

    def _close_connection(self):
        """Nothing to close."""
        pass

    def set_custom_validator(self, validator: Callable):
        """Set a custom validation function.

        validator(auth_type: str, credentials: str) -> AuthValidationResult
        """
        self._custom_validator = validator

    def add_token(self, token: str):
        """Add a valid bearer token."""
        self._valid_tokens.add(token)

    def remove_token(self, token: str):
        """Remove a bearer token."""
        self._valid_tokens.discard(token)

    def add_user(self, username: str, password: str):
        """Add a basic auth user."""
        self._users[username] = password

    def remove_user(self, username: str):
        """Remove a basic auth user."""
        self._users.pop(username, None)

    def validate(self, authorization_header: Optional[str]) -> AuthValidationResult:
        """Validate an Authorization header value.

        Args:
            authorization_header: The full Authorization header value
                                  e.g. "Bearer abc123" or "Basic dXNlcjpwYXNz"

        Returns:
            AuthValidationResult with valid=True/False and appropriate status
        """
        if not authorization_header:
            return AuthValidationResult(
                valid=False, error="No authorization provided",
                status_code=401,
            )

        parts = authorization_header.split(" ", 1)
        if len(parts) != 2:
            return AuthValidationResult(
                valid=False, error="Malformed Authorization header",
                status_code=401,
            )

        scheme, credentials = parts[0].lower(), parts[1]

        # Custom validator takes priority
        if self._custom_validator:
            try:
                return self._custom_validator(scheme, credentials)
            except Exception as e:
                logger.error(f"Custom validator error: {e}")
                return AuthValidationResult(
                    valid=False, error="Auth validation error",
                    status_code=500,
                )

        if scheme == "bearer":
            return self._validate_bearer(credentials)
        elif scheme == "basic":
            return self._validate_basic(credentials)
        else:
            return AuthValidationResult(
                valid=False,
                error=f"Unsupported auth scheme: {scheme}",
                status_code=401,
            )

    def _validate_bearer(self, token: str) -> AuthValidationResult:
        """Validate a bearer token."""
        if not self._valid_tokens:
            return AuthValidationResult(
                valid=False, error="No valid tokens configured",
                status_code=401,
            )
        if token in self._valid_tokens:
            return AuthValidationResult(valid=True, principal=f"token:{token[:8]}...")
        return AuthValidationResult(
            valid=False, error="Invalid bearer token",
            status_code=401,
        )

    def _validate_basic(self, encoded: str) -> AuthValidationResult:
        """Validate basic auth credentials."""
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
        except Exception:
            return AuthValidationResult(
                valid=False, error="Malformed basic auth credentials",
                status_code=401,
            )

        expected_password = self._users.get(username)
        if expected_password is None:
            return AuthValidationResult(
                valid=False, error="Unknown user",
                status_code=401,
            )

        # Constant-time comparison
        if hmac.compare_digest(password, expected_password):
            return AuthValidationResult(valid=True, principal=username)

        return AuthValidationResult(
            valid=False, error="Invalid password",
            status_code=401,
        )

    @property
    def realm(self) -> str:
        return self._realm


# Auto-register
from core import ServiceFactory
ServiceFactory.register(HTTPAuthService)
