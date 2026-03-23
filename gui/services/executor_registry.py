"""Global registry for continuous executors.

Executors run in background threads and must survive Streamlit page
refreshes (which reset session_state). This module provides a
process-level singleton that keeps track of all running executors.

It also persists the list of running flows to disk so they can be
automatically restarted after a server restart.

Hooks into DeploymentRegistry to track instance status (running/stopped).

Hot-reload: watches source files for changes and auto-restarts running
flows so handler configuration is always fresh.
"""

import json
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Dict, Optional, Any

from engine.continuous_executor import ContinuousFlowExecutor

logger = logging.getLogger(__name__)

STATE_FILE = "continuous_state.json"  # kept for cleanup only

# Directories to watch for hot-reload
_HOT_RELOAD_DIRS = ["tasks", "core", "services"]
_HOT_RELOAD_INTERVAL = 5  # seconds between mtime scans
_HOT_RELOAD_DEBOUNCE = 2  # seconds to wait after last change before restart


def _get_deployment_registry():
    """Lazy import to avoid circular imports."""
    try:
        from gui.services.deployment_registry import DeploymentRegistry
        return DeploymentRegistry.get_instance()
    except Exception:
        return None


class ExecutorRegistry:
    """Thread-safe global registry for continuous executors.

    Keys are instance_id (which may equal flow_id for legacy deployments).
    """

    _instance: Optional["ExecutorRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._executors: Dict[str, ContinuousFlowExecutor] = {}
        self._executor_lock = threading.Lock()
        self._restored = False
        self._hot_reload_started = False
        self._shutting_down = False
        self._py_mtimes: Dict[str, float] = {}  # path -> mtime

    @classmethod
    def get_instance(cls) -> "ExecutorRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, instance_id: str, executor: ContinuousFlowExecutor):
        """Register a running executor and persist state.

        If an executor already exists for this instance_id, stop it first
        to prevent duplicate execution.
        """
        with self._executor_lock:
            old = self._executors.get(instance_id)
            if old is not None and old is not executor:
                try:
                    old.stop()
                    logger.info("Stopped previous executor for '%s' before registering new one", instance_id)
                except Exception as e:
                    logger.warning("Failed to stop previous executor for '%s': %s", instance_id, e)
            self._executors[instance_id] = executor

        # Update deployment status
        dr = _get_deployment_registry()
        if dr:
            dr.update_status(instance_id, "running")

    def unregister(self, instance_id: str):
        """Remove an executor from the registry and persist state.

        Marks the deployment as stopped (does NOT delete it).
        """
        with self._executor_lock:
            self._executors.pop(instance_id, None)

        # Mark deployment as stopped (not deleted)
        dr = _get_deployment_registry()
        if dr:
            dr.update_status(instance_id, "stopped")

    def get(self, instance_id: str) -> Optional[ContinuousFlowExecutor]:
        """Get an executor by instance ID."""
        with self._executor_lock:
            return self._executors.get(instance_id)

    def get_all(self) -> Dict[str, ContinuousFlowExecutor]:
        """Get all registered executors (copy of dict)."""
        with self._executor_lock:
            return dict(self._executors)

    def cleanup_dead(self):
        """Remove executors that have stopped."""
        dead = []
        with self._executor_lock:
            for fid, ex in self._executors.items():
                try:
                    status = ex.get_status()
                    if not status.get("is_running", False):
                        dead.append(fid)
                except Exception:
                    dead.append(fid)
            for fid in dead:
                del self._executors[fid]
        if dead:
                # Update deployment statuses
            dr = _get_deployment_registry()
            if dr:
                for fid in dead:
                    dr.update_status(fid, "stopped")
        return dead

    def count(self) -> int:
        with self._executor_lock:
            return len(self._executors)

    # -- Persistence --

    def restore_from_disk(self):
        """Restore executors from deployed instances marked as running.

        Uses DeploymentRegistry as the sole source of truth.
        """
        with self._executor_lock:
            if self._restored:
                return
            self._restored = True

        # Connect all enabled global services (listeners, filesystem, etc.)
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            GlobalServiceRegistry.get_instance().connect_all_enabled()
            logger.info("Global services connected at startup")
        except Exception as e:
            logger.warning("Failed to connect global services: %s", e)

        dr = _get_deployment_registry()
        if dr:
            dr._ensure_loaded()
            # Do NOT sync before restore — sync would mark all "running" as
            # "stopped" because executors aren't in memory yet (fresh process).
            for iid, inst in dr.get_all().items():
                if inst.status != "running":
                    continue
                if self.get(iid) is not None:
                    continue
                self._restore_instance(iid, inst.flow_path,
                                       inst.max_workers, inst.max_retries,
                                       parameters=inst.parameters)

        # Clean up legacy state file if present
        legacy = Path(STATE_FILE)
        if legacy.exists():
            legacy.unlink(missing_ok=True)

        # Auto-restart flows when source files change
        self.start_hot_reload()


    def _restore_instance(self, instance_id: str, flow_path: str,
                          max_workers: int = 4, max_retries: int = 3,
                          flow_version: Optional[int] = None,
                          parameters: Optional[Dict[str, Any]] = None) -> bool:
        """Restore a single executor from a flow file. Returns True on success."""
        if not flow_path or not Path(flow_path).exists():
            logger.warning("Cannot restore '%s': file not found (%s)", instance_id, flow_path)
            return False

        try:
            # Ensure tasks & services are registered before parsing
            from tasks import register_all_tasks
            register_all_tasks()

            with open(flow_path, "r", encoding="utf-8") as ff:
                raw = json.load(ff)
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            from engine.parser import FlowParser
            flow = FlowParser.parse(clean)

            executor = ContinuousFlowExecutor(
                flow,
                max_workers=max_workers,
                max_retries=max_retries,
                parameters=parameters if parameters else None,
            )
            if flow_version and isinstance(flow_version, int):
                executor._flow_version = flow_version

            # Try to restore checkpoint
            if executor._checkpoint_mgr:
                cp = executor._checkpoint_mgr.load_latest_checkpoint()
                if cp:
                    flowfiles = executor._checkpoint_mgr.restore_flowfiles(cp)
                    for conn_key, ffs in flowfiles.items():
                        src_id, tgt_id = conn_key
                        conn = executor._connections.get_connection(src_id, tgt_id)
                        if conn:
                            for ff_item in ffs:
                                conn.enqueue(ff_item)
                    logger.info("Restored checkpoint for '%s'", instance_id)

            executor.start()
            with self._executor_lock:
                self._executors[instance_id] = executor
            logger.info("Restored executor for '%s'", instance_id)
            return True
        except Exception as e:
            logger.error("Failed to restore executor for '%s': %s", instance_id, e)
            # Mark as error in deployment registry
            if _get_deployment_registry():
                _get_deployment_registry().update_status(instance_id, "error", str(e))
            return False

    # -- Hot-reload: watch source files, restart flows on change --

    def start_hot_reload(self):
        """Start background thread that watches .py files for changes."""
        if self._hot_reload_started:
            return
        self._hot_reload_started = True
        self._snapshot_mtimes()
        t = threading.Thread(target=self._hot_reload_loop, daemon=True,
                             name="hot-reload-watcher")
        t.start()
        logger.info("Hot-reload watcher started (dirs: %s, interval: %ds)",
                     _HOT_RELOAD_DIRS, _HOT_RELOAD_INTERVAL)

    def _snapshot_mtimes(self):
        """Record mtime of all .py files in watched directories."""
        self._py_mtimes.clear()
        for d in _HOT_RELOAD_DIRS:
            p = Path(d)
            if not p.is_dir():
                continue
            for py in p.rglob("*.py"):
                try:
                    self._py_mtimes[str(py)] = py.stat().st_mtime
                except OSError:
                    pass

    def _check_changes(self) -> list:
        """Check for .py file changes. Returns list of changed paths."""
        changed = []
        for path, old_mt in list(self._py_mtimes.items()):
            try:
                new_mt = Path(path).stat().st_mtime
                if new_mt > old_mt:
                    changed.append(path)
            except OSError:
                pass
        # Also check for new files
        for d in _HOT_RELOAD_DIRS:
            p = Path(d)
            if not p.is_dir():
                continue
            for py in p.rglob("*.py"):
                s = str(py)
                if s not in self._py_mtimes:
                    changed.append(s)
        return changed

    def request_shutdown(self):
        """Signal that the process is shutting down. Prevents hot-reload restart."""
        self._shutting_down = True

    def _hot_reload_loop(self):
        """Background loop: scan for changes, debounce, restart process."""
        while not self._shutting_down:
            _time.sleep(_HOT_RELOAD_INTERVAL)
            if self._shutting_down:
                return
            try:
                changed = self._check_changes()
                if not changed:
                    continue
                # Debounce: wait, then re-check to catch multi-file saves
                _time.sleep(_HOT_RELOAD_DEBOUNCE)
                if self._shutting_down:
                    return
                changed = self._check_changes()
                if not changed:
                    continue
                short = [os.path.basename(p) for p in changed[:5]]
                if len(changed) > 5:
                    short.append(f"... +{len(changed) - 5} more")
                logger.info("Hot-reload: %d file(s) changed (%s), restarting server...",
                             len(changed), ", ".join(short))
                print(f"\n[HOT-RELOAD] {len(changed)} file(s) changed: "
                      f"{', '.join(short)} — restarting server...")
                self._restart_server()
            except Exception as e:
                logger.error("Hot-reload error: %s", e)

    def _restart_server(self):
        """Restart the entire server process.

        Gracefully stops all executors, then re-exec's the process with
        the same arguments. Flows auto-restore via restore_from_disk().
        """
        if self._shutting_down:
            return
        import sys
        # Stop all running executors gracefully
        try:
            reg = ExecutorRegistry.get_instance()
            for iid, ex in list(reg.get_all().items()):
                try:
                    ex.stop()
                except Exception:
                    pass
        except Exception:
            pass
        # Re-exec the process
        os.execv(sys.executable, [sys.executable] + sys.argv)

