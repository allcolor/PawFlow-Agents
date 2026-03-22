"""Amazon Login with Amazon OAuth2 authentication provider."""

from typing import Any, Dict
from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


class AmazonAuthProvider(OAuthBaseProvider):
    """Login with Amazon OAuth2 provider."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._scope = config.get("scope", "profile")
        self._authorize_url = "https://www.amazon.com/ap/oa"
        self._token_url = "https://api.amazon.com/auth/o2/token"
        self._userinfo_url = "https://api.amazon.com/user/profile"

    @property
    def name(self) -> str:
        return "amazon"

    @property
    def display_name(self) -> str:
        return "Sign in with Amazon"

    @property
    def icon(self) -> str:
        return "a"

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "Login with Amazon client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "Login with Amazon client secret"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        email = userinfo.get("email", "")
        return AuthResult(
            success=True,
            user_id=f"amazon:{userinfo.get('user_id', '')}",
            username=email.split("@")[0] if email else userinfo.get("name", ""),
            email=email,
            display_name=userinfo.get("name", ""),
            provider="amazon",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": "amazon"},
        )
