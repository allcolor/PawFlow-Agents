"""Persistent owner-scoped Google Chat space policy registry.

The registry is internal runtime state.  Each Google Chat service owned by a
PawFlow user gets an independent file containing immutable space IDs, their
authorization state, conversation binding, and a bounded event de-duplication
ledger.  Writes are serialized and atomic.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from core import paths


_LOCK = threading.RLock()
_MAX_SEEN_EVENTS = 2000


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class GoogleChatSpaceStore:
    """Atomic policy store for one PawFlow owner and one Chat app service."""

    def __init__(self, owner_user_id: str, service_id: str):
        if not owner_user_id:
            raise ValueError("GoogleChatSpaceStore.owner_user_id is required")
        if not service_id:
            raise ValueError("GoogleChatSpaceStore.service_id is required")
        self.owner_user_id = owner_user_id
        self.service_id = service_id
        self._path = (
            paths.RUNTIME_DIR / "google_chat" / _key(owner_user_id)
            / f"{_key(service_id)}.json"
        )

    def _load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = {}
        except (OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("spaces", {})
        value.setdefault("seen_events", {})
        return value

    def _save(self, value: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def get_space(self, space_id: str) -> Dict[str, Any]:
        with _LOCK:
            return dict(self._load()["spaces"].get(space_id) or {})

    def observe_space(self, space_id: str, display_name: str = "") -> Dict[str, Any]:
        if not space_id:
            raise ValueError("space_id is required")
        now = time.time()
        with _LOCK:
            data = self._load()
            row = dict(data["spaces"].get(space_id) or {})
            row.setdefault("status", "pending")
            row.setdefault("conversation_id", "")
            row.setdefault("permission_mode", "read_only")
            if row["permission_mode"] != "read_only":
                row["permission_mode"] = "read_only"
            row.setdefault("invocation_mode", "mention_only")
            row.setdefault("created_at", now)
            row["updated_at"] = now
            if display_name:
                row["display_name"] = display_name
            data["spaces"][space_id] = row
            self._save(data)
            return dict(row)

    def allow_space(self, space_id: str, conversation_id: str,
                    permission_mode: str = "read_only") -> Dict[str, Any]:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if permission_mode != "read_only":
            raise ValueError("Google Chat spaces only support read_only permissions")
        with _LOCK:
            data = self._load()
            row = dict(data["spaces"].get(space_id) or {})
            now = time.time()
            row.update({
                "status": "allowed",
                "conversation_id": conversation_id,
                "permission_mode": permission_mode,
                "updated_at": now,
            })
            row.setdefault("created_at", now)
            row.setdefault("invocation_mode", "mention_only")
            data["spaces"][space_id] = row
            self._save(data)
            return dict(row)

    def set_status(self, space_id: str, status: str) -> Dict[str, Any]:
        if status not in {"pending", "denied", "removed"}:
            raise ValueError("invalid Google Chat space status")
        with _LOCK:
            data = self._load()
            row = dict(data["spaces"].get(space_id) or {})
            now = time.time()
            row.update({"status": status, "updated_at": now})
            row.setdefault("created_at", now)
            row.setdefault("conversation_id", "")
            row.setdefault("permission_mode", "read_only")
            data["spaces"][space_id] = row
            self._save(data)
            return dict(row)

    def claim_event(self, event_id: str) -> bool:
        """Atomically claim an inbound event; return False for a duplicate."""
        if not event_id:
            raise ValueError("event_id is required")
        with _LOCK:
            data = self._load()
            seen = data["seen_events"]
            if event_id in seen:
                return False
            seen[event_id] = time.time()
            if len(seen) > _MAX_SEEN_EVENTS:
                for old_id, _ in sorted(seen.items(), key=lambda item: item[1])[
                        :len(seen) - _MAX_SEEN_EVENTS]:
                    seen.pop(old_id, None)
            self._save(data)
            return True
