"""Authentication router — login, logout, user management, API keys, OAuth2.

Includes Claude Code OAuth PKCE flow for LLM service credential login.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, List, Dict

from core.security import SecurityManager, Role, ROLE_PERMISSIONS
from api.auth import get_security_manager, get_current_session, require_permission

router = APIRouter()


# -- Request/Response models --

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    session_id: str
    username: str
    role: str
    expires_at: float


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    email: str = ""
    display_name: str = ""


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None
    email: Optional[str] = None


class OAuthConfigRequest(BaseModel):
    client_id: str
    client_secret: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    redirect_uri: str = ""


# -- Endpoints --

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, security: SecurityManager = Depends(get_security_manager)):
    """Authenticate with username/password. Returns a session token."""
    session = security.authenticate(req.username, req.password)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return LoginResponse(
        session_id=session.session_id,
        username=session.username,
        role=session.role.value,
        expires_at=session.expires_at,
    )


@router.post("/logout")
def logout(
    session=Depends(get_current_session),
    security: SecurityManager = Depends(get_security_manager),
):
    """Logout current session."""
    if session:
        security.logout(session.session_id)
    return {"status": "ok"}


@router.get("/me")
def get_me(session=Depends(get_current_session)):
    """Get current user info."""
    if session is None:
        return {"username": "anonymous", "role": "admin", "auth_enabled": False}
    return {
        "username": session.username,
        "role": session.role.value,
        "session_id": session.session_id,
        "expires_at": session.expires_at,
    }


@router.get("/config")
def get_auth_config(
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Get auth configuration."""
    return {
        "auth_enabled": security.auth_enabled,
    }


