"""GitHub OAuth2 authentication provider."""

from typing import Any, Dict
from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


class GitHubAuthProvider(OAuthBaseProvider):
    """GitHub OAuth2 provider."""

    DEFAULT_SCOPE = "read:user user:email"

    def __init__(self, config: Dict[str, Any]):
        config.setdefault("scope", self.DEFAULT_SCOPE)
        super().__init__(config)
        self._authorize_url = "https://github.com/login/oauth/authorize"
        self._token_url = "https://github.com/login/oauth/access_token"  # nosec B105
        self._userinfo_url = "https://api.github.com/user"

    @property
    def name(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        return "Sign in with GitHub"

    @property
    def icon(self) -> str:
        return "\U0001F4BB"  # laptop emoji

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "GitHub OAuth App client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "GitHub OAuth App client secret"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        email = userinfo.get("email", "")
        login = userinfo.get("login", "")
        return AuthResult(
            success=True,
            user_id=f"github:{userinfo.get('id', '')}",
            username=login,
            email=email,
            display_name=userinfo.get("name", login),
            provider="github",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": "github"},
        )
