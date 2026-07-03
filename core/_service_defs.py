"""Shared definitions for the service registry: scope constants, uniqueness
constraints, the ServiceDef dataclass, and small module-level helpers.

Split out of service_registry.py so the persistence/resolution mixin
(_service_registry_io) and the registry class can both depend on these names
without a circular import.
"""

import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

import core.paths as _paths


# Resolve paths dynamically — capturing at module import time freezes them
# to the real RUNTIME_DIR and defeats conftest.py's tmpdir redirect (tests
# would then write to the real data/runtime/services/). Call-site lookup
# keeps a single source of truth in core.paths.
def _global_services_dir() -> Path:
    return _paths.RUNTIME_DIR / "services" / "global"


def _user_services_dir() -> Path:
    return _paths.RUNTIME_DIR / "services" / "users"


CONV_EXTRAS_KEY = "conv_services"

# Scopes
SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_CONV = "conv"
VALID_SCOPES = (SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV)

# Global scope uses a fixed scope_id internally
_GLOBAL_SCOPE_ID = "__global__"


def _parent_conversation_id(conv_id: str) -> str:
    conv_id = str(conv_id or "")
    for marker in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
        if marker in conv_id:
            return conv_id.split(marker, 1)[0]
    return ""


def _package_runtime_dedupe_key(sdef) -> tuple:
    runtime = (getattr(sdef, "config", {}) or {}).get("package_runtime") or {}
    return (
        getattr(sdef, "service_id", ""),
        str(runtime.get("package") or ""),
        str(runtime.get("object_id") or ""),
    )

# Service types that support heartbeat (have a ping() method)
_HEARTBEAT_TYPES = frozenset({"relay"})

# ── Uniqueness constraints ────────────────────────────────────────────
#
# Some services bind exclusive OS/network resources (ports, bot tokens,
# file locks). Installing a second instance with the same resource key
# — even in a different scope — would fail at connect time or cause
# silent corruption.  We enforce uniqueness at install() time.
#
# Each entry maps a service_type to a tuple of config keys whose
# combined value must be unique **across all scopes**.

_UNIQUE_RESOURCE_KEYS: Dict[str, tuple] = {
    # OS-level port bind — two listeners on the same port = EADDRINUSE
    "httpListener":  ("port",),
    # relay / toolRelay: routes on main HTTP listener, path includes service_id — uniqueness is implicit
    # Bot tokens open a single persistent connection (WS or long-poll)
    "discordBot":    ("bot_token",),
    "slackBot":      ("bot_token",),
    "telegramBot":   ("bot_token",),
    # One webhook registration per phone number
    "whatsappCloud": ("phone_number_id",),

    # Two cache clients with the same prefix on the same Redis = data corruption
    "distributedMapCache": ("redis_url", "key_prefix"),
    # Two trackers writing the same state file = corruption
    "fileTracking":  ("storage_path",),
    # Two SSL contexts for the same cert = pointless duplication
    "sslContext":    ("certfile",),
}

# Default config values for uniqueness keys.
# ONLY for params that have a real default in the service code.
# Required params with no default are NOT listed — if the user omits
# them, _resource_key() returns None and the service will fail at connect.
_UNIQUE_RESOURCE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "distributedMapCache": {"redis_url": "redis://localhost:6379/0", "key_prefix": "pawflow:"},
    "fileTracking":        {"storage_path": "file_tracking.json"},
}


class ResourceConflictError(ValueError):
    """Raised when installing a service that would conflict with an existing one."""


@dataclass
class ServiceDef:
    """Definition of a service (any scope)."""

    service_id: str
    service_type: str
    scope: str = SCOPE_GLOBAL
    scope_id: str = _GLOBAL_SCOPE_ID
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def __init__(self, service_id: str = "", service_type: str = "",
                 scope: str = SCOPE_GLOBAL, scope_id: str = _GLOBAL_SCOPE_ID,
                 config: Optional[Dict[str, Any]] = None,
                 enabled: bool = True, description: str = "",
                 created_at: Optional[float] = None):
        self.service_id = service_id
        self.service_type = service_type
        self.config = config if config is not None else {}
        self.enabled = enabled
        self.description = description
        self.created_at = created_at if created_at is not None else time.time()
        self.scope = scope
        self.scope_id = scope_id

    @property
    def user_id(self) -> str:
        return self.scope_id if self.scope == SCOPE_USER else ""

    @property
    def conversation_id(self) -> str:
        return self.scope_id if self.scope == SCOPE_CONV else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceDef":
        data = dict(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
