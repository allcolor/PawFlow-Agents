"""Plan Store — file-based storage for plans.

Each plan is stored as an individual JSON file:
  data/plans/{user_id}/{conv_id}/{plan_id}.json

No duplication, no JSONL extras. Delete = delete file.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

from core.paths import PLANS_DIR as _PLANS_DIR


class PlanStore:
    """Singleton plan store — one JSON file per plan."""

    _instance: Optional["PlanStore"] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "PlanStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._write_lock = threading.Lock()

    def _plan_dir(self, user_id: str, conv_id: str) -> Path:
        if not user_id:
            raise ValueError("BUG: user_id is required for plan storage")
        safe_user = user_id.replace("/", "_").replace("\\", "_")
        safe_conv = conv_id.replace(":", "_")
        return _PLANS_DIR / safe_user / safe_conv

    def _plan_path(self, user_id: str, conv_id: str, plan_id: str) -> Path:
        return self._plan_dir(user_id, conv_id) / f"{plan_id}.json"

    def get(self, user_id: str, conv_id: str, plan_id: str) -> Optional[Dict]:
        """Load a single plan. Returns None if not found."""
        path = self._plan_path(user_id, conv_id, plan_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load plan %s: %s", plan_id, e)
            return None

    def list_plans(self, user_id: str, conv_id: str) -> List[Dict]:
        """List all plans for a conversation."""
        plan_dir = self._plan_dir(user_id, conv_id)
        if not plan_dir.exists():
            return []
        plans = []
        for f in plan_dir.glob("*.json"):
            try:
                plan = json.loads(f.read_text(encoding="utf-8"))
                plans.append(plan)
            except Exception as e:
                logger.warning("Failed to load plan %s: %s", f.name, e)
        plans.sort(key=lambda p: p.get("created_at", 0), reverse=True)
        return plans

    def save(self, user_id: str, conv_id: str, plan: Dict):
        """Save a plan (create or update)."""
        plan_id = plan.get("id", "")
        if not plan_id:
            raise ValueError("Plan must have an 'id' field")
        path = self._plan_path(user_id, conv_id, plan_id)
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    def delete(self, user_id: str, conv_id: str, plan_id: str) -> bool:
        """Delete a plan file. Returns True if deleted."""
        path = self._plan_path(user_id, conv_id, plan_id)
        if path.exists():
            with self._write_lock:
                path.unlink(missing_ok=True)
            return True
        return False

    def delete_all(self, user_id: str, conv_id: str):
        """Delete all plans for a conversation."""
        plan_dir = self._plan_dir(user_id, conv_id)
        if plan_dir.exists():
            with self._write_lock:
                for f in plan_dir.glob("*.json"):
                    f.unlink(missing_ok=True)
                # Clean up empty dirs
                try:
                    plan_dir.rmdir()
                except OSError:
                    pass

    @staticmethod
    def migrate_from_extras(conv_id: str, user_id: str, store):
        """One-shot migration: move plans from conv extras to plan files.

        Called at startup or on first access. Reads 'plans' extra,
        writes each plan as a file, then removes the extra.
        """
        try:
            plans = store.get_extra(conv_id, "plans") or {}
            if not plans or not isinstance(plans, dict):
                return
            ps = PlanStore.instance()
            migrated = 0
            for plan_id, plan in plans.items():
                if not isinstance(plan, dict):
                    continue
                plan.setdefault("id", plan_id)
                ps.save(user_id, conv_id, plan)
                migrated += 1
            if migrated:
                # Clear the bloated extras
                store.set_extra(conv_id, "plans", {}, user_id=user_id)
                logger.info("Migrated %d plan(s) from extras to files for conv %s",
                            migrated, conv_id[:8])
        except Exception as e:
            logger.warning("Plan migration failed for conv %s: %s", conv_id[:8], e)
