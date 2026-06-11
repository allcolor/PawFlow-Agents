"""X.com (Twitter) OAuth2 authentication provider."""

import threading
import time
from typing import Any, Dict
from services.auth_providers.base import AuthResult
from services.auth_providers.oauth_base import OAuthBaseProvider

# PKCE verifiers expire with the authorize flow; OAuth states use a 600s TTL.
_VERIFIER_TTL = 600


class XTwitterAuthProvider(OAuthBaseProvider):
    """X.com (formerly Twitter) OAuth2 provider with PKCE."""

    DEFAULT_SCOPE = "users.read tweet.read"

    def __init__(self, config: Dict[str, Any]):
        config.setdefault("scope", self.DEFAULT_SCOPE)
        super().__init__(config)
        self._authorize_url = "https://twitter.com/i/oauth2/authorize"
        self._token_url = "https://api.twitter.com/2/oauth2/token"  # nosec B105
        self._userinfo_url = "https://api.twitter.com/2/users/me"
        self._code_verifier = ""
        # PKCE verifiers keyed by OAuth state so concurrent logins don't
        # clobber each other; _code_verifier is the stateless fallback.
        self._verifiers: Dict[str, tuple] = {}
        self._verifiers_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "x"

    @property
    def display_name(self) -> str:
        return "Sign in with X"

    @property
    def icon(self) -> str:
        return "X"

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "client_id": {"type": "string", "required": True,
                          "description": "X.com OAuth2 client ID"},
            "client_secret": {"type": "string", "required": True, "sensitive": True,
                              "description": "X.com OAuth2 client secret"},
        }

    def _customize_authorize_params(self, params: dict):
        """X requires PKCE (code_challenge)."""
        import hashlib, base64, secrets
        verifier = secrets.token_urlsafe(64)
        self._code_verifier = verifier
        state = str(params.get("state") or "")
        if state:
            now = time.time()
            with self._verifiers_lock:
                self._verifiers = {
                    s: v for s, v in self._verifiers.items()
                    if v[1] > now}
                self._verifiers[state] = (verifier, now + _VERIFIER_TTL)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    def _verifier_for(self, state: str) -> str:
        """Pop the PKCE verifier minted for this state, or the fallback."""
        if state:
            with self._verifiers_lock:
                entry = self._verifiers.pop(state, None)
            if entry and entry[1] > time.time():
                return entry[0]
        return self._code_verifier

    def _request_token(self, code: str, redirect_uri: str,
                       state: str = "") -> dict:
        """Override to include code_verifier for PKCE."""
        import base64
        import urllib.parse
        parsed = urllib.parse.urlparse(self._token_url)
        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")
        body = urllib.parse.urlencode({
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": self._verifier_for(state),
        }).encode()
        basic = base64.b64encode(
            f"{client_id}:{client_secret}".encode()).decode("ascii")
        try:
            import json
            conn = self._make_conn(parsed)
            conn.request("POST", parsed.path, body=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Authorization": f"Basic {basic}",
                                  "Accept": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()
            return data
        except Exception as e:
            return {"error": str(e)}

    def _fetch_userinfo(self, access_token: str) -> dict:
        """Override: X returns nested data structure."""
        import json
        parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(self._userinfo_url)
        path = parsed.path + "?user.fields=id,name,username,profile_image_url"
        try:
            conn = self._make_conn(parsed)
            conn.request("GET", path,
                         headers={"Authorization": f"Bearer {access_token}"})
            resp = conn.getresponse()
            raw = json.loads(resp.read().decode())
            conn.close()
            return raw.get("data", raw)
        except Exception:
            return {}

    def _build_result(self, userinfo: dict, access_token: str,
                       refresh_token: str, expires_at: float) -> AuthResult:
        return AuthResult(
            success=True,
            user_id=f"x:{userinfo.get('id', '')}",
            username=userinfo.get("username", ""),
            email="",  # X doesn't provide email in basic scope
            display_name=userinfo.get("name", ""),
            provider="x",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=expires_at,
            claims={**userinfo, "provider": "x"},
        )
