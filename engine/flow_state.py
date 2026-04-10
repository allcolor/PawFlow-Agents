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

from core.paths import RUNTIME_DIR, SYSTEM_DIR
_STATE_DIR = str(RUNTIME_DIR)
_STATE_FILE = str(RUNTIME_DIR / "running_flows.json")
_VERSIONS_DIR = str(RUNTIME_DIR / "flow_versions")


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

    def __init__(self, state_file: str = _STATE_FILE):
        self._state_file = state_file
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


class FlowVersionStore:
    """Stores flow config versions for rollback/downgrade.

    Each time a flow is started or updated, a versioned copy is saved.
    """

    def __init__(self, versions_dir: str = _VERSIONS_DIR):
        self._versions_dir = versions_dir
        os.makedirs(versions_dir, exist_ok=True)

    def _flow_dir(self, flow_id: str) -> str:
        d = os.path.join(self._versions_dir, flow_id)
        os.makedirs(d, exist_ok=True)
        return d

    def save_version(self, flow_id: str, flow_config: Dict[str, Any],
                     label: str = "") -> int:
        """Save a version of the flow config. Returns version number."""
        flow_dir = self._flow_dir(flow_id)
        existing = self.list_versions(flow_id)
        version = max([v["version"] for v in existing], default=0) + 1

        path = os.path.join(flow_dir, f"v{version}.json")
        data = {
            "version": version,
            "timestamp": datetime.now().isoformat(),
            "label": label or f"Version {version}",
            "config": flow_config,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        # Keep max 50 versions
        if len(existing) >= 50:
            oldest = sorted(existing, key=lambda v: v["version"])
            for old in oldest[:len(existing) - 49]:
                old_path = os.path.join(flow_dir, f"v{old['version']}.json")
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        logger.info(f"Flow '{flow_id}' saved as version {version}")
        return version

    def list_versions(self, flow_id: str) -> List[Dict[str, Any]]:
        """List available versions for a flow."""
        flow_dir = self._flow_dir(flow_id)
        versions = []
        for fname in os.listdir(flow_dir):
            if fname.startswith("v") and fname.endswith(".json"):
                try:
                    with open(os.path.join(flow_dir, fname)) as f:
                        data = json.load(f)
                    versions.append({
                        "version": data["version"],
                        "timestamp": data["timestamp"],
                        "label": data.get("label", ""),
                    })
                except (json.JSONDecodeError, KeyError, OSError):
                    continue
        return sorted(versions, key=lambda v: v["version"])

    def get_version(self, flow_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Get a specific version's config."""
        flow_dir = self._flow_dir(flow_id)
        path = os.path.join(flow_dir, f"v{version}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("config")
        except (json.JSONDecodeError, OSError):
            return None

    def get_latest_version(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest version's config."""
        versions = self.list_versions(flow_id)
        if not versions:
            return None
        return self.get_version(flow_id, versions[-1]["version"])

    def delete_versions(self, flow_id: str):
        """Delete all versions for a flow."""
        flow_dir = self._flow_dir(flow_id)
        if os.path.exists(flow_dir):
            shutil.rmtree(flow_dir, ignore_errors=True)
