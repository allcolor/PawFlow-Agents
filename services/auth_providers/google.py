"""Google OAuth2 authentication provider."""

from typing import Any, Dict

from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


class GoogleAuthProvider(OAuthBaseProvider):
    """Google OAuth2 provider with OpenID Connect."""

    DEFAULT_SCOPE = "openid email profile"

    def __init__(self, config: Dict[str, Any]):
        config.setdefault("scope", self.DEFAULT_SCOPE)
        super().__init__(config)
        self._authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
        self._token_url = "https://oauth2.googleapis.com/token"  # nosec B105
        self._userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"

    @property
    def name(self) -> str:
        return "google"

    @property
    def display_name(self) -> str:
        return "Sign in with Google"

    @property
    def icon(self) -> str:
        return "G"

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "Google OAuth2 client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "Google OAuth2 client secret"},
            "scope": {"type": "string", "required": False,
                      "default": "openid email profile",
                      "description": "OAuth2 scopes"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        email = userinfo.get("email", "")
        return AuthResult(
            success=True,
            user_id=f"google:{userinfo.get('sub', '')}",
            username=email.split("@")[0] if email else userinfo.get("sub", ""),
            email=email,
            display_name=userinfo.get("name", ""),
            provider="google",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={
                **userinfo,
                "provider": "google",
                "email_verified": userinfo.get("email_verified", False),
                "hd": userinfo.get("hd", ""),  # Google hosted domain
            },
        )
