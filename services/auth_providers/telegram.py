"""Telegram Login Widget authentication provider.

Telegram uses a different flow than standard OAuth2:
1. The login page embeds the Telegram Login Widget (JavaScript)
2. User clicks "Log in with Telegram" → Telegram popup
3. Telegram redirects back with user data signed by the bot token
4. We verify the signature using HMAC-SHA256

See: https://core.telegram.org/widgets/login
"""

import hashlib
import hmac
import logging
import time
from typing import Any, Dict

from services.auth_providers.base import AuthProvider, AuthResult

logger = logging.getLogger(__name__)


class TelegramAuthProvider(AuthProvider):
    """Telegram Login Widget provider."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def display_name(self) -> str:
        return "Sign in with Telegram"

    @property
    def icon(self) -> str:
        return "\U00002708"  # airplane emoji (closest to Telegram)

    @property
    def is_oauth(self) -> bool:
        # Telegram uses widget-based auth, not standard OAuth redirect
        return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "bot_token": {"type": "string", "required": True, "sensitive": True,
                          "description": "Telegram Bot API token"},
            "bot_username": {"type": "string", "required": True,
                             "description": "Telegram bot username (without @)"},
        }

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        # Not used — Telegram uses a widget embedded in the login page
        return ""

    def exchange_code(self, code: str, redirect_uri: str) -> AuthResult:
        # Not used — Telegram sends signed user data, not an auth code
        return AuthResult(success=False, error="Use validate_telegram_data instead")

    def validate_telegram_data(self, data: Dict[str, str]) -> AuthResult:
        """Verify Telegram Login Widget callback data.

        The data dict contains: id, first_name, last_name, username,
        photo_url, auth_date, hash.

        Verification: HMAC-SHA256 of sorted data fields using
        SHA256(bot_token) as the key.
        """
        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            return AuthResult(success=False, error="Bot token not configured")

        received_hash = data.get("hash", "")
        if not received_hash:
            return AuthResult(success=False, error="Missing hash in Telegram data")

        # Check auth_date (prevent replay attacks — max 5 min)
        auth_date = int(data.get("auth_date", "0"))
        if abs(time.time() - auth_date) > 300:
            return AuthResult(success=False, error="Telegram auth data expired")

        # Build data-check-string (sorted key=value, excluding hash)
        check_items = sorted(
            f"{k}={v}" for k, v in data.items() if k != "hash"
        )
        data_check_string = "\n".join(check_items)

        # Verify HMAC
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        expected_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(received_hash, expected_hash):
            logger.warning("[auth:telegram] Invalid hash — data tampered")
            return AuthResult(success=False, error="Invalid Telegram signature")

        tg_id = data.get("id", "")
        username = data.get("username", "")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        display = f"{first_name} {last_name}".strip() or username

        return AuthResult(
            success=True,
            user_id=f"telegram:{tg_id}",
            username=username or f"tg_{tg_id}",
            display_name=display,
            provider="telegram",
            claims={
                "provider": "telegram",
                "telegram_id": tg_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "photo_url": data.get("photo_url", ""),
            },
        )

    def get_widget_html(self, callback_url: str) -> str:
        """Generate the Telegram Login Widget HTML for embedding in login page."""
        bot_username = self.config.get("bot_username", "")
        if not bot_username:
            return ""
        return (
            f'<script async src="https://telegram.org/js/telegram-widget.js?22" '
            f'data-telegram-login="{bot_username}" '
            f'data-size="large" '
            f'data-auth-url="{callback_url}" '
            f'data-request-access="write"></script>'
        )
