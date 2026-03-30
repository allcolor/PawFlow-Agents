"""Generic OAuth2 authentication provider — works with any OAuth2/OIDC server.

Supports Keycloak, Okta, Auth0, GitLab, or any custom OAuth2 provider.
All URLs are configured, no hardcoded endpoints.
"""

from typing import Any, Dict

from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider


_PRESETS = {
    "keycloak": {
        "display_name": "Sign in with Keycloak",
        "icon": "🔑",
        "scope": "openid email profile",
        "authorize_url": "https://{host}/realms/{realm}/protocol/openid-connect/auth",
        "token_url": "https://{host}/realms/{realm}/protocol/openid-connect/token",
        "userinfo_url": "https://{host}/realms/{realm}/protocol/openid-connect/userinfo",
    },
    "okta": {
        "display_name": "Sign in with Okta",
        "icon": "🔒",
        "scope": "openid email profile",
        "authorize_url": "https://{domain}/oauth2/default/v1/authorize",
        "token_url": "https://{domain}/oauth2/default/v1/token",
        "userinfo_url": "https://{domain}/oauth2/default/v1/userinfo",
    },
    "auth0": {
        "display_name": "Sign in with Auth0",
        "icon": "🛡",
        "scope": "openid email profile",
        "authorize_url": "https://{domain}/authorize",
        "token_url": "https://{domain}/oauth/token",
        "userinfo_url": "https://{domain}/userinfo",
    },
    "gitlab": {
        "display_name": "Sign in with GitLab",
        "icon": "🦊",
        "scope": "openid email profile",
        "authorize_url": "https://gitlab.com/oauth/authorize",
        "token_url": "https://gitlab.com/oauth/token",
        "userinfo_url": "https://gitlab.com/oauth/userinfo",
    },
}


class GenericOAuthProvider(OAuthBaseProvider):
    """Fully configurable OAuth2 provider. Supports presets for Keycloak, Okta, Auth0, GitLab."""

    def __init__(self, config: Dict[str, Any]):
        # Apply preset defaults into config (config values take precedence)
        preset_name = config.get("preset", "")
        preset = _PRESETS.get(preset_name, {})
        for key in ("display_name", "icon", "scope", "authorize_url", "token_url", "userinfo_url"):
            if key not in config and key in preset:
                config[key] = preset[key]
        config.setdefault("name", preset_name or "oauth")
        config.setdefault("scope", "openid email profile")
        super().__init__(config)

    @property
    def name(self) -> str:
        return self.config.get("name", "oauth")

    @property
    def display_name(self) -> str:
        return self.config.get("display_name", "Sign in with OAuth")

    @property
    def icon(self) -> str:
        return self.config.get("icon", "\U0001F510")

    @property
    def _authorize_url(self) -> str:
        return self.config.get("authorize_url", "")

    @_authorize_url.setter
    def _authorize_url(self, value):
        pass  # ignored — built dynamically from config

    @property
    def _token_url(self) -> str:
        return self.config.get("token_url", "")

    @_token_url.setter
    def _token_url(self, value):
        pass  # ignored — built dynamically from config

    @property
    def _userinfo_url(self) -> str:
        return self.config.get("userinfo_url", "")

    @_userinfo_url.setter
    def _userinfo_url(self, value):
        pass  # ignored — built dynamically from config

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "name": {"type": "string", "required": True,
                     "description": "Unique provider ID (e.g. 'keycloak', 'okta')"},
            "display_name": {"type": "string", "required": False, "default": "Sign in with OAuth",
                             "description": "Button label on login page"},
            "icon": {"type": "string", "required": False, "default": "🔐",
                     "description": "Icon/emoji for login button"},
            "client_id": {"type": "string", "required": True,
                          "description": "OAuth2 client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "OAuth2 client secret"},
            "authorize_url": {"type": "string", "required": True,
                              "description": "Authorization endpoint URL"},
            "token_url": {"type": "string", "required": True,
                          "description": "Token endpoint URL"},
            "userinfo_url": {"type": "string", "required": True,
                             "description": "UserInfo endpoint URL"},
            "scope": {"type": "string", "required": False, "default": "openid email profile",
                      "description": "OAuth2 scopes"},
            "field_user_id": {"type": "string", "required": False, "default": "sub",
                              "description": "UserInfo field for unique user ID"},
            "field_email": {"type": "string", "required": False, "default": "email",
                            "description": "UserInfo field for email"},
            "field_name": {"type": "string", "required": False, "default": "name",
                           "description": "UserInfo field for display name"},
            "field_username": {"type": "string", "required": False, "default": "preferred_username",
                               "description": "UserInfo field for username"},
        }

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        field_user_id = self.config.get("field_user_id", "sub")
        field_email = self.config.get("field_email", "email")
        field_name = self.config.get("field_name", "name")
        field_username = self.config.get("field_username", "preferred_username")
        provider_name = self.name

        uid = str(userinfo.get(field_user_id, ""))
        email = str(userinfo.get(field_email, ""))
        display = str(userinfo.get(field_name, ""))
        username = str(userinfo.get(field_username, ""))
        if not username:
            username = email.split("@")[0] if email else uid

        return AuthResult(
            success=True,
            user_id=f"{provider_name}:{uid}",
            username=username,
            email=email,
            display_name=display,
            provider=provider_name,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": provider_name},
        )
