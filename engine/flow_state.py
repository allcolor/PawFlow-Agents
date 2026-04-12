"""Flow State Manager — persists which flows were running for crash recovery.

Tracks active continuous flows on disk so the server can automatically
restart them after a crash or planned restart.

Also manages flow config version history for downgrade support.

State file: data/config/running_flows.json
Version backups: data/config/flow_versions/{flow_id}/v{N}.json
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

import core.paths as _paths


class FlowStateEntry:
    """Metadata about a running flow."""

    def __init__(self, flow_id: str, flow_path: str = "",
                 parameters: Optional[Dict[str, Any]] = None,
                 max_workers: int = 8, max_retries: int = 3,
                 enable_checkpoints: bool = True,
                 checkpoint_interval: float = 30.0,
                 started_at: Optional[str] = None,
                 status: str = "running",
                 error: str = ""):
        self.flow_id = flow_id
        self.flow_path = flow_path
        self.parameters = parameters or {}
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.enable_checkpoints = enable_checkpoints
        self.checkpoint_interval = checkpoint_interval
        self.started_at = started_at or datetime.now().isoformat()
        self.status = status  # running, stopped, crashed, recovery_failed
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "flow_path": self.flow_path,
            "parameters": self.parameters,
            "max_workers": self.max_workers,
            "max_retries": self.max_retries,
            "enable_checkpoints": self.enable_checkpoints,
            "checkpoint_interval": self.checkpoint_interval,
            "started_at": self.started_at,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FlowStateEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class FlowStateManager:
    """Manages persistent state of running flows."""

    def __init__(self, state_file: str = ""):
        self._state_file = state_file or str(_paths.RUNTIME_DIR / "running_flows.json")
        self._entries: Dict[str, FlowStateEntry] = {}
        os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)

    def load(self):
        """Load state from disk."""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file) as f:
                data = json.load(f)
            for entry_data in data.get("flows", []):
                entry = FlowStateEntry.from_dict(entry_data)
                self._entries[entry.flow_id] = entry
            logger.info(f"FlowState loaded: {len(self._entries)} flows")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"FlowState load failed: {e}")

    def save(self):
        """Persist state to disk."""
        try:
            data = {
                "timestamp": datetime.now().isoformat(),
                "flows": [e.to_dict() for e in self._entries.values()],
            }
            with open(self._state_file, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error(f"FlowState save failed: {e}")

    def register_flow(self, flow_id: str, flow_path: str = "",
                      parameters: Optional[Dict[str, Any]] = None,
                      max_workers: int = 8, max_retries: int = 3,
                      enable_checkpoints: bool = True,
                      checkpoint_interval: float = 30.0):
        """Register a flow as running."""
        self._entries[flow_id] = FlowStateEntry(
            flow_id=flow_id,
            flow_path=flow_path,
            parameters=parameters,
            max_workers=max_workers,
            max_retries=max_retries,
            enable_checkpoints=enable_checkpoints,
            checkpoint_interval=checkpoint_interval,
        )
        self.save()

    def unregister_flow(self, flow_id: str):
        """Remove a flow from the running state."""
        if flow_id in self._entries:
            del self._entries[flow_id]
            self.save()

    def mark_crashed(self, flow_id: str, error: str = "Server crashed"):
        """Mark a flow as crashed (on startup, when we see it was running)."""
        entry = self._entries.get(flow_id)
        if entry:
            entry.status = "crashed"
            entry.error = error
            self.save()

    def mark_recovery_failed(self, flow_id: str, error: str):
        """Mark a flow's recovery as failed."""
        entry = self._entries.get(flow_id)
        if entry:
            entry.status = "recovery_failed"
            entry.error = error
            self.save()

    def mark_recovered(self, flow_id: str):
        """Mark a flow as successfully recovered."""
        entry = self._entries.get(flow_id)
        if entry:
            entry.status = "running"
            entry.error = ""
            self.save()

    def get_flows_to_recover(self) -> List[FlowStateEntry]:
        """Get flows that were running before crash (need recovery)."""
        return [e for e in self._entries.values() if e.status == "running"]

    def get_all_entries(self) -> List[FlowStateEntry]:
        """Get all flow state entries."""
        return list(self._entries.values())

    def get_entry(self, flow_id: str) -> Optional[FlowStateEntry]:
        """Get a specific flow's state."""
        return self._entries.get(flow_id)


