"""Microsoft OAuth2 authentication provider."""

from typing import Any, Dict
from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


class MicrosoftAuthProvider(OAuthBaseProvider):
    """Microsoft / Azure AD OAuth2 provider."""

    DEFAULT_SCOPE = "openid email profile"

    def __init__(self, config: Dict[str, Any]):
        config.setdefault("scope", self.DEFAULT_SCOPE)
        super().__init__(config)
        self._userinfo_url = "https://graph.microsoft.com/v1.0/me"

    @property
    def _authorize_url(self) -> str:
        tenant = self.config.get("tenant", "common")
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"

    @_authorize_url.setter
    def _authorize_url(self, value):
        pass  # ignored — built dynamically from config

    @property
    def _token_url(self) -> str:
        tenant = self.config.get("tenant", "common")
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    @_token_url.setter
    def _token_url(self, value):
        pass  # ignored — built dynamically from config

    @property
    def name(self) -> str:
        return "microsoft"

    @property
    def display_name(self) -> str:
        return "Sign in with Microsoft"

    @property
    def icon(self) -> str:
        return "\U0001F7E6"  # blue square

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "Azure AD application client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "Azure AD client secret"},
            "tenant": {"type": "string", "required": False, "default": "common",
                       "description": "Azure AD tenant (common, organizations, or tenant ID)"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        email = userinfo.get("mail", userinfo.get("userPrincipalName", ""))
        return AuthResult(
            success=True,
            user_id=f"microsoft:{userinfo.get('id', '')}",
            username=email.split("@")[0] if email else userinfo.get("id", ""),
            email=email,
            display_name=userinfo.get("displayName", ""),
            provider="microsoft",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": "microsoft"},
        )
