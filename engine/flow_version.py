"""Flow Version Manager - track and apply flow updates.

Manages version history and provides diff-based updates
to a running ContinuousFlowExecutor.
"""

import copy
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FlowVersion:
    """A snapshot of a flow at a specific version."""
    version: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    flow_dict: Dict[str, Any] = field(default_factory=dict)
    change_description: str = ""
    author: str = ""


@dataclass
class FlowDiff:
    """Difference between two flow versions."""
    added_tasks: List[str] = field(default_factory=list)
    removed_tasks: List[str] = field(default_factory=list)
    modified_tasks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    added_relations: List[Dict] = field(default_factory=list)
    removed_relations: List[Dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (not self.added_tasks and not self.removed_tasks and
                not self.modified_tasks and not self.added_relations and
                not self.removed_relations)

    def to_dict(self) -> dict:
        return {
            "added_tasks": self.added_tasks,
            "removed_tasks": self.removed_tasks,
            "modified_tasks": self.modified_tasks,
            "added_relations": self.added_relations,
            "removed_relations": self.removed_relations,
        }


class FlowVersionManager:
    """Manages flow version history with diff computation.

    Usage:
        mgr = FlowVersionManager()
        mgr.save_version(flow_dict, "Initial flow")
        # ... user modifies the flow ...
        diff = mgr.compute_diff(old_version=1, new_flow_dict=new_dict)
        mgr.save_version(new_dict, "Fixed log task config")
        # Apply diff to a running executor
    """

    def __init__(self, max_versions: int = 50):
        self._versions: List[FlowVersion] = []
        self._max_versions = max_versions
        self._current_version = 0

    def save_version(self, flow_dict: Dict[str, Any],
                     description: str = "",
                     author: str = "") -> FlowVersion:
        """Save a new version of the flow."""
        self._current_version += 1
        version = FlowVersion(
            version=self._current_version,
            flow_dict=copy.deepcopy(flow_dict),
            change_description=description,
            author=author,
        )
        self._versions.append(version)

        # Trim old versions
        if len(self._versions) > self._max_versions:
            self._versions = self._versions[-self._max_versions:]

        logger.info(f"Flow version {self._current_version} saved: {description}")
        return version

    @property
    def current_version(self) -> int:
        return self._current_version

    def get_version(self, version: int) -> Optional[FlowVersion]:
        """Get a specific version."""
        for v in self._versions:
            if v.version == version:
                return v
        return None

    def get_latest(self) -> Optional[FlowVersion]:
        """Get the latest version."""
        return self._versions[-1] if self._versions else None

    def list_versions(self) -> List[Dict[str, Any]]:
        """List all versions (metadata only, no full flow_dict)."""
        return [
            {
                "version": v.version,
                "timestamp": v.timestamp,
                "description": v.change_description,
                "author": v.author,
                "task_count": len(v.flow_dict.get("tasks", {})),
            }
            for v in self._versions
        ]

    def compute_diff(self, old_version: int,
                     new_flow_dict: Dict[str, Any]) -> FlowDiff:
        """Compute the diff between a saved version and a new flow dict."""
        old_v = self.get_version(old_version)
        if not old_v:
            raise ValueError(f"Version {old_version} not found")

        return self._diff(old_v.flow_dict, new_flow_dict)

    def diff_versions(self, v1: int, v2: int) -> FlowDiff:
        """Compute diff between two saved versions."""
        ver1 = self.get_version(v1)
        ver2 = self.get_version(v2)
        if not ver1 or not ver2:
            raise ValueError(f"Version {v1} or {v2} not found")
        return self._diff(ver1.flow_dict, ver2.flow_dict)

    def _diff(self, old_dict: Dict, new_dict: Dict) -> FlowDiff:
        """Compute structural diff between two flow dicts."""
        old_tasks = set(old_dict.get("tasks", {}).keys())
        new_tasks = set(new_dict.get("tasks", {}).keys())

        added = list(new_tasks - old_tasks)
        removed = list(old_tasks - new_tasks)

        # Find modified tasks (same ID but different config)
        modified = {}
        common = old_tasks & new_tasks
        for tid in common:
            old_cfg = old_dict["tasks"][tid]
            new_cfg = new_dict["tasks"][tid]
            if old_cfg != new_cfg:
                modified[tid] = {
                    "old": old_cfg,
                    "new": new_cfg,
                }

        # Relations diff
        def rel_key(r):
            return (r.get("from", ""), r.get("to", ""), r.get("type", "success"))

        old_rels = {rel_key(r) for r in old_dict.get("relations", [])}
        new_rels = {rel_key(r) for r in new_dict.get("relations", [])}

        added_rels = [
            {"from": k[0], "to": k[1], "type": k[2]}
            for k in (new_rels - old_rels)
        ]
        removed_rels = [
            {"from": k[0], "to": k[1], "type": k[2]}
            for k in (old_rels - new_rels)
        ]

        return FlowDiff(
            added_tasks=added,
            removed_tasks=removed,
            modified_tasks=modified,
            added_relations=added_rels,
            removed_relations=removed_rels,
        )

    def can_rollback(self) -> bool:
        """Check if rollback is possible."""
        return len(self._versions) >= 2

    def get_rollback_target(self) -> Optional[FlowVersion]:
        """Get the version to roll back to (previous version)."""
        if len(self._versions) >= 2:
            return self._versions[-2]
        return None
