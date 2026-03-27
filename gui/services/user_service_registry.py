"""Registry for user-scoped service instances.

User services are per-user service instances (DB connections, LLM clients, etc.)
that can be managed from the chat UI and used as forwarding targets in deployed flows.

Each user's services are isolated — user A cannot see or use user B's services.

Persistence: config/user_services/{user_id}.json (one file per user)
Runtime instances: kept alive in-process, connected on enable.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

from core import ServiceFactory, Service
from core.expression import resolve_expression

logger = logging.getLogger(__name__)

USER_SERVICES_DIR = Path("config/user_services")


@dataclass
class UserServiceDef:
    """Definition of a user-scoped service."""

    service_id: str
    service_type: str
    user_id: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserServiceDef":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class UserServiceRegistry:
    """Thread-safe singleton managing user-scoped service definitions and live instances."""

    _instance: Optional["UserServiceRegistry"] = None
    _lock = threading.Lock()

    # Plan C: service types that support heartbeat (have a ping() method)
    _HEARTBEAT_TYPES = frozenset({
        "relay",
        "remoteExecutor",
    })

    def __init__(self):
        # _definitions[user_id][service_id] = UserServiceDef
        self._definitions: Dict[str, Dict[str, UserServiceDef]] = {}
        # _live_instances[user_id][service_id] = Service
        self._live_instances: Dict[str, Dict[str, Service]] = {}
        self._data_lock = threading.Lock()
        self._loaded_users: set = set()
        # Plan C: heartbeat
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False
        # {(user_id, service_id): failure_count}
        self._failure_counts: Dict[tuple, int] = {}

    @classmethod
    def get_instance(cls) -> "UserServiceRegistry":
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
                inst.disconnect_all_users()
            cls._instance = None

    def _ensure_user_loaded(self, user_id: str):
        if user_id not in self._loaded_users:
            with self._data_lock:
                if user_id not in self._loaded_users:
                    self._load_user_from_disk(user_id)
                    self._loaded_users.add(user_id)

    # ---- CRUD ----

    def install(
        self,
        user_id: str,
        service_id: str,
        service_type: str,
        config: Optional[Dict[str, Any]] = None,
        description: str = "",
        enabled: bool = True,
    ) -> UserServiceDef:
        """Install a new user service definition."""
        self._ensure_user_loaded(user_id)

        # Reserved names (used by fs:// URL routing)
        _RESERVED = {"filestore", "store", "server"}
        if service_id.lower() in _RESERVED:
            raise ValueError(f"Service name '{service_id}' is reserved (builtin FileStore alias)")

        # Validate service type exists
        try:
            ServiceFactory.get(service_type)
        except Exception:
            raise ValueError(f"Unknown service type: {service_type}")

        svc_def = UserServiceDef(
            service_id=service_id,
            service_type=service_type,
            user_id=user_id,
            config=config or {},
            enabled=enabled,
            description=description,
            created_at=time.time(),
        )

        # Disconnect old instance if replacing (before acquiring lock to avoid deadlock)
        with self._data_lock:
            needs_disconnect = service_id in self._live_instances.get(user_id, {})
        if needs_disconnect:
            self._disconnect_one(user_id, service_id)

        with self._data_lock:
            self._definitions.setdefault(user_id, {})[service_id] = svc_def

        self._save_user_to_disk(user_id)

        if enabled:
            self._connect_one(user_id, service_id)

        logger.info("Installed user service '%s' for user '%s' (type=%s)",
                     service_id, user_id, service_type)
        return svc_def

    def update_config(self, user_id: str, service_id: str, config: Dict[str, Any]) -> None:
        """Update the configuration of a user service."""
        self._ensure_user_loaded(user_id)

        with self._data_lock:
            user_defs = self._definitions.get(user_id, {})
            svc_def = user_defs.get(service_id)
            if not svc_def:
                raise KeyError(f"User service '{service_id}' not found for user '{user_id}'")
            needs_disconnect = service_id in self._live_instances.get(user_id, {})

        if needs_disconnect:
            self._disconnect_one(user_id, service_id)

        with self._data_lock:
            svc_def.config = config

        self._save_user_to_disk(user_id)

        if svc_def.enabled:
            self._connect_one(user_id, service_id)

    def rename(self, user_id: str, old_id: str, new_id: str) -> None:
        """Rename a user service."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            user_defs = self._definitions.get(user_id, {})
            svc_def = user_defs.get(old_id)
            if not svc_def:
                raise KeyError(f"User service '{old_id}' not found for user '{user_id}'")
            if new_id in user_defs:
                raise ValueError(f"User service '{new_id}' already exists for user '{user_id}'")
            if old_id in self._live_instances.get(user_id, {}):
                self._disconnect_one(user_id, old_id)
            user_defs.pop(old_id)
            svc_def.service_id = new_id
            user_defs[new_id] = svc_def
        self._save_user_to_disk(user_id)
        if svc_def.enabled:
            self._connect_one(user_id, new_id)

    def update_description(self, user_id: str, service_id: str, description: str) -> None:
        """Update description."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            user_defs = self._definitions.get(user_id, {})
            svc_def = user_defs.get(service_id)
            if svc_def:
                svc_def.description = description
        self._save_user_to_disk(user_id)

    def enable(self, user_id: str, service_id: str) -> None:
        """Enable a user service (connect it)."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            svc_def = self._definitions.get(user_id, {}).get(service_id)
            if not svc_def:
                return
            svc_def.enabled = True
        self._save_user_to_disk(user_id)
        self._connect_one(user_id, service_id)

    def disable(self, user_id: str, service_id: str) -> None:
        """Disable a user service (disconnect it)."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            svc_def = self._definitions.get(user_id, {}).get(service_id)
            if not svc_def:
                return
            svc_def.enabled = False
        self._disconnect_one(user_id, service_id)
        self._save_user_to_disk(user_id)

    def uninstall(self, user_id: str, service_id: str) -> None:
        """Remove a user service entirely."""
        self._ensure_user_loaded(user_id)
        self._disconnect_one(user_id, service_id)
        with self._data_lock:
            user_defs = self._definitions.get(user_id, {})
            user_defs.pop(service_id, None)
        self._save_user_to_disk(user_id)
        logger.info("Uninstalled user service '%s' for user '%s'", service_id, user_id)

    # ---- Queries ----

    def get_definition(self, user_id: str, service_id: str) -> Optional[UserServiceDef]:
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            return self._definitions.get(user_id, {}).get(service_id)

    def get_all_for_user(self, user_id: str) -> Dict[str, UserServiceDef]:
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            return dict(self._definitions.get(user_id, {}))

    def get_live_instance(self, user_id: str, service_id: str) -> Optional[Service]:
        """Get a live (connected) service instance for forwarding.

        Lazy-connects the service on first access if defined and enabled.
        """
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            svc = self._live_instances.get(user_id, {}).get(service_id)
            if svc is not None:
                return svc
            svc_def = self._definitions.get(user_id, {}).get(service_id)
            if not svc_def or not svc_def.enabled:
                return None
        # Connect outside the lock
        self._connect_one(user_id, service_id)
        with self._data_lock:
            return self._live_instances.get(user_id, {}).get(service_id)

    def get_compatible(self, service_type: str, user_id: str) -> List[UserServiceDef]:
        """Get all user services compatible with a given type for a specific user."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            return [
                svc_def for svc_def in self._definitions.get(user_id, {}).values()
                if svc_def.service_type == service_type
            ]

    def is_connected(self, user_id: str, service_id: str) -> bool:
        """Check if a user service is currently connected."""
        with self._data_lock:
            svc = self._live_instances.get(user_id, {}).get(service_id)
            if svc is None:
                return False
            return svc.is_connected() if hasattr(svc, 'is_connected') else False

    # ---- Lifecycle ----

    def connect_all_enabled(self, user_id: str) -> None:
        """Connect all enabled services for a user."""
        self._ensure_user_loaded(user_id)
        with self._data_lock:
            ids = [
                sid for sid, sdef in self._definitions.get(user_id, {}).items()
                if sdef.enabled
            ]
        for sid in ids:
            self._connect_one(user_id, sid)

    def disconnect_all(self, user_id: str) -> None:
        """Disconnect all live service instances for a user."""
        with self._data_lock:
            ids = list(self._live_instances.get(user_id, {}).keys())
        for sid in ids:
            self._disconnect_one(user_id, sid)

    def disconnect_all_users(self) -> None:
        """Disconnect all live service instances for all users."""
        with self._data_lock:
            all_users = list(self._live_instances.keys())
        for uid in all_users:
            self.disconnect_all(uid)

    def _connect_one(self, user_id: str, service_id: str) -> None:
        """Instantiate and connect a single user service."""
        with self._data_lock:
            svc_def = self._definitions.get(user_id, {}).get(service_id)
            if not svc_def:
                return
            if service_id in self._live_instances.get(user_id, {}):
                return

        try:
            from tasks import _register_all_services
            _register_all_services()
            svc_class = ServiceFactory.get(svc_def.service_type)
            # Wrap config with lazy expression resolution
            from core.expression import LazyResolveDict
            lazy_config = LazyResolveDict(svc_def.config)
            lazy_config["_service_id"] = service_id
            svc_instance = svc_class(lazy_config)
            svc_instance.connect()
            with self._data_lock:
                self._live_instances.setdefault(user_id, {})[service_id] = svc_instance
            logger.info("User service '%s' connected for user '%s'", service_id, user_id)
        except Exception as e:
            logger.error("Failed to connect user service '%s' for user '%s': %s",
                         service_id, user_id, e)

    def _disconnect_one(self, user_id: str, service_id: str) -> None:
        """Disconnect and remove a live service instance."""
        with self._data_lock:
            user_live = self._live_instances.get(user_id, {})
            svc = user_live.pop(service_id, None)
        if svc is not None:
            try:
                svc.disconnect()
                logger.info("User service '%s' disconnected for user '%s'", service_id, user_id)
            except Exception as e:
                logger.warning("Error disconnecting user service '%s' for user '%s': %s",
                               service_id, user_id, e)

    # ---- Heartbeat (Plan C) ----

    def start_heartbeat(self, interval: int = 30):
        """Start background heartbeat thread to monitor relay services."""
        if self._heartbeat_running:
            return
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, args=(interval,),
            daemon=True, name="user-service-heartbeat",
        )
        self._heartbeat_thread.start()
        logger.info("Service heartbeat started (interval=%ds)", interval)

    def stop_heartbeat(self):
        """Stop the heartbeat thread."""
        self._heartbeat_running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

    def _heartbeat_loop(self, interval: int):
        """Periodically check relay services for connectivity."""
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
            for user_id, user_defs in self._definitions.items():
                for svc_id, sdef in user_defs.items():
                    if sdef.enabled and sdef.service_type in self._HEARTBEAT_TYPES:
                        live = self._live_instances.get(user_id, {}).get(svc_id)
                        if live and hasattr(live, 'ping'):
                            to_check.append((user_id, svc_id, live))

        for user_id, svc_id, live in to_check:
            try:
                ok = live.ping()
            except Exception:
                ok = False

            key = (user_id, svc_id)
            if ok:
                self._failure_counts.pop(key, None)
            else:
                count = self._failure_counts.get(key, 0) + 1
                self._failure_counts[key] = count
                if count >= 3:
                    logger.warning("Service '%s' for user '%s' failed %d checks — auto-disabling",
                                   svc_id, user_id, count)
                    self.disable(user_id, svc_id)
                    self._failure_counts.pop(key, None)
                    self._notify_service_down(user_id, svc_id)

    def _notify_service_down(self, user_id: str, service_id: str):
        """Notify user via SSE that a service went down."""
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

    # ---- Persistence ----

    def _load_user_from_disk(self, user_id: str) -> None:
        filepath = USER_SERVICES_DIR / f"{user_id}.json"
        if not filepath.exists():
            return
        try:
            raw = json.loads(filepath.read_text(encoding="utf-8"))
            user_defs = {}
            for sid, data in raw.items():
                data["service_id"] = sid
                data["user_id"] = user_id
                user_defs[sid] = UserServiceDef.from_dict(data)
            self._definitions[user_id] = user_defs
            logger.info("Loaded %d user service(s) for user '%s'", len(user_defs), user_id)
        except Exception as e:
            logger.warning("Failed to load user services for '%s': %s", user_id, e)

    def _save_user_to_disk(self, user_id: str) -> None:
        USER_SERVICES_DIR.mkdir(parents=True, exist_ok=True)
        with self._data_lock:
            user_defs = self._definitions.get(user_id, {})
            data = {
                sid: sdef.to_dict()
                for sid, sdef in user_defs.items()
            }
        filepath = USER_SERVICES_DIR / f"{user_id}.json"
        try:
            filepath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save user services for '%s': %s", user_id, e)
