"""Registry for global (shared) service instances.

Global services are service instances defined once and shared across multiple
flows. Instead of each flow instantiating its own service, it can "forward"
to a global service instance.

Persistence: config/global_services.json
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

logger = logging.getLogger(__name__)

GLOBAL_SERVICES_FILE = Path("config/global_services.json")




@dataclass
class GlobalServiceDef:
    """Definition of a global service."""

    service_id: str
    service_type: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalServiceDef":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class GlobalServiceRegistry:
    """Thread-safe singleton managing global service definitions and live instances."""

    _instance: Optional["GlobalServiceRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._definitions: Dict[str, GlobalServiceDef] = {}
        self._live_instances: Dict[str, Service] = {}
        self._data_lock = threading.RLock()
        self._loaded = False
        self._load_failed = False  # if True, refuse to save (prevents data loss)

    @classmethod
    def get_instance(cls) -> "GlobalServiceRegistry":
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
                inst.disconnect_all()
            cls._instance = None

    def _ensure_loaded(self):
        if not self._loaded:
            with self._data_lock:
                if not self._loaded:
                    self._load_from_disk()
                    self._loaded = True

    # ---- CRUD ----

    def install(
        self,
        service_id: str,
        service_type: str,
        config: Optional[Dict[str, Any]] = None,
        description: str = "",
        enabled: bool = True,
    ) -> GlobalServiceDef:
        """Install a new global service definition."""
        self._ensure_loaded()

        # Reserved names (used by fs:// URL routing)
        _RESERVED = {"filestore", "store", "server"}
        if service_id.lower() in _RESERVED:
            raise ValueError(f"Service name '{service_id}' is reserved (builtin FileStore alias)")

        # Validate service type exists
        try:
            ServiceFactory.get(service_type)
        except Exception:
            raise ValueError(f"Unknown service type: {service_type}")

        svc_def = GlobalServiceDef(
            service_id=service_id,
            service_type=service_type,
            config=config or {},
            enabled=enabled,
            description=description,
            created_at=time.time(),
        )

        with self._data_lock:
            # Disconnect old instance if replacing
            if service_id in self._live_instances:
                self._disconnect_one(service_id)
            self._definitions[service_id] = svc_def

        self._save_to_disk()

        if enabled:
            self._connect_one(service_id)

        logger.info("Installed global service '%s' (type=%s)", service_id, service_type)
        return svc_def

    def update_config(self, service_id: str, config: Dict[str, Any]) -> None:
        """Update the configuration of a global service."""
        self._ensure_loaded()

        with self._data_lock:
            svc_def = self._definitions.get(service_id)
            if not svc_def:
                raise KeyError(f"Global service '{service_id}' not found")
            # Disconnect if live (config changed)
            if service_id in self._live_instances:
                self._disconnect_one(service_id)
            # Merge: new config overwrites, but preserve keys not in the new config
            # (e.g., oauth tokens set by login flow that aren't in the edit form)
            svc_def.config.update(config)

        self._save_to_disk()

        if svc_def.enabled:
            self._connect_one(service_id)

    def rename(self, old_id: str, new_id: str) -> None:
        """Rename a global service."""
        self._ensure_loaded()
        with self._data_lock:
            svc_def = self._definitions.get(old_id)
            if not svc_def:
                raise KeyError(f"Global service '{old_id}' not found")
            if new_id in self._definitions:
                raise ValueError(f"Global service '{new_id}' already exists")
            # Disconnect old
            if old_id in self._live_instances:
                self._disconnect_one(old_id)
            # Move definition
            self._definitions.pop(old_id)
            svc_def.service_id = new_id
            self._definitions[new_id] = svc_def
        self._save_to_disk()
        if svc_def.enabled:
            self._connect_one(new_id)

    def update_description(self, service_id: str, description: str) -> None:
        """Update description."""
        self._ensure_loaded()
        with self._data_lock:
            svc_def = self._definitions.get(service_id)
            if svc_def:
                svc_def.description = description
        self._save_to_disk()

    def enable(self, service_id: str) -> None:
        """Enable a global service (connect it)."""
        self._ensure_loaded()
        with self._data_lock:
            svc_def = self._definitions.get(service_id)
            if not svc_def:
                return
            svc_def.enabled = True
        self._save_to_disk()
        self._connect_one(service_id)

    def disable(self, service_id: str) -> None:
        """Disable a global service (disconnect it)."""
        self._ensure_loaded()
        with self._data_lock:
            svc_def = self._definitions.get(service_id)
            if not svc_def:
                return
            svc_def.enabled = False
        self._disconnect_one(service_id)
        self._save_to_disk()

    def uninstall(self, service_id: str) -> None:
        """Remove a global service entirely."""
        self._ensure_loaded()
        self._disconnect_one(service_id)
        with self._data_lock:
            self._definitions.pop(service_id, None)
        self._save_to_disk()
        logger.info("Uninstalled global service '%s'", service_id)

    # ---- Queries ----

    def get_definition(self, service_id: str) -> Optional[GlobalServiceDef]:
        self._ensure_loaded()
        with self._data_lock:
            return self._definitions.get(service_id)

    def get_all_definitions(self) -> Dict[str, GlobalServiceDef]:
        self._ensure_loaded()
        with self._data_lock:
            return dict(self._definitions)

    def get_live_instance(self, service_id: str) -> Optional[Service]:
        """Get a live (connected) service instance for forwarding.

        Lazy-connects the service on first access if it is defined and enabled.
        """
        self._ensure_loaded()
        with self._data_lock:
            svc = self._live_instances.get(service_id)
            if svc is not None:
                return svc
            # Check if defined + enabled → lazy connect
            svc_def = self._definitions.get(service_id)
            if not svc_def or not svc_def.enabled:
                return None
        # Connect outside the lock
        self._connect_one(service_id)
        with self._data_lock:
            return self._live_instances.get(service_id)

    def get_compatible(self, service_type: str) -> List[GlobalServiceDef]:
        """Get all global services compatible with a given type."""
        self._ensure_loaded()
        with self._data_lock:
            return [
                svc_def for svc_def in self._definitions.values()
                if svc_def.service_type == service_type
            ]

    def is_connected(self, service_id: str) -> bool:
        """Check if a global service is currently connected."""
        with self._data_lock:
            svc = self._live_instances.get(service_id)
            if svc is None:
                return False
            return svc.is_connected() if hasattr(svc, 'is_connected') else False

    # ---- Lifecycle ----

    def connect_all_enabled(self) -> None:
        """Connect all enabled global services. Call at app startup."""
        self._ensure_loaded()
        with self._data_lock:
            ids = [
                sid for sid, sdef in self._definitions.items()
                if sdef.enabled
            ]
        for sid in ids:
            self._connect_one(sid)

    def disconnect_all(self) -> None:
        """Disconnect all live service instances."""
        with self._data_lock:
            ids = list(self._live_instances.keys())
        for sid in ids:
            self._disconnect_one(sid)

    def _connect_one(self, service_id: str) -> None:
        """Instantiate and connect a single global service."""
        with self._data_lock:
            svc_def = self._definitions.get(service_id)
            if not svc_def:
                return
            # Already connected?
            if service_id in self._live_instances:
                return

        try:
            from tasks import _register_all_services
            _register_all_services()
            svc_class = ServiceFactory.get(svc_def.service_type)
            # Wrap config with lazy expression resolution —
            # expressions are resolved at each .get() call, not cached.
            from core.expression import LazyResolveDict
            lazy_config = LazyResolveDict(svc_def.config)
            svc_instance = svc_class(lazy_config)
            svc_instance.connect()
            with self._data_lock:
                self._live_instances[service_id] = svc_instance
            logger.info("Global service '%s' connected", service_id)
        except Exception as e:
            logger.error("Failed to connect global service '%s': %s", service_id, e)

    def _disconnect_one(self, service_id: str) -> None:
        """Disconnect and remove a live service instance."""
        with self._data_lock:
            svc = self._live_instances.pop(service_id, None)
        if svc is not None:
            try:
                svc.disconnect()
                logger.info("Global service '%s' disconnected", service_id)
            except Exception as e:
                logger.warning("Error disconnecting global service '%s': %s", service_id, e)

    # ---- Persistence ----

    def _load_from_disk(self) -> None:
        if not GLOBAL_SERVICES_FILE.exists():
            return
        try:
            raw = json.loads(GLOBAL_SERVICES_FILE.read_text(encoding="utf-8"))
            for sid, data in raw.items():
                data["service_id"] = sid
                self._definitions[sid] = GlobalServiceDef.from_dict(data)
            logger.info("Loaded %d global service(s) from disk", len(self._definitions))
        except Exception as e:
            # CRITICAL: mark load as failed — _save_to_disk MUST NOT overwrite
            # the file with empty/partial data. The file on disk is the source
            # of truth until a successful load.
            self._load_failed = True
            logger.error(
                "CRITICAL: Failed to load global services from %s: %s — "
                "registry is READ-ONLY until restart with valid file",
                GLOBAL_SERVICES_FILE, e)

    def _save_to_disk(self) -> None:
        # NEVER overwrite the file if the initial load failed — the file on
        # disk has the real data, we just couldn't parse it.
        if self._load_failed:
            logger.warning(
                "REFUSING to save global services — initial load failed. "
                "Fix %s and restart.", GLOBAL_SERVICES_FILE)
            return
        GLOBAL_SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self._data_lock:
            data = {
                sid: sdef.to_dict()
                for sid, sdef in self._definitions.items()
            }
        # Atomic write: write to .tmp then rename (prevents corruption on crash)
        tmp_path = GLOBAL_SERVICES_FILE.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(GLOBAL_SERVICES_FILE)
        except Exception as e:
            logger.error("Failed to save global services: %s", e)
            # Clean up tmp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
