"""Audit Log — tracks who did what, when.

Records actions like flow CRUD, execution start/stop, user management,
plugin install/uninstall, etc.
"""

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Maximum audit entries kept in memory
MAX_ENTRIES = 10000


@dataclass
class AuditEntry:
    """A single audit log entry."""
    timestamp: str
    action: str          # e.g. "flow.create", "execution.start", "user.create"
    user: str            # username or "system" or "anonymous"
    resource_type: str   # e.g. "flow", "execution", "user", "plugin"
    resource_id: str     # e.g. flow_id, username, plugin_id
    details: Dict[str, Any]  # Additional context
    source_ip: str = ""  # Client IP if available

    def to_dict(self) -> dict:
        return asdict(self)


class AuditLog:
    """In-memory audit log with optional persistence.

    Usage:
        audit = AuditLog.get_instance()
        audit.log("flow.create", user="admin", resource_type="flow",
                   resource_id="my-flow", details={"name": "My Flow"})
        entries = audit.query(action="flow.*", limit=50)
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._entries: deque = deque(maxlen=max_entries)
        self._lock_entries = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'AuditLog':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset the singleton (for testing)."""
        cls._instance = None

    def log(
        self,
        action: str,
        user: str = "system",
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        source_ip: str = "",
    ) -> AuditEntry:
        """Record an audit event."""
        entry = AuditEntry(
            timestamp=datetime.now().isoformat(),
            action=action,
            user=user,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            source_ip=source_ip,
        )
        with self._lock_entries:
            self._entries.append(entry)

        logger.debug(f"AUDIT: {action} by {user} on {resource_type}/{resource_id}")
        return entry

    def query(
        self,
        action: Optional[str] = None,
        user: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query audit entries with filters.

        Args:
            action: Filter by action (supports prefix match with '*', e.g. "flow.*")
            user: Filter by username
            resource_type: Filter by resource type
            resource_id: Filter by resource ID
            since: ISO timestamp — only entries after this time
            limit: Max entries to return
        """
        with self._lock_entries:
            entries = list(self._entries)

        # Apply filters
        results = []
        for entry in reversed(entries):  # newest first
            if action:
                if action.endswith('*'):
                    if not entry.action.startswith(action[:-1]):
                        continue
                elif entry.action != action:
                    continue

            if user and entry.user != user:
                continue
            if resource_type and entry.resource_type != resource_type:
                continue
            if resource_id and entry.resource_id != resource_id:
                continue
            if since and entry.timestamp < since:
                continue

            results.append(entry.to_dict())
            if len(results) >= limit:
                break

        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        with self._lock_entries:
            entries = list(self._entries)

        if not entries:
            return {"total": 0, "actions": {}, "users": {}}

        actions: Dict[str, int] = {}
        users: Dict[str, int] = {}
        for e in entries:
            actions[e.action] = actions.get(e.action, 0) + 1
            users[e.user] = users.get(e.user, 0) + 1

        return {
            "total": len(entries),
            "actions": actions,
            "users": users,
            "oldest": entries[0].timestamp,
            "newest": entries[-1].timestamp,
        }

    def clear(self):
        """Clear all audit entries."""
        with self._lock_entries:
            self._entries.clear()

    def export_json(self) -> str:
        """Export all entries as JSON."""
        with self._lock_entries:
            entries = [e.to_dict() for e in self._entries]
        return json.dumps(entries, indent=2, ensure_ascii=False)
