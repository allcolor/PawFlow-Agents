"""Unified service registry for all scopes (global, user, conversation).

A single class that manages service definitions and live instances across
all three scopes. The scope determines storage and keying:

    global  — shared across all users. Persisted in data/runtime/services/global/{id}.json
    user    — per-user. Persisted in data/runtime/services/users/{user_id}/{id}.json
    conv    — per-conversation. Persisted in ConversationStore extras.

All scopes share the same CRUD interface — only the scope_id changes.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from core import Service, ServiceFactory

from core._service_defs import (  # noqa: F401 -- re-exported for back-compat
    CONV_EXTRAS_KEY,
    ResourceConflictError,
    SCOPE_CONV,
    SCOPE_GLOBAL,
    SCOPE_USER,
    VALID_SCOPES,
    ServiceDef,
    _GLOBAL_SCOPE_ID,
    _HEARTBEAT_TYPES,
    _UNIQUE_RESOURCE_DEFAULTS,
    _UNIQUE_RESOURCE_KEYS,
    _global_services_dir,
    _package_runtime_dedupe_key,
    _parent_conversation_id,
    _user_services_dir,
)
from core._service_registry_io import _ServiceRegistryIOMixin

logger = logging.getLogger(__name__)


class ServiceRegistry(_ServiceRegistryIOMixin):
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
        if sid in self._loaded:
            return
        just_loaded = False
        with self._data_lock:
            if sid not in self._loaded:
                self._load(scope, sid)
                self._loaded.add(sid)
                just_loaded = True
        if just_loaded:
            self._connect_managed_relays(sid)

    def _connect_managed_relays(self, scope_id: str) -> None:
        with self._data_lock:
            relay_ids = [
                service_id
                for service_id, sdef in self._definitions.get(scope_id, {}).items()
                if sdef.enabled
                and sdef.service_type == "relay"
                and bool((sdef.config or {}).get("server_managed"))
            ]
        for service_id in relay_ids:
            self._connect_one(scope_id, service_id)

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

    def _check_relay_conflict(
        self, service_id: str, scope: str, scope_id: str,
        config: Dict[str, Any],
        exclude_scope_id: str = "", exclude_service_id: str = "",
    ) -> None:
        """Relays own global listener routes and managed workspace scopes."""
        managed = bool((config or {}).get("server_managed"))
        target_scope = str((config or {}).get("server_scope") or scope or "user")
        target_scope_id = str((config or {}).get("server_scope_id") or scope_id or "")
        target_kind = str((config or {}).get("server_kind") or "workspace")
        with self._data_lock:
            for sid, scope_defs in self._definitions.items():
                for svc_id, sdef in scope_defs.items():
                    if sid == exclude_scope_id and svc_id == exclude_service_id:
                        continue
                    if sdef.service_type != "relay":
                        continue
                    if svc_id == service_id:
                        raise ResourceConflictError(
                            f"Relay service id '{service_id}' already exists "
                            f"in scope={sdef.scope}. Relay websocket routes are "
                            "global; use a unique relay name per server.")
                    other_cfg = sdef.config or {}
                    if not (managed and other_cfg.get("server_managed")):
                        continue
                    other_scope = str(other_cfg.get("server_scope") or sdef.scope or "user")
                    other_scope_id = str(other_cfg.get("server_scope_id") or sdef.scope_id or "")
                    other_kind = str(other_cfg.get("server_kind") or "workspace")
                    if (other_scope, other_scope_id, other_kind) == (
                            target_scope, target_scope_id, target_kind):
                        label = "conversation" if target_scope == "conv" else target_scope
                        raise ResourceConflictError(
                            f"Managed server relay for {label} scope "
                            f"'{target_scope_id or 'global'}' already exists "
                            f"as service '{svc_id}'. Only one managed server "
                            "relay is allowed per scope.")

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
        if service_type == "relay":
            self._check_relay_conflict(
                service_id, scope, sid, _new_config,
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

    def iter_all_scopes(self, conv_pairs=None):
        """Admin enumeration of every populated service scope (read-only).

        Returns ``[(scope, scope_id, owner_id, conv_id), ...]``. Global is
        always present; user scopes come from the on-disk service dirs; conv
        scopes are drawn from ``conv_pairs`` (the real conversation index,
        ``[(owner_user_id, conv_id), ...]``) and included only when the
        conversation actually carries service definitions.
        """
        scopes = [
            (SCOPE_GLOBAL, self._resolve_scope_id(SCOPE_GLOBAL, ""), "", "")]
        users_dir = _user_services_dir()
        try:
            if users_dir.is_dir():
                for udir in sorted(
                        x for x in users_dir.iterdir() if x.is_dir()):
                    uid = udir.name
                    scopes.append((SCOPE_USER, uid, uid, ""))
        except OSError:
            logger.debug("iter_all_scopes: user dir scan failed", exc_info=True)
        if conv_pairs:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            for pair in conv_pairs:
                try:
                    uid, cid = pair
                except (TypeError, ValueError):
                    continue
                if not cid:
                    continue
                try:
                    raw = store.get_extra(cid, CONV_EXTRAS_KEY) or {}
                except Exception:
                    raw = {}
                if raw:
                    scopes.append((SCOPE_CONV, cid, uid, cid))
        return scopes

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

    def get_live_instance_cached(self, scope: str, scope_id: str,
                                 service_id: str) -> Optional[Service]:
        """Return a live instance snapshot without lazy-connecting."""
        sid = self._resolve_scope_id(scope, scope_id)
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
            _t0 = time.monotonic()
            self._connect_one(sid, svc_id)
            logger.debug("[startup-timing] service connect %s/%s: %.1fms",
                        sid[:8] if len(sid) > 8 else sid, svc_id,
                        (time.monotonic() - _t0) * 1000)

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
            _t0 = time.monotonic()
            from tasks import _register_all_services
            _register_all_services()
            logger.debug("[startup-timing] service %s register services: %.1fms",
                        service_id, (time.monotonic() - _t0) * 1000)
            svc_class = ServiceFactory.get(svc_def.service_type)
            from core.expression import LazyResolveDict
            lazy_config = LazyResolveDict(svc_def.config)
            lazy_config["_service_id"] = service_id
            # Pass scope-derived owner identity through so services that
            # take inverse-direction calls (e.g. RelayService FUSE bridge)
            # can self-initialize their user/conv binding instead of
            # waiting for a tool handler to call set_user_id() — that
            # waiting window is what made `ls /cc_sessions/` from a
            # bare relay terminal block: the FUSE callback hits the
            # server before any tool has wired up the owner.
            lazy_config["_scope"] = svc_def.scope
            lazy_config["_scope_id"] = svc_def.scope_id
            svc_instance = svc_class(lazy_config)
            _t0 = time.monotonic()
            svc_instance.connect()
            logger.debug("[startup-timing] service %s connect call: %.1fms",
                        service_id, (time.monotonic() - _t0) * 1000)
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
