"""Facebook/Meta OAuth2 authentication provider."""

from typing import Any, Dict
from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


class FacebookAuthProvider(OAuthBaseProvider):
    """Facebook (Meta) OAuth2 provider. Works for Facebook and Instagram Login."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._scope = config.get("scope", "email public_profile")
        self._authorize_url = "https://www.facebook.com/v19.0/dialog/oauth"
        self._token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
        self._userinfo_url = "https://graph.facebook.com/me?fields=id,name,email,picture"

    @property
    def name(self) -> str:
        return "facebook"

    @property
    def display_name(self) -> str:
        return "Sign in with Facebook"

    @property
    def icon(self) -> str:
        return "f"

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "Facebook App ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "Facebook App Secret"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        email = userinfo.get("email", "")
        return AuthResult(
            success=True,
            user_id=f"facebook:{userinfo.get('id', '')}",
            username=email.split("@")[0] if email else f"fb_{userinfo.get('id', '')}",
            email=email,
            display_name=userinfo.get("name", ""),
            provider="facebook",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": "facebook"},
        )
