"""Unified service registry for all scopes (global, user, conversation).

A single class that manages service definitions and live instances across
all three scopes. The scope determines storage and keying:

    global  — shared across all users. Persisted in data/runtime/services/global/{id}.json
    user    — per-user. Persisted in data/runtime/services/users/{user_id}/{id}.json
    conv    — per-conversation. Persisted in ConversationStore extras.

All scopes share the same CRUD interface — only the scope_id changes.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

from core import ServiceFactory, Service

logger = logging.getLogger(__name__)

import core.paths as _paths
_SERVICES_DIR = _paths.RUNTIME_DIR / "services"
_GLOBAL_SERVICES_DIR = _SERVICES_DIR / "global"
_USER_SERVICES_DIR = _SERVICES_DIR / "users"
CONV_EXTRAS_KEY = "conv_services"

# Scopes
SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_CONV = "conv"
VALID_SCOPES = (SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV)

# Global scope uses a fixed scope_id internally
_GLOBAL_SCOPE_ID = "__global__"

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
    # WebSocket listener port+path — same shared-port scheme
    "relay":         ("port", "path"),
    "toolRelay":     ("port", "path"),
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
    "httpListener":        {"port": "9090"},
    "relay":               {"port": "9091", "path": "/ws/relay"},
    "toolRelay":           {"port": "9091", "path": "/ws/tools"},

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
                 created_at: Optional[float] = None,
                 # Legacy aliases
                 user_id: str = "", conversation_id: str = ""):
        self.service_id = service_id
        self.service_type = service_type
        self.config = config if config is not None else {}
        self.enabled = enabled
        self.description = description
        self.created_at = created_at if created_at is not None else time.time()
        # Resolve scope/scope_id from legacy aliases
        if user_id and scope_id == _GLOBAL_SCOPE_ID:
            self.scope = SCOPE_USER
            self.scope_id = user_id
        elif conversation_id and scope_id == _GLOBAL_SCOPE_ID:
            self.scope = SCOPE_CONV
            self.scope_id = conversation_id
        else:
            self.scope = scope
            self.scope_id = scope_id

    @property
    def user_id(self) -> str:
        return self.scope_id if self.scope == SCOPE_USER else ""

    @property
    def conversation_id(self) -> str:
        return self.scope_id if self.scope == SCOPE_CONV else ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Backwards compat: include user_id / conversation_id in output
        if self.scope == SCOPE_USER:
            d["user_id"] = self.scope_id
        elif self.scope == SCOPE_CONV:
            d["conversation_id"] = self.scope_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceDef":
        # Accept legacy user_id / conversation_id as scope_id
        data = dict(data)
        if "user_id" in data and "scope_id" not in data:
            data["scope_id"] = data.pop("user_id")
            data.setdefault("scope", SCOPE_USER)
        elif "conversation_id" in data and "scope_id" not in data:
            data["scope_id"] = data.pop("conversation_id")
            data.setdefault("scope", SCOPE_CONV)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class ServiceRegistry:
    """Thread-safe singleton managing service definitions and live instances
    across global, user, and conversation scopes."""

    _instance: Optional["ServiceRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        # _definitions[scope_id][service_id] = ServiceDef
        self._definitions: Dict[str, Dict[str, ServiceDef]] = {}
        # _live_instances[scope_id][service_id] = Service
        self._live_instances: Dict[str, Dict[str, Service]] = {}
        self._data_lock = threading.Lock()
        self._loaded: set = set()  # scope_ids that have been loaded
        self._load_failed: set = set()  # scope_ids where load failed
        # Heartbeat
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False
        self._failure_counts: Dict[tuple, int] = {}

    @classmethod
    def get_instance(cls) -> "ServiceRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            inst = cls._instance
            if inst:
                inst._disconnect_all_scopes()
            cls._instance = None

    def _resolve_scope_id(self, scope: str, scope_id: str) -> str:
        """Normalize scope_id: global always uses the fixed key."""
        return _GLOBAL_SCOPE_ID if scope == SCOPE_GLOBAL else scope_id

    def _ensure_loaded(self, scope: str, scope_id: str):
        sid = self._resolve_scope_id(scope, scope_id)
        if sid not in self._loaded:
            with self._data_lock:
                if sid not in self._loaded:
                    self._load(scope, sid)
                    self._loaded.add(sid)

    def reload_scope(self, scope: str, scope_id: str = "") -> None:
        """Force reload definitions from disk for a scope.

        New services found on disk are loaded. Deleted files are removed.
        Already-connected services are NOT disconnected (only definitions update).
        """
        sid = self._resolve_scope_id(scope, scope_id)
        if sid in self._load_failed:
            return
        with self._data_lock:
            old_defs = set(self._definitions.get(sid, {}).keys())
        self._load(scope, sid)
        self._loaded.add(sid)
        with self._data_lock:
            new_defs = set(self._definitions.get(sid, {}).keys())
        added = new_defs - old_defs
        removed = old_defs - new_defs
        for sid_new in added:
            logger.info("Hot-reload: new service '%s' found on disk (scope=%s)", sid_new, scope)
        for sid_rm in removed:
            logger.info("Hot-reload: service '%s' removed from disk (scope=%s)", sid_rm, scope)

    # ---- Uniqueness ----

    @staticmethod
    def _resource_key(service_type: str, config: Dict[str, Any]) -> Optional[tuple]:
        """Return the uniqueness key for a service, or None if unconstrained.

        Returns None if any key has no value (not in config and no default)
        — the service will fail at connect anyway, so no conflict to check.
        """
        keys = _UNIQUE_RESOURCE_KEYS.get(service_type)
        if keys is None:
            return None
        defaults = _UNIQUE_RESOURCE_DEFAULTS.get(service_type, {})
        parts = []
        for k in keys:
            val = str(config.get(k, defaults.get(k, "")))
            if not val:
                return None  # required param missing — skip conflict check
            parts.append(val)
        return (service_type,) + tuple(parts)

    def _check_resource_conflict(
        self, service_type: str, config: Dict[str, Any],
        exclude_scope_id: str = "", exclude_service_id: str = "",
    ) -> None:
        """Raise ResourceConflictError if another service already owns this resource.

        Scans ALL scopes/scope_ids currently loaded.
        """
        rk = self._resource_key(service_type, config)
        if rk is None:
            return
        with self._data_lock:
            for sid, scope_defs in self._definitions.items():
                for svc_id, sdef in scope_defs.items():
                    if sid == exclude_scope_id and svc_id == exclude_service_id:
                        continue
                    if self._resource_key(sdef.service_type, sdef.config) == rk:
                        keys = _UNIQUE_RESOURCE_KEYS[service_type]
                        defaults = _UNIQUE_RESOURCE_DEFAULTS.get(service_type, {})
                        vals = {k: config.get(k, defaults.get(k, "")) for k in keys}
                        raise ResourceConflictError(
                            f"Resource conflict: {service_type} with {vals} "
                            f"is already in use by service '{svc_id}' "
                            f"(scope={sdef.scope})"
                        )

    # ---- CRUD ----

    def install(
        self,
        scope: str,
        scope_id: str,
        service_id: str,
        service_type: str,
        config: Optional[Dict[str, Any]] = None,
        description: str = "",
        enabled: bool = True,
    ) -> ServiceDef:
        """Install a new service definition.

        Idempotent: if a service with the same id already exists with the
        same service_type, config, and enabled flag, AND has a live-
        connected instance, this is a no-op — the existing ServiceDef is
        returned unchanged.

        Why this matters: PawCode's relay health check re-registers the
        relay service (uninstall + install) every time it thinks the
        relay is disconnected. The uninstall tears down the RelayService
        instance, which closes the live WS to the container — so the
        *next* health check also sees pool=0, triggering another
        re-register. Self-reinforcing churn, observed live. With the
        short-circuit below, a same-config install is silently accepted
        and the existing WS stays alive.
        """
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)

        _RESERVED = {"filestore", "store", "server"}
        if service_id.lower() in _RESERVED:
            raise ValueError(f"Service name '{service_id}' is reserved (builtin FileStore alias)")

        try:
            ServiceFactory.get(service_type)
        except Exception:
            raise ValueError(f"Unknown service type: {service_type}")

        # Idempotent short-circuit: same definition already live → no-op.
        # Compare the load-bearing fields (type + config + enabled); we
        # ignore description and timestamps (not connection-relevant).
        _new_config = config or {}
        with self._data_lock:
            _existing_def = self._definitions.get(sid, {}).get(service_id)
            _existing_live = self._live_instances.get(sid, {}).get(service_id)
        if (_existing_def is not None
                and _existing_def.service_type == service_type
                and _existing_def.enabled == enabled
                and _existing_def.config == _new_config
                and (_existing_live is not None
                     or not enabled)):
            # Optional in-place description update (no reconnect needed).
            if description and _existing_def.description != description:
                with self._data_lock:
                    _existing_def.description = description
                self._save(scope, sid)
            logger.info(
                "Install no-op for %s service '%s' (scope_id=%s) — "
                "same config, already live",
                scope, service_id, sid[:8] if len(sid) > 8 else sid)
            return _existing_def

        svc_def = ServiceDef(
            service_id=service_id,
            service_type=service_type,
            scope=scope,
            scope_id=sid,
            config=_new_config,
            enabled=enabled,
            description=description,
            created_at=time.time(),
        )

        # Prevent resource conflicts across all scopes
        self._check_resource_conflict(
            service_type, _new_config,
            exclude_scope_id=sid, exclude_service_id=service_id,
        )

        with self._data_lock:
            needs_disconnect = service_id in self._live_instances.get(sid, {})
        if needs_disconnect:
            self._disconnect_one(sid, service_id)

        with self._data_lock:
            self._definitions.setdefault(sid, {})[service_id] = svc_def

        self._save(scope, sid)

        if enabled:
            self._connect_one(sid, service_id)

        logger.info("Installed %s service '%s' (scope_id=%s, type=%s)",
                     scope, service_id, sid[:8] if len(sid) > 8 else sid, service_type)
        return svc_def

    def update_config(self, scope: str, scope_id: str, service_id: str,
                      config: Dict[str, Any]) -> None:
        """Update the configuration of a service."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)

        with self._data_lock:
            svc_def = self._definitions.get(sid, {}).get(service_id)
            if not svc_def:
                raise KeyError(f"Service '{service_id}' not found (scope={scope}, id={sid[:8]})")
            needs_disconnect = service_id in self._live_instances.get(sid, {})

        # Check with merged config (existing + new values)
        merged = {**svc_def.config, **config}
        self._check_resource_conflict(
            svc_def.service_type, merged,
            exclude_scope_id=sid, exclude_service_id=service_id,
        )

        if needs_disconnect:
            self._disconnect_one(sid, service_id)

        with self._data_lock:
            svc_def.config.update(config)

        self._save(scope, sid)

        if svc_def.enabled:
            self._connect_one(sid, service_id)

    def rename(self, scope: str, scope_id: str, old_id: str, new_id: str) -> None:
        """Rename a service."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            scope_defs = self._definitions.get(sid, {})
            svc_def = scope_defs.get(old_id)
            if not svc_def:
                raise KeyError(f"Service '{old_id}' not found (scope={scope}, id={sid[:8]})")
            if new_id in scope_defs:
                raise ValueError(f"Service '{new_id}' already exists (scope={scope}, id={sid[:8]})")
            needs_disconnect = old_id in self._live_instances.get(sid, {})
        if needs_disconnect:
            self._disconnect_one(sid, old_id)
        with self._data_lock:
            scope_defs = self._definitions.get(sid, {})
            scope_defs.pop(old_id, None)
            svc_def.service_id = new_id
            scope_defs[new_id] = svc_def
        self._save(scope, sid)
        if svc_def.enabled:
            self._connect_one(sid, new_id)

    def update_description(self, scope: str, scope_id: str, service_id: str,
                           description: str) -> None:
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            svc_def = self._definitions.get(sid, {}).get(service_id)
            if svc_def:
                svc_def.description = description
        self._save(scope, sid)

    def enable(self, scope: str, scope_id: str, service_id: str) -> None:
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            svc_def = self._definitions.get(sid, {}).get(service_id)
            if not svc_def:
                return
            svc_def.enabled = True
        self._save(scope, sid)
        self._connect_one(sid, service_id)

    def disable(self, scope: str, scope_id: str, service_id: str) -> None:
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            svc_def = self._definitions.get(sid, {}).get(service_id)
            if not svc_def:
                return
            svc_def.enabled = False
        self._disconnect_one(sid, service_id)
        self._save(scope, sid)

    def uninstall(self, scope: str, scope_id: str, service_id: str) -> None:
        """Remove a service entirely."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        self._disconnect_one(sid, service_id)
        with self._data_lock:
            self._definitions.get(sid, {}).pop(service_id, None)
        self._save(scope, sid)
        logger.info("Uninstalled %s service '%s' (scope_id=%s)",
                     scope, service_id, sid[:8] if len(sid) > 8 else sid)

    # ---- Queries ----

    def get_definition(self, scope: str, scope_id: str,
                       service_id: str) -> Optional[ServiceDef]:
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            return self._definitions.get(sid, {}).get(service_id)

    def get_all(self, scope: str, scope_id: str) -> Dict[str, ServiceDef]:
        """Get all service definitions for a scope."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            return dict(self._definitions.get(sid, {}))

    def get_live_instance(self, scope: str, scope_id: str,
                          service_id: str) -> Optional[Service]:
        """Get a live (connected) service instance. Lazy-connects if enabled."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            svc = self._live_instances.get(sid, {}).get(service_id)
            if svc is not None:
                return svc
            svc_def = self._definitions.get(sid, {}).get(service_id)
            if not svc_def or not svc_def.enabled:
                return None
        self._connect_one(sid, service_id)
        with self._data_lock:
            return self._live_instances.get(sid, {}).get(service_id)

    def get_compatible(self, scope: str, scope_id: str,
                       service_type: str) -> List[ServiceDef]:
        """Get all services compatible with a given type."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            return [
                sdef for sdef in self._definitions.get(sid, {}).values()
                if sdef.service_type == service_type
            ]

    def is_connected(self, scope: str, scope_id: str, service_id: str) -> bool:
        sid = self._resolve_scope_id(scope, scope_id)
        with self._data_lock:
            svc = self._live_instances.get(sid, {}).get(service_id)
            if svc is None:
                return False
            return svc.is_connected() if hasattr(svc, 'is_connected') else False

    # ---- Lifecycle ----

    def connect_all_enabled(self, scope: str, scope_id: str) -> None:
        """Connect all enabled services for a scope."""
        sid = self._resolve_scope_id(scope, scope_id)
        self._ensure_loaded(scope, scope_id)
        with self._data_lock:
            ids = [
                svc_id for svc_id, sdef in self._definitions.get(sid, {}).items()
                if sdef.enabled
            ]
        for svc_id in ids:
            self._connect_one(sid, svc_id)

    def disconnect_scope(self, scope: str, scope_id: str) -> None:
        """Disconnect all live instances for a scope."""
        sid = self._resolve_scope_id(scope, scope_id)
        with self._data_lock:
            ids = list(self._live_instances.get(sid, {}).keys())
        for svc_id in ids:
            self._disconnect_one(sid, svc_id)

    def cleanup_scope(self, scope: str, scope_id: str) -> None:
        """Disconnect and evict all state for a scope (e.g. deleted conversation)."""
        sid = self._resolve_scope_id(scope, scope_id)
        self.disconnect_scope(scope, scope_id)
        with self._data_lock:
            self._definitions.pop(sid, None)
            self._live_instances.pop(sid, None)
            self._loaded.discard(sid)

    def _disconnect_all_scopes(self) -> None:
        """Disconnect everything (for shutdown/reset)."""
        with self._data_lock:
            all_sids = list(self._live_instances.keys())
        for sid in all_sids:
            with self._data_lock:
                ids = list(self._live_instances.get(sid, {}).keys())
            for svc_id in ids:
                self._disconnect_one(sid, svc_id)

    def _connect_one(self, scope_id: str, service_id: str) -> None:
        """Instantiate and connect a single service."""
        with self._data_lock:
            svc_def = self._definitions.get(scope_id, {}).get(service_id)
            if not svc_def:
                return
            if service_id in self._live_instances.get(scope_id, {}):
                return

        try:
            from tasks import _register_all_services
            _register_all_services()
            svc_class = ServiceFactory.get(svc_def.service_type)
            from core.expression import LazyResolveDict
            lazy_config = LazyResolveDict(svc_def.config)
            lazy_config["_service_id"] = service_id
            svc_instance = svc_class(lazy_config)
            svc_instance.connect()
            with self._data_lock:
                self._live_instances.setdefault(scope_id, {})[service_id] = svc_instance
            logger.info("Service '%s' connected (scope_id=%s)",
                         service_id, scope_id[:8] if len(scope_id) > 8 else scope_id)
        except Exception as e:
            logger.error("Failed to connect service '%s' (scope_id=%s): %s",
                         service_id, scope_id[:8] if len(scope_id) > 8 else scope_id, e)

    def _disconnect_one(self, scope_id: str, service_id: str) -> None:
        """Disconnect and remove a live service instance."""
        with self._data_lock:
            scope_live = self._live_instances.get(scope_id, {})
            svc = scope_live.pop(service_id, None)
        if svc is not None:
            try:
                svc.disconnect()
                logger.info("Service '%s' disconnected (scope_id=%s)",
                             service_id, scope_id[:8] if len(scope_id) > 8 else scope_id)
            except Exception as e:
                logger.warning("Error disconnecting service '%s' (scope_id=%s): %s",
                               service_id, scope_id[:8] if len(scope_id) > 8 else scope_id, e)

    # ---- Heartbeat ----

    def start_heartbeat(self, interval: int = 30):
        """Start background heartbeat thread to monitor relay services."""
        if self._heartbeat_running:
            return
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, args=(interval,),
            daemon=True, name="service-heartbeat",
        )
        self._heartbeat_thread.start()
        logger.info("Service heartbeat started (interval=%ds)", interval)

    def stop_heartbeat(self):
        self._heartbeat_running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

    def _heartbeat_loop(self, interval: int):
        while self._heartbeat_running:
            try:
                self._heartbeat_check()
            except Exception as e:
                logger.debug("Heartbeat check error: %s", e)
            time.sleep(interval)

    def _heartbeat_check(self):
        """Check all enabled relay services for connectivity."""
        with self._data_lock:
            to_check = []
            for scope_id, scope_defs in self._definitions.items():
                for svc_id, sdef in scope_defs.items():
                    if sdef.enabled and sdef.service_type in _HEARTBEAT_TYPES:
                        live = self._live_instances.get(scope_id, {}).get(svc_id)
                        if live and hasattr(live, 'ping'):
                            to_check.append((sdef.scope, scope_id, svc_id, live))

        for scope, scope_id, svc_id, live in to_check:
            try:
                ok = live.ping()
            except Exception:
                ok = False

            key = (scope_id, svc_id)
            if ok:
                self._failure_counts.pop(key, None)
            else:
                count = self._failure_counts.get(key, 0) + 1
                self._failure_counts[key] = count
                if count >= 3:
                    logger.warning("Service '%s' (scope_id=%s) failed %d checks — auto-disabling",
                                   svc_id, scope_id[:8], count)
                    self.disable(scope, scope_id, svc_id)
                    self._failure_counts.pop(key, None)
                    self._notify_service_down(scope_id, svc_id)

    def _notify_service_down(self, scope_id: str, service_id: str):
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            for conv_id in bus.active_conversations():
                bus.publish_event(conv_id, "notification", {
                    "message": f"Service '{service_id}' disconnected — relay is no longer reachable",
                    "urgency": "high",
                    "service_id": service_id,
                })
        except Exception:
            pass

    # ---- Sensitive field encryption ----

    @staticmethod
    def _sensitive_keys(service_type: str) -> set:
        """Return the set of config keys marked sensitive in the service schema."""
        try:
            svc_cls = ServiceFactory.get(service_type)
            schema = svc_cls.get_parameter_schema(svc_cls)
            return {k for k, v in schema.items() if v.get("sensitive")}
        except Exception:
            return set()

    @staticmethod
    def _encrypt_config(config: dict, sensitive_keys: set) -> dict:
        """Return a copy of config with sensitive values encrypted."""
        if not sensitive_keys:
            return config
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        out = dict(config)
        for k in sensitive_keys:
            v = out.get(k)
            if isinstance(v, str) and v and not v.startswith("enc:") and not v.startswith("${"):
                out[k] = sm.encrypt(v)
        return out

    @staticmethod
    def _decrypt_config(config: dict, sensitive_keys: set) -> dict:
        """Return a copy of config with sensitive values decrypted."""
        if not sensitive_keys:
            return config
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        out = dict(config)
        for k in sensitive_keys:
            v = out.get(k)
            if isinstance(v, str) and v.startswith("enc:"):
                try:
                    out[k] = sm.decrypt(v)
                except Exception:
                    pass  # leave encrypted if key changed
        return out

    # ---- Persistence ----

    def _load(self, scope: str, scope_id: str) -> None:
        """Load service definitions from the appropriate backend."""
        try:
            if scope == SCOPE_GLOBAL:
                self._load_dir(scope_id, _GLOBAL_SERVICES_DIR, scope)
            elif scope == SCOPE_USER:
                svc_dir = _USER_SERVICES_DIR / scope_id
                self._load_dir(scope_id, svc_dir, scope)
            elif scope == SCOPE_CONV:
                self._load_conv(scope_id)
        except Exception as e:
            self._load_failed.add(scope_id)
            logger.error(
                "CRITICAL: Failed to load %s services (id=%s): %s — "
                "registry is READ-ONLY for this scope until restart",
                scope, scope_id[:8] if len(scope_id) > 8 else scope_id, e)

    def _load_dir(self, scope_id: str, svc_dir: Path, scope: str) -> None:
        """Load definitions from a directory (1 JSON file per service)."""
        if not svc_dir.exists():
            return
        defs = {}
        for f in svc_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("service_id", f.stem)
                data["service_id"] = sid
                data["scope"] = scope
                data["scope_id"] = scope_id
                stype = data.get("service_type", "")
                sk = self._sensitive_keys(stype)
                if sk and "config" in data:
                    data["config"] = self._decrypt_config(data["config"], sk)
                defs[sid] = ServiceDef.from_dict(data)
            except Exception as e:
                logger.warning("Failed to load service from %s: %s", f, e)
        self._definitions[scope_id] = defs
        logger.info("Loaded %d %s service(s) (id=%s)", len(defs), scope,
                     scope_id[:8] if len(scope_id) > 8 else scope_id)

    def _load_conv(self, scope_id: str) -> None:
        """Load definitions from ConversationStore extras."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        raw = store.get_extra(scope_id, CONV_EXTRAS_KEY) or {}
        defs = {}
        for sid, data in raw.items():
            data["service_id"] = sid
            data["scope"] = SCOPE_CONV
            data["scope_id"] = scope_id
            defs[sid] = ServiceDef.from_dict(data)
        self._definitions[scope_id] = defs
        if defs:
            logger.info("Loaded %d conv service(s) for conv '%s'", len(defs), scope_id[:8])

    def _save(self, scope: str, scope_id: str) -> None:
        """Save service definitions to the appropriate backend."""
        if scope_id in self._load_failed:
            logger.warning(
                "REFUSING to save %s services (id=%s) — initial load failed.",
                scope, scope_id[:8] if len(scope_id) > 8 else scope_id)
            return
        if scope == SCOPE_GLOBAL:
            self._save_dir(scope_id, _GLOBAL_SERVICES_DIR)
        elif scope == SCOPE_USER:
            svc_dir = _USER_SERVICES_DIR / scope_id
            self._save_dir(scope_id, svc_dir)
        elif scope == SCOPE_CONV:
            self._save_conv(scope_id)

    def _save_dir(self, scope_id: str, svc_dir: Path) -> None:
        """Save each service as an individual JSON file (atomic writes)."""
        svc_dir.mkdir(parents=True, exist_ok=True)
        with self._data_lock:
            current_defs = dict(self._definitions.get(scope_id, {}))

        # Write/update each service file
        for sid, sdef in current_defs.items():
            d = sdef.to_dict()
            sk = self._sensitive_keys(sdef.service_type)
            if sk:
                d["config"] = self._encrypt_config(d.get("config", {}), sk)
            safe_name = sid.replace("/", "_").replace("\\", "_")
            filepath = svc_dir / f"{safe_name}.json"
            tmp_path = filepath.with_suffix(".tmp")
            try:
                tmp_path.write_text(
                    json.dumps(d, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(filepath)
            except Exception as e:
                logger.error("Failed to save service %s to %s: %s", sid, filepath, e)
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

        # Remove files for services that no longer exist
        for f in svc_dir.glob("*.json"):
            if f.stem not in current_defs and f.stem.replace("_", "/") not in current_defs:
                try:
                    f.unlink()
                    logger.info("Removed stale service file: %s", f)
                except Exception:
                    pass

    def _save_conv(self, scope_id: str) -> None:
        """Save to ConversationStore extras."""
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            with self._data_lock:
                conv_defs = self._definitions.get(scope_id, {})
                data = {
                    sid: sdef.to_dict()
                    for sid, sdef in conv_defs.items()
                }
            store.set_extra(scope_id, CONV_EXTRAS_KEY, data)
        except Exception as e:
            logger.error("Failed to save conv services for '%s': %s", scope_id[:8], e)


    # ---- Resolution (scope chain: conv > user > global) ----

    def _scope_chain(self, *, user_id: str = "", conv_id: str = ""):
        """Yield (scope, scope_id) in resolution order: conv > user > global."""
        if conv_id:
            yield SCOPE_CONV, conv_id
        if user_id:
            yield SCOPE_USER, user_id
        yield SCOPE_GLOBAL, ""

    def resolve(self, service_id: str, *,
                user_id: str = "", conv_id: str = "") -> Optional[Service]:
        """Walk conv > user > global and return the first live instance found."""
        for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
            svc = self.get_live_instance(scope, sid, service_id)
            if svc is not None:
                return svc
        return None

    def resolve_definition(self, service_id: str, *,
                           user_id: str = "", conv_id: str = "") -> Optional[ServiceDef]:
        """Walk conv > user > global and return the first definition found."""
        for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
            d = self.get_definition(scope, sid, service_id)
            if d is not None:
                return d
        return None

    def resolve_by_type(self, service_type: str, *,
                        user_id: str = "", conv_id: str = "",
                        enabled_only: bool = True) -> List[ServiceDef]:
        """Get all services matching a type, ordered conv > user > global.

        If the same service_id exists in multiple scopes, the most specific wins.
        """
        result = []
        seen: set = set()
        for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
            self._ensure_loaded(scope, sid)
            rsid = self._resolve_scope_id(scope, sid)
            with self._data_lock:
                for sdef in self._definitions.get(rsid, {}).values():
                    if sdef.service_id in seen:
                        continue
                    if sdef.service_type == service_type:
                        if enabled_only and not sdef.enabled:
                            continue
                        result.append(sdef)
                        seen.add(sdef.service_id)
        return result

    def resolve_all(self, *, user_id: str = "", conv_id: str = "",
                    enabled_only: bool = False) -> Dict[str, ServiceDef]:
        """Get all definitions across all scopes, most specific wins."""
        result: Dict[str, ServiceDef] = {}
        # Walk reverse (global first) so more specific scopes override
        for scope, sid in reversed(list(
                self._scope_chain(user_id=user_id, conv_id=conv_id))):
            self._ensure_loaded(scope, sid)
            rsid = self._resolve_scope_id(scope, sid)
            with self._data_lock:
                for svc_id, sdef in self._definitions.get(rsid, {}).items():
                    if enabled_only and not sdef.enabled:
                        continue
                    result[svc_id] = sdef
        return result
