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
        login = userinfo.get("login", "")
        # The /user payload's `email` is the user's PUBLIC profile email,
        # which is user-settable and may be unverified. Never trust it for
        # identity matching. Fetch the account's verified primary email from
        # /user/emails and only treat it as verified when GitHub says so.
        email, email_verified = self._fetch_verified_email(access_token)
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
            claims={**userinfo, "provider": "github",
                    "email": email, "email_verified": email_verified},
        )

    def _fetch_verified_email(self, access_token: str) -> tuple:
        """Return (email, verified) for the GitHub account's primary email.

        Uses the /user/emails endpoint (requires the user:email scope).
        Returns ("", False) when no verified primary email is available.
        """
        try:
            emails = self._fetch_userinfo_url(
                "https://api.github.com/user/emails", access_token)
        except Exception:
            return "", False
        if not isinstance(emails, list):
            return "", False
        primary = None
        for entry in emails:
            if isinstance(entry, dict) and entry.get("primary"):
                primary = entry
                break
        if primary is None:
            # Fall back to the first verified email if no primary is flagged.
            for entry in emails:
                if isinstance(entry, dict) and entry.get("verified"):
                    primary = entry
                    break
        if not isinstance(primary, dict):
            return "", False
        return str(primary.get("email") or ""), bool(primary.get("verified"))

    def _fetch_userinfo_url(self, url: str, access_token: str) -> Any:
        """GET an arbitrary GitHub API URL with the bearer token (JSON)."""
        import json
        import urllib.parse
        from services.auth_providers.oauth_base import OAUTH_HTTP_USER_AGENT
        parsed = urllib.parse.urlparse(url)
        conn = self._make_conn(parsed)
        try:
            conn.request("GET", parsed.path,
                         headers={"Authorization": f"Bearer {access_token}",
                                  "Accept": "application/json",
                                  "User-Agent": OAUTH_HTTP_USER_AGENT})
            resp = conn.getresponse()
            return json.loads(resp.read().decode())
        finally:
            conn.close()
