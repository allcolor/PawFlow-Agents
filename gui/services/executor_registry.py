"""Global registry for continuous executors.

Executors run in background threads and must survive Streamlit page
refreshes (which reset session_state). This module provides a
process-level singleton that keeps track of all running executors.

It also persists the list of running flows to disk so they can be
automatically restarted after a server restart.

Hooks into DeploymentRegistry to track instance status (running/stopped).
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Any

from engine.continuous_executor import ContinuousFlowExecutor

logger = logging.getLogger(__name__)

STATE_FILE = "continuous_state.json"


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
        self._save_state()

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
        self._save_state()

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
            self._save_state()
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

    def _save_state(self):
        """Persist the list of running flows to disk."""
        try:
            state = []
            with self._executor_lock:
                for fid, ex in self._executors.items():
                    try:
                        entry = {
                            "flow_id": fid,
                            "flow_path": self._find_flow_path(fid),
                            "flow_version": ex._flow_version,
                            "max_workers": ex._max_workers,
                            "max_retries": ex._max_retries,
                        }
                        state.append(entry)
                    except Exception as e:
                        logger.debug("Cannot serialize executor %s: %s", fid, e)

            path = Path(STATE_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"running_flows": state}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("Cannot save executor state: %s", e)

    def _find_flow_path(self, instance_id: str) -> Optional[str]:
        """Find the JSON file path for an instance.

        First checks DeploymentRegistry, then falls back to scanning flows/.
        """
        # Check deployment registry first
        dr = _get_deployment_registry()
        if dr:
            inst = dr.get(instance_id)
            if inst and inst.flow_path and Path(inst.flow_path).exists():
                return inst.flow_path

        # Fallback: search in flows/
        flows_dir = Path("flows")
        if flows_dir.exists():
            for p in flows_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("id") == instance_id:
                        return str(p)
                except Exception:
                    pass
        return None

    def restore_from_disk(self):
        """Restore executors from deployed instances marked as running.

        Called once on startup. Uses DeploymentRegistry as the source of truth,
        falling back to continuous_state.json for legacy data.
        """
        with self._executor_lock:
            if self._restored:
                return
            self._restored = True

        # First, restore from DeploymentRegistry (instances marked "running")
        dr = _get_deployment_registry()
        if dr:
            dr._ensure_loaded()
            dr.sync_with_executors()
            for iid, inst in dr.get_all().items():
                if inst.status != "running":
                    continue
                if self.get(iid) is not None:
                    continue
                self._restore_instance(iid, inst.flow_path,
                                       inst.max_workers, inst.max_retries)

        # Fallback: restore from continuous_state.json (legacy)
        self._restore_from_state_file()

        self._save_state()

    def _restore_instance(self, instance_id: str, flow_path: str,
                          max_workers: int = 4, max_retries: int = 3,
                          flow_version: Optional[int] = None) -> bool:
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

    def _restore_from_state_file(self):
        """Legacy restore from continuous_state.json."""
        path = Path(STATE_FILE)
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Cannot read executor state file: %s", e)
            return

        running = data.get("running_flows", [])
        if not running:
            return

        for entry in running:
            flow_id = entry.get("flow_id")
            if not flow_id or self.get(flow_id) is not None:
                continue

            flow_path = entry.get("flow_path")
            max_workers = entry.get("max_workers", 8)
            max_retries = entry.get("max_retries", 3)
            flow_version = entry.get("flow_version")

            self._restore_instance(flow_id, flow_path, max_workers,
                                   max_retries, flow_version)
