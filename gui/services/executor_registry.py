"""Global registry for continuous executors.

Executors run in background threads and must survive Streamlit page
refreshes (which reset session_state). This module provides a
process-level singleton that keeps track of all running executors.

It also persists the list of running flows to disk so they can be
automatically restarted after a server restart.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Any

from engine.continuous_executor import ContinuousFlowExecutor

logger = logging.getLogger(__name__)

STATE_FILE = "continuous_state.json"


class ExecutorRegistry:
    """Thread-safe global registry for continuous executors."""

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

    def register(self, flow_id: str, executor: ContinuousFlowExecutor):
        """Register a running executor and persist state.

        If an executor already exists for this flow_id, stop it first
        to prevent duplicate execution.
        """
        with self._executor_lock:
            old = self._executors.get(flow_id)
            if old is not None and old is not executor:
                try:
                    old.stop()
                    logger.info("Stopped previous executor for flow '%s' before registering new one", flow_id)
                except Exception as e:
                    logger.warning("Failed to stop previous executor for '%s': %s", flow_id, e)
            self._executors[flow_id] = executor
        self._save_state()

    def unregister(self, flow_id: str):
        """Remove an executor from the registry and persist state."""
        with self._executor_lock:
            self._executors.pop(flow_id, None)
        self._save_state()

    def get(self, flow_id: str) -> Optional[ContinuousFlowExecutor]:
        """Get an executor by flow ID."""
        with self._executor_lock:
            return self._executors.get(flow_id)

    def get_all(self) -> Dict[str, ContinuousFlowExecutor]:
        """Get all registered executors (copy of dict)."""
        with self._executor_lock:
            return dict(self._executors)

    def cleanup_dead(self):
        """Remove executors that have stopped."""
        with self._executor_lock:
            dead = []
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
                        flow = ex._flow
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

    def _find_flow_path(self, flow_id: str) -> Optional[str]:
        """Find the JSON file path for a flow ID."""
        # Search in flows/ and data/agent_flows/
        for dir_name in ("flows", "data/agent_flows"):
            flows_dir = Path(dir_name)
            if flows_dir.exists():
                for p in flows_dir.glob("*.json"):
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        if data.get("id") == flow_id:
                            return str(p)
                    except Exception:
                        pass
        return None

    def restore_from_disk(self):
        """Restore executors from the persisted state file.

        Called once on startup. Loads each flow from disk and starts
        the executor, optionally restoring checkpoint data.
        """
        with self._executor_lock:
            if self._restored:
                return
            self._restored = True

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

        logger.info("Restoring %d continuous flow(s) from previous session", len(running))

        from gui.services.flow_service import FlowService
        flow_service = FlowService()

        for entry in running:
            flow_id = entry.get("flow_id")
            flow_path = entry.get("flow_path")
            if not flow_path or not Path(flow_path).exists():
                logger.warning("Cannot restore flow %s: file not found (%s)", flow_id, flow_path)
                continue

            # Skip if already running
            if self.get(flow_id) is not None:
                continue

            try:
                # Load and clean agent metadata fields before parsing
                with open(flow_path, "r", encoding="utf-8") as ff:
                    raw = json.load(ff)
                clean = {k: v for k, v in raw.items() if not k.startswith("_")}
                from engine.parser import FlowParser
                flow = FlowParser.parse(clean)
                max_workers = entry.get("max_workers", 8)
                max_retries = entry.get("max_retries", 3)

                executor = ContinuousFlowExecutor(
                    flow,
                    max_workers=max_workers,
                    max_retries=max_retries,
                )
                # Restore flow version from saved state
                saved_version = entry.get("flow_version")
                if saved_version and isinstance(saved_version, int):
                    executor._flow_version = saved_version

                # Try to restore checkpoint (queue contents)
                if executor._checkpoint_mgr:
                    cp = executor._checkpoint_mgr.load_latest_checkpoint()
                    if cp:
                        flowfiles = executor._checkpoint_mgr.restore_flowfiles(cp)
                        for conn_key, ffs in flowfiles.items():
                            src_id, tgt_id = conn_key
                            conn = executor._connections.get_connection(src_id, tgt_id)
                            if conn:
                                for ff in ffs:
                                    conn.enqueue(ff)
                        logger.info("Restored checkpoint for flow %s", flow_id)

                executor.start()
                with self._executor_lock:
                    self._executors[flow_id] = executor
                logger.info("Restored continuous executor for flow: %s", flow_id)
            except Exception as e:
                logger.error("Failed to restore executor for %s: %s", flow_id, e)

        # Also restore agent-deployed flows marked as "running"
        self._restore_agent_flows()

        # Clean up state file entries that failed to restore
        self._save_state()

    def _restore_agent_flows(self):
        """Restore flows from data/agent_flows/ that were marked as running."""
        agent_dir = Path("data/agent_flows")
        if not agent_dir.exists():
            return

        for p in agent_dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                flow_id = raw.get("id", p.stem)
                if raw.get("_status") != "running":
                    continue
                if self.get(flow_id) is not None:
                    continue  # already running

                clean = {k: v for k, v in raw.items() if not k.startswith("_")}
                from engine.parser import FlowParser
                flow = FlowParser.parse(clean)
                executor = ContinuousFlowExecutor(
                    flow, max_workers=4, max_retries=3,
                )
                executor.start()
                with self._executor_lock:
                    self._executors[flow_id] = executor
                logger.info("Restored agent flow: %s", flow_id)
            except Exception as e:
                logger.warning("Failed to restore agent flow %s: %s", p.name, e)
                # Mark as error so we don't retry every time
                try:
                    raw["_status"] = "error"
                    raw["_error"] = str(e)
                    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

    def sync_from_disk(self):
        """Re-read the state file and start any flows not yet in the registry.

        Unlike restore_from_disk() which runs once at startup, this can be
        called repeatedly to pick up flows started by other processes
        (e.g. agent tool in a separate FastAPI process).
        """
        state_path = Path(STATE_FILE)
        if not state_path.exists():
            return

        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        running = data.get("running_flows", [])
        if not running:
            return

        for entry in running:
            flow_id = entry.get("flow_id")
            if not flow_id:
                continue
            # Skip if already running in this process
            if self.get(flow_id) is not None:
                continue

            flow_path = entry.get("flow_path")
            if not flow_path or not Path(flow_path).exists():
                continue

            try:
                with open(flow_path, "r", encoding="utf-8") as ff:
                    raw = json.load(ff)
                clean = {k: v for k, v in raw.items() if not k.startswith("_")}
                from engine.parser import FlowParser
                flow = FlowParser.parse(clean)
                max_workers = entry.get("max_workers", 8)
                max_retries = entry.get("max_retries", 3)

                executor = ContinuousFlowExecutor(
                    flow,
                    max_workers=max_workers,
                    max_retries=max_retries,
                )
                saved_version = entry.get("flow_version")
                if saved_version and isinstance(saved_version, int):
                    executor._flow_version = saved_version

                # Restore checkpoint
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

                executor.start()
                with self._executor_lock:
                    self._executors[flow_id] = executor
                logger.info("Synced executor from disk for flow: %s", flow_id)
            except Exception as e:
                logger.warning("Failed to sync executor for %s: %s", flow_id, e)
