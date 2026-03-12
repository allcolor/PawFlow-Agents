"""Authentication router — login, logout, user management, API keys, OAuth2."""

from fastapi import APIRouter, Depends, HTTPException, status
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