@router.put("/config")
def update_auth_config(
    enabled: bool,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Enable or disable authentication."""
    security.enable_auth(enabled)
    return {"auth_enabled": security.auth_enabled}


# -- User management --

@router.get("/users")
def list_users(
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """List all users."""
    return security.list_users()


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    req: CreateUserRequest,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Create a new user."""
    try:
        role = Role(req.role)
    except ValueError:
        raise HTTPException(400, f"Invalid role: {req.role}. Valid: {[r.value for r in Role]}")
    try:
        user = security.create_user(
            req.username, req.password, role,
            email=req.email, display_name=req.display_name,
        )
        return {"username": user.username, "role": user.role.value}
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.put("/users/{username}")
def update_user(
    username: str,
    req: UpdateUserRequest,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Update user properties."""
    try:
        role = Role(req.role) if req.role else None
        security.update_user(
            username, role=role, password=req.password,
            enabled=req.enabled, email=req.email,
        )
        return {"status": "updated"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/users/{username}")
def delete_user(
    username: str,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Delete a user."""
    if username == "admin":
        raise HTTPException(400, "Cannot delete admin user")
    security.delete_user(username)
    return {"status": "deleted"}


# -- API Keys --

@router.get("/api-keys")
def list_api_keys(
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """List API keys."""
    return security.list_api_keys()


@router.post("/api-keys")
def create_api_key(
    description: str = "API key",
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Generate a new API key."""
    key = security.generate_api_key(description)
    return {"key": key, "description": description}


@router.delete("/api-keys/{key}")
def revoke_api_key(
    key: str,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Revoke an API key."""
    security.revoke_api_key(key)
    return {"status": "revoked"}


# -- OAuth2 --

@router.get("/oauth/providers")
def list_oauth_providers(
    security: SecurityManager = Depends(get_security_manager),
):
    """List configured OAuth2 providers."""
    return security.list_oauth_providers()


@router.get("/oauth/providers/{provider}")
def get_oauth_config(
    provider: str,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Get OAuth2 provider configuration."""
    config = security.get_oauth_config(provider)
    if not config:
        raise HTTPException(404, f"Provider '{provider}' not configured")
    return config


@router.put("/oauth/providers/{provider}")
def set_oauth_config(
    provider: str,
    req: OAuthConfigRequest,
    _=Depends(require_permission("user.manage")),
    security: SecurityManager = Depends(get_security_manager),
):
    """Configure an OAuth2 provider."""
    security.set_oauth_config(provider, req.model_dump())
    return {"status": "configured", "provider": provider}


# -- Roles & Permissions --

@router.get("/roles")
def list_roles():
    """List available roles and their permissions."""
    return {
        role.value: sorted(list(perms))
        for role, perms in ROLE_PERMISSIONS.items()
    }


# -- Claude Code OAuth PKCE Login --
# Allows an LLM service to authenticate with Claude via Anthropic's OAuth.
# Tokens are stored in secrets, referenced by the service config.

_CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_AUTHORIZE_URL = "https://console.anthropic.com/oauth/authorize"
_CLAUDE_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_CLAUDE_SCOPES = "user:inference user:profile user:sessions:claude_code"

# In-memory PKCE state storage (short-lived, per login attempt)
_pkce_states: Dict[str, dict] = {}


@router.get("/claude-code/login")
def claude_code_login(service_id: str, request: Request):
    """Start Claude Code OAuth PKCE flow.

    Redirects to Anthropic's authorize page. On success, callback
    saves tokens to secrets and updates the service config.
    """
    import base64
    import hashlib
    import secrets as _secrets

    # Generate PKCE
    code_verifier = _secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    state = _secrets.token_urlsafe(32)

    # Build callback URL from request
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    redirect_uri = f"{scheme}://{host}/api/auth/claude-code/callback"

    _pkce_states[state] = {
        "code_verifier": code_verifier,
        "service_id": service_id,
        "redirect_uri": redirect_uri,
    }

    import urllib.parse
    params = urllib.parse.urlencode({
        "client_id": _CLAUDE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _CLAUDE_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return RedirectResponse(f"{_CLAUDE_AUTHORIZE_URL}?{params}")


@router.get("/claude-code/callback")
def claude_code_callback(code: str, state: str):
    """Handle Anthropic OAuth callback: exchange code for tokens, save to service."""
    import json as _json
    import time as _time
    import urllib.request
    import urllib.error

    pkce = _pkce_states.pop(state, None)
    if not pkce:
        raise HTTPException(400, "Invalid or expired state")

    service_id = pkce["service_id"]
    code_verifier = pkce["code_verifier"]
    redirect_uri = pkce["redirect_uri"]

    # Exchange code for tokens
    payload = _json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": _CLAUDE_OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }).encode("utf-8")

    req = urllib.request.Request(
        _CLAUDE_TOKEN_URL, data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise HTTPException(502, f"Token exchange failed ({e.code}): {body}")

    access_token = data.get("accessToken", "")
    refresh_token = data.get("refreshToken", "")
    expires_at = data.get("expiresAt", 0)

    if not access_token:
        raise HTTPException(502, f"No access token in response: {list(data.keys())}")

    # Save tokens to secrets (encrypted)
    from pathlib import Path
    from core.secrets import get_secrets_manager
    sm = get_secrets_manager()

    secrets_path = Path("config/global_secrets.json")
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if secrets_path.exists():
        existing = _json.loads(secrets_path.read_text(encoding="utf-8"))

    prefix = service_id.replace("-", "_")
    existing[f"{prefix}_access_token"] = sm.encrypt(access_token)
    existing[f"{prefix}_refresh_token"] = sm.encrypt(refresh_token)
    existing[f"{prefix}_expires_at"] = str(int(expires_at))
    secrets_path.write_text(
        _json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update service config to reference secrets
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        greg = GlobalServiceRegistry.get_instance()
        sdef = greg.get_service(service_id)
        if sdef:
            cfg = getattr(sdef, "config", {}) or {}
            cfg["claude_access_token"] = f"${{secrets.global.{prefix}_access_token}}"
            cfg["claude_refresh_token"] = f"${{secrets.global.{prefix}_refresh_token}}"
            cfg["claude_expires_at"] = f"${{secrets.global.{prefix}_expires_at}}"
            greg.update_service(service_id, config=cfg)

            # Also update live instance attrs so next call picks up tokens immediately
            live = greg.get_live_instance(service_id)
            if live and hasattr(live, '_client') and live._client:
                live._client.claude_access_token = access_token
                live._client.claude_refresh_token = refresh_token
                live._client.claude_expires_at = int(expires_at)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to update service config: %s", e)

    expires_in_h = (expires_at - _time.time() * 1000) / 3600000
    return {
        "status": "ok",
        "service_id": service_id,
        "expires_in_hours": round(expires_in_h, 1),
        "message": f"Claude Code credentials saved for service '{service_id}'. "
                   f"Token expires in {expires_in_h:.1f}h.",
    }
