"""Authentication dependencies for the FastAPI REST API.

Supports three auth methods:
1. Bearer session token (from /auth/login)
2. API key (from SecurityManager)
3. No auth required when auth is disabled
"""

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from core.security import SecurityManager, Session, Role, ROLE_PERMISSIONS

security_scheme = HTTPBearer(auto_error=False)


def get_security_manager() -> SecurityManager:
    """Get the global SecurityManager singleton."""
    return SecurityManager.get_instance()


def get_current_session(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    security: SecurityManager = Depends(get_security_manager),
) -> Optional[Session]:
    """Extract and validate the current session from the Authorization header.

    If auth is disabled, returns None (meaning all access granted).
    If auth is enabled, validates Bearer token as session ID or API key.
    """
    if not security.auth_enabled:
        return None

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Try session token first
    session = security.get_session(token)
    if session:
        return session

    # Try API key
    if security.validate_api_key(token):
        # API keys get admin-level access
        return Session(
            session_id=token,
            username="api_key",
            role=Role.ADMIN,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(permission: str):
    """Dependency factory that checks a specific permission."""

    def checker(
        session: Optional[Session] = Depends(get_current_session),
        security: SecurityManager = Depends(get_security_manager),
    ):
        if not security.auth_enabled:
            return session
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not security.check_permission(session, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission}",
            )
        return session

    return checker
