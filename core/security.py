"""Security Module - Authentication, authorization, and permissions.

Provides:
- User management with roles (admin, editor, viewer, operator)
- Permission checks for operations (create/edit/delete flows, execute, admin)
- Session-based auth for the Streamlit GUI
- API key management for worker endpoints
- OAuth2 support via pluggable providers (Google, GitHub, custom OIDC)

Roles:
    admin    - Full access: manage users, plugins, settings, all operations
    editor   - Create/edit/delete flows, execute, view monitoring
    operator - Execute flows, view monitoring, inject FlowFiles
    viewer   - Read-only: view flows, monitoring, history
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

import core.paths as _paths


class Role(Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Permission definitions per role
ROLE_PERMISSIONS = {
    Role.ADMIN: {
        "flow.create", "flow.edit", "flow.delete", "flow.execute",
        "flow.import", "flow.export",
        "monitor.view", "monitor.clear",
        "plugin.install", "plugin.uninstall",
        "settings.edit", "user.manage",
        "worker.manage", "service.manage",
    },
    Role.EDITOR: {
        "flow.create", "flow.edit", "flow.delete", "flow.execute",
        "flow.import", "flow.export",
        "monitor.view",
        "service.manage",
    },
    Role.OPERATOR: {
        "flow.execute",
        "monitor.view",
    },
    Role.VIEWER: {
        "monitor.view",
    },
}


@dataclass
class User:
    """A system user."""
    username: str
    password_hash: str = ""
    role: Role = Role.VIEWER
    email: str = ""
    display_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_login: str = ""
    enabled: bool = True

    def check_password(self, password: str) -> bool:
        ok = _verify_password(password, self.password_hash, self.username)
        # Auto-upgrade legacy hashes to PBKDF2 on successful login
        if ok and not self.password_hash.startswith("pbkdf2:"):
            self.password_hash = _hash_password(password)
        return ok

    def has_permission(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "role": self.role.value,
            "email": self.email,
            "display_name": self.display_name or self.username,
            "created_at": self.created_at,
            "last_login": self.last_login,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'User':
        return User(
            username=data["username"],
            password_hash=data.get("password_hash", ""),
            role=Role(data.get("role", "viewer")),
            email=data.get("email", ""),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", ""),
            last_login=data.get("last_login", ""),
            enabled=data.get("enabled", True),
        )


@dataclass
class Session:
    """An authenticated session."""
    session_id: str
    username: str
    role: Role
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    ip_address: str = ""
    oauth_provider: str = ""  # Which OAuth provider created this session

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at


def _hash_password_legacy(password: str, salt: str = "") -> str:
    """Legacy hash: SHA-256 + salt (kept for backward compatibility)."""
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _hash_password(password: str, salt: str = "") -> str:
    """Hash a password with PBKDF2-HMAC-SHA256.

    Returns 'pbkdf2:<hex_salt>:<hex_hash>' format.
    The *salt* parameter (username) is ignored; a random salt is used instead.
    """
    random_salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), random_salt, 600_000)
    return f"pbkdf2:{random_salt.hex()}:{dk.hex()}"


def _verify_password(password: str, stored_hash: str, username: str = "") -> bool:
    """Verify a password against a stored hash.

    Supports both the new PBKDF2 format and the legacy SHA-256 format.
    """
    if stored_hash.startswith("pbkdf2:"):
        parts = stored_hash.split(":")
        if len(parts) != 3:
            return False
        salt_bytes = bytes.fromhex(parts[1])
        expected = parts[2]
        dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt_bytes, 600_000)
        return hmac.compare_digest(dk.hex(), expected)
    else:
        # Legacy SHA-256 verification
        return hmac.compare_digest(stored_hash, _hash_password_legacy(password, username))


class SecurityManager:
    """Manages users, sessions, and authorization.

    Usage:
        security = SecurityManager.get_instance()

        # Authentication
        session = security.authenticate("admin", "password")
        if session:
            if security.check_permission(session, "flow.edit"):
                # allowed
            security.logout(session.session_id)

        # User management
        security.create_user("bob", "pass123", Role.EDITOR)
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'SecurityManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._users: Dict[str, User] = {}
        self._sessions: Dict[str, Session] = {}
        self._session_ttl = 3600 * 8  # 8 hours
        self._oauth_config: Dict[str, Any] = {}
        self._api_keys: Dict[str, str] = {}
        self._auto_provision: Dict[str, Any] = {"rules": [], "default_action": "deny"}
        self._load_config()
        self._load_users()
        self._load_sessions()


    # -- User management --

    def create_user(self, username: str, password: str, role: Role = Role.VIEWER,
                    email: str = "", display_name: str = "") -> User:
        if username in self._users:
            raise ValueError(f"User '{username}' already exists")
        user = User(
            username=username,
            password_hash=_hash_password(password),
            role=role,
            email=email,
            display_name=display_name or username,
        )
        self._users[username] = user
        self._save_users()
        logger.info(f"User created: {username} (role={role.value})")
        return user

    def update_user(self, username: str, role: Optional[Role] = None,
                    password: Optional[str] = None, enabled: Optional[bool] = None,
                    email: Optional[str] = None):
        user = self._users.get(username)
        if not user:
            raise ValueError(f"User '{username}' not found")
        if role is not None:
            user.role = role
        if password is not None:
            user.password_hash = _hash_password(password)
        if enabled is not None:
            user.enabled = enabled
        if email is not None:
            user.email = email
        self._save_users()

    def delete_user(self, username: str):
        if username in self._users:
            del self._users[username]
            # Invalidate sessions
            to_remove = [sid for sid, s in self._sessions.items()
                         if s.username == username]
            for sid in to_remove:
                del self._sessions[sid]
            self._save_users()

    def get_user(self, username: str) -> Optional[User]:
        return self._users.get(username)

    def list_users(self) -> List[Dict[str, Any]]:
        return [
            {k: v for k, v in u.to_dict().items() if k != "password_hash"}
            for u in self._users.values()
        ]

    # -- Authentication --

    def authenticate(self, username: str, password: str,
                     ip_address: str = "") -> Optional[Session]:
        """Authenticate with username/password. Returns session or None."""
        user = self._users.get(username)
        if not user or not user.enabled:
            return None
        if not user.check_password(password):
            logger.warning(f"Failed login attempt for '{username}'")
            return None

        session = self._create_session(user, ip_address)
        user.last_login = datetime.now().isoformat()
        self._save_users()
        return session

    def _create_session(self, user: User, ip_address: str = "",
                        oauth_provider: str = "") -> Session:
        session = Session(
            session_id=secrets.token_urlsafe(32),
            username=user.username,
            role=user.role,
            expires_at=time.time() + self._session_ttl,
            ip_address=ip_address,
            oauth_provider=oauth_provider,
        )
        self._sessions[session.session_id] = session
        self._save_sessions()
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if not session:
            return None
        # Expired sessions are NOT deleted here — caller (validate_session_auth)
        # will attempt silent refresh using session.oauth_provider before giving up.
        if session.is_expired:
            return session  # return as-is, caller checks is_expired
        # Sliding window: extend session on each access
        new_expiry = time.time() + self._session_ttl
        # Persist if extended by more than 5 minutes
        if new_expiry - session.expires_at > 300:
            session.expires_at = new_expiry
            self._save_sessions()
        else:
            session.expires_at = new_expiry
        return session

    def logout(self, session_id: str):
        self._sessions.pop(session_id, None)
        self._save_sessions()

    def check_permission(self, session: Session, permission: str) -> bool:
        """Check if a session has a specific permission."""
        if session.is_expired:
            return False
        return permission in ROLE_PERMISSIONS.get(session.role, set())

    # -- API Keys --

    def generate_api_key(self, description: str = "") -> str:
        """Generate a new API key for worker/API auth."""
        key = secrets.token_urlsafe(32)
        self._api_keys[key] = description or f"Key generated {datetime.now().isoformat()}"
        self._save_config()
        return key

    def validate_api_key(self, key: str) -> bool:
        return key in self._api_keys

    def revoke_api_key(self, key: str):
        self._api_keys.pop(key, None)
        self._save_config()

    def list_api_keys(self) -> List[Dict[str, str]]:
        return [
            {"key": k[:8] + "...", "description": v}
            for k, v in self._api_keys.items()
        ]

    # -- OAuth2 Config --

    def set_oauth_config(self, provider: str, config: Dict[str, str]):
        """Set OAuth2 config for a provider.

        Config should include: client_id, client_secret, authorize_url,
        token_url, userinfo_url, redirect_uri
        """
        self._oauth_config[provider] = config
        self._save_config()

    def get_oauth_config(self, provider: str) -> Optional[Dict[str, str]]:
        return self._oauth_config.get(provider)

    def list_oauth_providers(self) -> List[str]:
        return list(self._oauth_config.keys())

    # -- Persistence --

    def _load_config(self):
        path = _paths.SECURITY_FILE
        if path.exists():
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self._session_ttl = data.get("session_ttl", 3600 * 8)
                self._oauth_config = data.get("oauth_providers", {})
                self._api_keys = data.get("api_keys", {})
                self._auto_provision = data.get("auto_provision", {
                    "rules": [], "default_action": "deny",
                })
            except Exception as e:
                logger.error(f"Failed to load security config: {e}")

    def _save_config(self):
        path = _paths.SECURITY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_ttl": self._session_ttl,
            "oauth_providers": self._oauth_config,
            "api_keys": self._api_keys,
            "auto_provision": getattr(self, '_auto_provision', {
                "rules": [], "default_action": "deny",
            }),
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def get_auto_provision(self) -> dict:
        """Get auto-provisioning rules (read from security.json, not flow config)."""
        return getattr(self, '_auto_provision', {
            "rules": [], "default_action": "deny",
        })

    def _load_users(self):
        path = _paths.USERS_FILE
        if path.exists():
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                for user_data in data.get("users", []):
                    user = User.from_dict(user_data)
                    self._users[user.username] = user
            except Exception as e:
                logger.error(f"Failed to load users: {e}")

        # Ensure default admin exists
        if "admin" not in self._users:
            self._users["admin"] = User(
                username="admin",
                password_hash=_hash_password("admin"),
                role=Role.ADMIN,
                display_name="Administrator",
            )
            self._save_users()

    def _save_users(self):
        path = _paths.USERS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"users": [u.to_dict() for u in self._users.values()]}
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    _SESSION_HARD_EXPIRY = 14 * 24 * 3600  # 2 weeks — delete sessions with no activity

    def _load_sessions(self):
        """Load sessions from disk (survives process/module reloads).

        Expired sessions are KEPT (for silent OAuth refresh) unless they
        exceed the hard expiry (2 weeks with no activity).
        """
        path = _paths.SESSIONS_FILE
        if not path.exists():
            return
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            now = time.time()
            skipped = 0
            for s in data.get("sessions", []):
                expires_at = s.get("expires_at", 0)
                # Hard expiry: if session hasn't been touched in 2 weeks, drop it
                if expires_at > 0 and now - expires_at > self._SESSION_HARD_EXPIRY:
                    skipped += 1
                    continue
                session = Session(
                    session_id=s["session_id"],
                    username=s["username"],
                    role=Role(s["role"]),
                    created_at=s.get("created_at", now),
                    expires_at=expires_at,
                    ip_address=s.get("ip_address", ""),
                    oauth_provider=s.get("oauth_provider", ""),
                )
                self._sessions[session.session_id] = session
            if self._sessions or skipped:
                logger.info(f"Restored {len(self._sessions)} session(s) from disk"
                            + (f" ({skipped} stale removed)" if skipped else ""))
            if skipped:
                self._save_sessions()
        except Exception as e:
            logger.warning(f"Failed to load sessions: {e}")

    def _save_sessions(self):
        """Persist sessions to disk. Keeps expired sessions for silent refresh.
        Only hard-expired sessions (>2 weeks) are excluded.
        """
        path = _paths.SESSIONS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        sessions = [
            {
                "session_id": s.session_id,
                "username": s.username,
                "role": s.role.value,
                "created_at": s.created_at,
                "expires_at": s.expires_at,
                "ip_address": s.ip_address,
                "oauth_provider": s.oauth_provider,
            }
            for s in self._sessions.values()
            # Keep expired sessions for silent refresh; only drop hard-expired
            if not (s.expires_at > 0 and now - s.expires_at > self._SESSION_HARD_EXPIRY)
        ]
        with open(path, 'w') as f:
            json.dump({"sessions": sessions}, f, indent=2)

    # -- Cleanup --

    def cleanup_expired_sessions(self) -> int:
        """Remove hard-expired sessions (>2 weeks). Soft-expired sessions are
        kept for silent OAuth refresh."""
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if s.expires_at > 0 and now - s.expires_at > self._SESSION_HARD_EXPIRY]
        for sid in stale:
            del self._sessions[sid]
        if stale:
            self._save_sessions()
        return len(stale)

    def delete_session(self, session_id: str):
        """Hard-delete a session (after failed silent refresh)."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save_sessions()
