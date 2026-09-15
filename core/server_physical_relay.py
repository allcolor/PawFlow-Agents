"""Asynchronous lifecycle owned by a server physical relay, never by a child."""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from contextlib import nullcontext

from core import server_physical_config as config


class ServerPhysicalRelayManager:
    def __init__(self, registry):
        self.registry = registry
        self._guard = threading.RLock()
        self._save_lock = threading.Lock()
        self._scope_locks = {}
        self._operations = {}
        self._active = {}
        self._started = set()
        self._last_start = {}
        self._disconnected = {}

    def scope_lock(self, scope, scope_id):
        key = config.normalize_scope(scope, scope_id)
        with self._guard:
            return self._scope_locks.setdefault(key, threading.RLock())

    def adopt(self, scope, scope_id):
        with self.scope_lock(scope, scope_id):
            return config.adopt_scope(self.registry, scope, scope_id)

    def _get(self, scope, scope_id, physical_id):
        for record in config.load_groups(scope, scope_id):
            if record["physical_id"] == physical_id and not record["deleted"]:
                return record
        raise ValueError("Physical relay configuration was not found")

    def describe(self, scope, scope_id, physical_id):
        record = self._get(scope, scope_id, physical_id)
        result = self.public_record(record)
        with self._guard:
            operations = [op for op in self._operations.values()
                          if (op["scope"], op["scope_id"], op["physical_id"]) ==
                          (record["scope"], record["scope_id"], physical_id)]
            result["operation"] = copy.deepcopy(operations[-1]) if operations else None
        return result

    @staticmethod
    def public_record(record):
        return {
            key: record[key] for key in (
                "physical_id", "name", "scope", "scope_id", "revision", "enabled", "deleted")
        } | {"workspaces": [{
            "service_id": member["service_id"],
            "workspace_dir": member["config"]["server_workspace_dir"],
            "workspace": "/workspace", "mode": member["config"].get("mode", "readwrite"),
            "server_local_exec": bool(member["config"].get("server_local_exec")),
            "allow_service_tunnels": bool(member["config"].get("allow_service_tunnels")),
            "allow_exec": bool(member["config"].get("allow_exec", True)),
        } for member in record["members"]]}

    def submit(self, operation, scope, scope_id, physical_id, *, body=None, user_id=""):
        if operation not in {"save", "start", "stop", "restart", "delete", "ensure", "autostart"}:
            raise ValueError("Unknown physical relay operation")
        scope, scope_id = config.normalize_scope(scope, scope_id)
        key = (scope, scope_id, physical_id)
        payload = copy.deepcopy(body or {})
        with self._guard:
            if key in self._active:
                if operation in {"ensure", "autostart"}:
                    return {"accepted": False, "operation_id": self._active[key]}
                raise ValueError("A physical relay operation is already running in this scope")
            operation_id = str(uuid.uuid4())
            state = {
                "operation_id": operation_id, "physical_id": physical_id,
                "scope": scope, "scope_id": scope_id, "action": operation,
                "status": "running", "created_at": time.time(), "error": "",
            }
            self._operations[operation_id] = state
            self._active[key] = operation_id
            for old_id in list(self._operations):
                if len(self._operations) <= 100:
                    break
                if self._operations[old_id]["status"] != "running":
                    del self._operations[old_id]

        def run():
            try:
                result = self.execute(
                    operation, scope, scope_id, physical_id,
                    body=payload, user_id=user_id)
                with self._guard:
                    state.update(status="completed", result=result,
                                 physical_id=result["physical_id"])
            except Exception as exc:
                logging.getLogger(__name__).exception("Physical relay operation failed")
                with self._guard:
                    state.update(status="failed", error=str(exc))
            finally:
                with self._guard:
                    state["finished_at"] = time.time()
                    self._active.pop(key, None)

        worker = threading.Thread(target=run, daemon=True, name="server-physical-relay")
        try:
            worker.start()
        except BaseException:
            with self._guard:
                self._active.pop(key, None)
                state.update(status="failed", error="Unable to start relay operation")
            raise
        return {"accepted": True, "operation_id": operation_id}

    def operation(self, operation_id):
        with self._guard:
            if operation_id not in self._operations:
                raise ValueError("Physical relay operation was not found")
            return copy.deepcopy(self._operations[operation_id])

    def _commit(self, record):
        with self.registry._data_lock:
            config.overlay_definitions(self.registry._definitions.get(record["scope_id"], {}),
                                       record["scope"], record["scope_id"], [record])
        config.save_group(record)
        config.project_scope(self.registry, record["scope"], record["scope_id"], [record])
        # These are recoverable projections; the atomic parent document wins on
        # every load, including when an old per-service save was interrupted.
        try:
            self.registry._save(record["scope"], record["scope_id"])
        except Exception:
            logging.getLogger(__name__).exception(
                "Physical relay saved; service projections will recover on load")

    def _connected(self, record):
        with self.registry._data_lock:
            live = [self.registry._live_instances.get(record["scope_id"], {}).get(m["service_id"])
                    for m in record["members"]]
        return bool(live) and all(service is not None and service.is_connected() for service in live)

    def _running(self, record):
        from core.server_relay_manager import ServerRelayManager
        return ServerRelayManager.get_instance()._is_container_running(record["container_name"])

    def _stable(self, record):
        """A brief reconnect must not restart the group's outage grace."""
        with self.registry._data_lock:
            live = [self.registry._live_instances.get(record["scope_id"], {}).get(m["service_id"])
                    for m in record["members"]]
        now = time.monotonic()
        for service in live:
            if service is None:
                return False
            with service._relay_pool_lock:
                latest = service._relay_pool[-1] if service._relay_pool else None
                if latest is None or now - latest.get("connected_at", now) < 5.0:
                    return False
        return bool(live)

    def _start(self, record, *, replace=False):
        from core.server_relay_manager import ServerRelayManager

        key = (record["scope"], record["scope_id"], record["physical_id"])
        if not replace and key in self._started and self._running(record):
            return
        if not record["enabled"] or record["deleted"]:
            return
        for member in record["members"]:
            self.registry._connect_one(record["scope_id"], member["service_id"])
        first = record["members"][0]
        ServerRelayManager.get_instance().spawn_service_relay(
            first["service_id"], first["config"]["token"],
            scope=record["scope"], scope_id=record["scope_id"], user_id=record["user_id"],
            kind=record["kind"], allow_service_tunnels=bool(
                first["config"].get("allow_service_tunnels")),
            replace=replace, physical=record,
        )
        self._started.add(key)
        self._last_start[key] = time.monotonic()
        self._disconnected.pop(key, None)

    def _stop(self, record):
        from core._server_physical_launch import launch_file
        from core._server_relay_container import stop_managed_relay_container

        stop_managed_relay_container(record["container_name"])
        for member in record["members"]:
            self.registry._disconnect_one(record["scope_id"], member["service_id"])
        launch_file(record).unlink(missing_ok=True)
        self._started.discard((record["scope"], record["scope_id"], record["physical_id"]))

    def _ensure(self, record):
        key = (record["scope"], record["scope_id"], record["physical_id"])
        if not record["enabled"] or record["deleted"]:
            return
        now = time.monotonic()
        if self._connected(record):
            if self._stable(record):
                self._disconnected.pop(key, None)
            return
        if now - self._last_start.get(key, -60.0) < 60.0:
            return
        running = self._running(record)
        if running:
            disconnected_at = self._disconnected.setdefault(key, now)
            if now - disconnected_at < 15.0 or self._connected(record):
                return
        # Charge only an attempted spawn; all logical children share this budget.
        self._last_start[key] = now
        self._start(record, replace=running)

    def execute(self, operation, scope, scope_id, physical_id, *, body=None, user_id=""):
        """Worker entry point; never call from an HTTP action directly."""
        scope, scope_id = config.normalize_scope(scope, scope_id)
        with (self._save_lock if operation == "save" else nullcontext()), self.scope_lock(scope, scope_id):
            self.adopt(scope, scope_id)
            previous = self._get(scope, scope_id, physical_id) if physical_id else None
            if operation == "save":
                record = config.prepare_group(
                    self.registry, scope, scope_id, user_id, body or {}, previous)
                if previous:
                    self._stop(previous)
                try:
                    self._commit(record)
                except Exception:
                    if previous and previous["enabled"]:
                        self._start(previous)
                    raise
                if record["enabled"]:
                    self._start(record)
                return self.public_record(record)
            if previous is None:
                raise ValueError("physical_id is required")
            record = copy.deepcopy(previous)
            if operation in {"ensure", "autostart"}:
                if operation == "ensure":
                    self._ensure(record)
                else:
                    self._start(record)
                return self.public_record(record)
            if operation not in {"start", "stop", "restart", "delete"}:
                raise ValueError("Unknown physical relay operation")
            record["enabled"] = operation in {"start", "restart"}
            # Keep the disabled group visible until cleanup succeeds, so a
            # failed deletion can be retried after Docker becomes available.
            record["deleted"] = False
            record["revision"] += 1
            record["updated_at"] = time.time()
            self._commit(record)
            if record["enabled"]:
                self._start(record, replace=operation == "restart")
            else:
                self._stop(previous)
                if operation == "delete":
                    record["deleted"] = True
                    record["revision"] += 1
                    record["updated_at"] = time.time()
                    self._commit(record)
            return self.public_record(record)

    def set_permission(self, scope, scope_id, physical_id, service_id, key, enabled):
        """Persist an admin permission without blocking behind a Docker action."""
        if key not in {"server_local_exec", "allow_service_tunnels"} or not isinstance(enabled, bool):
            raise ValueError("Invalid logical relay permission")
        lock = self.scope_lock(scope, scope_id)
        if not lock.acquire(blocking=False):
            raise ValueError("Physical relay is busy; retry after its current operation")
        try:
            record = self._get(scope, scope_id, physical_id)
            for member in record["members"]:
                if member["service_id"] == service_id:
                    member["config"][key] = enabled
                    record["revision"] += 1
                    record["updated_at"] = time.time()
                    self._commit(record)
                    return
            raise ValueError("Logical relay is not a member of this physical")
        finally:
            lock.release()


def physical_manager(registry=None):
    if registry is None:
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
    # The registry's data lock protects singleton construction, never Docker I/O.
    with registry._data_lock:
        manager = getattr(registry, "_physical_relay_manager", None)
        if manager is None:
            manager = ServerPhysicalRelayManager(registry)
            registry._physical_relay_manager = manager
        return manager
