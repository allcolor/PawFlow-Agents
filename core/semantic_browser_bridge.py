"""Authenticated correlation between PFP runtimes and semantic browser tabs.

This module is generic infrastructure. It knows about browser tabs, installed
package IDs and bounded JSON messages; it contains no avatar or application
business logic.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.audit import AuditLog
from core.conversation_event_bus import ConversationEventBus

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}[a-z0-9]$")
_OPERATIONS = {"list", "get", "invoke"}
_MAX_JSON_BYTES = 64 * 1024
_TAB_TTL_SECONDS = 45.0
_DEFAULT_TIMEOUT_SECONDS = 10.0


class SemanticBrowserError(RuntimeError):
    """Raised when a semantic browser request cannot be safely completed."""


@dataclass
class _PendingRequest:
    user_id: str
    conversation_id: str
    tab_id: str
    target_package: str = ""
    operation: str = ""
    caller_package: str = ""
    caller_object: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str = ""


class SemanticBrowserBridge:
    """Process-local registry and request/result correlator for browser tabs."""

    _instance: Optional["SemanticBrowserBridge"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tabs: Dict[str, Dict[str, Any]] = {}
        self._pending: Dict[str, _PendingRequest] = {}

    @classmethod
    def instance(cls) -> "SemanticBrowserBridge":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            current = cls._instance
            cls._instance = None
        if current is not None:
            current.close()

    def close(self) -> None:
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
            self._tabs.clear()
        for request_id, row in pending:
            row.error = "semantic browser bridge stopped"
            row.event.set()
            self._audit(
                "pfp.semantic.result", row, request_id, "stopped")

    def register_tab(
            self, *, user_id: str, conversation_id: str, tab_id: str,
            bus_id: str, packages: list[str], active: bool = False) -> Dict[str, Any]:
        user_id = self._required_id(user_id, "user_id")
        conversation_id = self._required_id(conversation_id, "conversation_id")
        tab_id = self._required_id(tab_id, "tab_id")
        if str(bus_id or "") != f"__ui__:{tab_id}":
            raise SemanticBrowserError("semantic browser bus_id does not match tab_id")
        if not isinstance(packages, list):
            raise SemanticBrowserError("semantic browser packages must be a list")
        normalized = sorted({
            str(package).strip() for package in packages
            if _PACKAGE_RE.fullmatch(str(package or "").strip())
        })
        if not normalized:
            raise SemanticBrowserError(
                "semantic browser tab has no eligible installed packages")
        now = time.monotonic()
        with self._lock:
            existing = self._tabs.get(tab_id)
            if existing and existing["user_id"] != user_id:
                raise SemanticBrowserError(
                    "semantic browser tab belongs to another user")
            if existing and existing["conversation_id"] != conversation_id:
                self._disconnect_locked(tab_id, "semantic browser tab context changed")
            self._tabs[tab_id] = {
                "tab_id": tab_id,
                "bus_id": bus_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "packages": normalized,
                "active": bool(active),
                "updated_at": now,
            }
        return {"ok": True, "tab_id": tab_id, "packages": normalized}

    def unregister_tab(
            self, *, user_id: str, conversation_id: str, tab_id: str) -> bool:
        with self._lock:
            tab = self._tabs.get(str(tab_id or ""))
            if not tab:
                return False
            if (tab["user_id"] != str(user_id or "")
                    or tab["conversation_id"] != str(conversation_id or "")):
                raise SemanticBrowserError(
                    "semantic browser tab context mismatch")
            self._disconnect_locked(tab_id, "semantic browser tab disconnected")
            return True

    def authorize_bus(self, user_id: str, bus_id: str) -> bool:
        """Return whether a known semantic tab bus belongs to this user."""
        prefix = "__ui__:"
        if not str(bus_id or "").startswith(prefix):
            return False
        tab_id = str(bus_id)[len(prefix):]
        with self._lock:
            self._prune_stale_locked(time.monotonic())
            tab = self._tabs.get(tab_id)
            return tab is None or tab["user_id"] == str(user_id or "")

    def select_tab(
            self, user_id: str, conversation_id: str,
            target_package: str) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._prune_stale_locked(now)
            candidates = [
                tab for tab in self._tabs.values()
                if tab["user_id"] == user_id
                and tab["conversation_id"] == conversation_id
                and target_package in tab["packages"]
            ]
            if not candidates:
                raise SemanticBrowserError(
                    "no eligible browser tab for semantic request")
            if len(candidates) == 1:
                return dict(candidates[0])
            active = [tab for tab in candidates if tab["active"]]
            if len(active) == 1:
                return dict(active[0])
            raise SemanticBrowserError(
                "ambiguous semantic browser tab selection")

    def call(
            self, *, user_id: str, conversation_id: str,
            caller: Dict[str, Any], grant: Dict[str, Any], operation: str,
            arguments: Dict[str, Any], timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> Any:
        operation = str(operation or "").strip()
        if operation not in _OPERATIONS:
            raise SemanticBrowserError(
                f"unsupported semantic browser operation: {operation}")
        if not isinstance(arguments, dict):
            raise SemanticBrowserError(
                "semantic browser arguments must be an object")
        self._bounded_json(arguments, "semantic browser request")
        target_package = self._authorize_grant(grant, operation, arguments)
        tab = self.select_tab(user_id, conversation_id, target_package)
        request_id = uuid.uuid4().hex
        pending = _PendingRequest(
            user_id=user_id, conversation_id=conversation_id,
            tab_id=tab["tab_id"], target_package=target_package,
            operation=operation,
            caller_package=str((caller or {}).get("package") or ""),
            caller_object=str((caller or {}).get("object_id") or ""))
        with self._lock:
            self._pending[request_id] = pending
        payload = {
            "request_id": request_id,
            "operation": operation,
            "arguments": arguments,
            "target_package": target_package,
            "caller": {
                "package": str((caller or {}).get("package") or ""),
                "object_id": str((caller or {}).get("object_id") or ""),
            },
        }
        logger.info(
            "PFP semantic browser request: id=%s user=%s conv=%s tab=%s "
            "caller=%s target=%s operation=%s",
            request_id[:12], user_id, conversation_id, tab["tab_id"],
            payload["caller"]["package"], target_package, operation)
        self._audit("pfp.semantic.request", pending, request_id, "pending")
        ConversationEventBus.instance().publish_event(
            tab["bus_id"], "pfp_semantic_request", payload)
        if not pending.event.wait(max(0.001, float(timeout))):
            with self._lock:
                self._pending.pop(request_id, None)
            logger.warning(
                "PFP semantic browser timeout: id=%s user=%s conv=%s tab=%s",
                request_id[:12], user_id, conversation_id, tab["tab_id"])
            self._audit("pfp.semantic.timeout", pending, request_id, "timeout")
            raise SemanticBrowserError("semantic browser request timed out")
        if pending.error:
            raise SemanticBrowserError(pending.error)
        return pending.result

    def complete(
            self, *, user_id: str, conversation_id: str, tab_id: str,
            request_id: str, result: Any = None, error: str = "") -> bool:
        with self._lock:
            pending = self._pending.get(str(request_id or ""))
            if pending is None:
                raise SemanticBrowserError(
                    "semantic browser request is unknown or expired")
            if (pending.user_id != str(user_id or "")
                    or pending.conversation_id != str(conversation_id or "")
                    or pending.tab_id != str(tab_id or "")):
                raise SemanticBrowserError(
                    "semantic browser result context mismatch")
            if error:
                if len(str(error)) > 2048:
                    raise SemanticBrowserError(
                        "semantic browser error is too large")
                pending.error = str(error)
            else:
                self._bounded_json(result, "semantic browser result")
                pending.result = result
            self._pending.pop(request_id, None)
            pending.event.set()
        logger.info(
            "PFP semantic browser result: id=%s user=%s conv=%s tab=%s ok=%s",
            request_id[:12], user_id, conversation_id, tab_id, not bool(error))
        self._audit(
            "pfp.semantic.result", pending, request_id,
            "error" if error else "success")
        return True

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _disconnect_locked(self, tab_id: str, reason: str) -> None:
        self._tabs.pop(tab_id, None)
        affected = [
            (request_id, row)
            for request_id, row in self._pending.items()
            if row.tab_id == tab_id
        ]
        for request_id, row in affected:
            self._pending.pop(request_id, None)
            row.error = reason
            row.event.set()
            self._audit("pfp.semantic.result", row, request_id, "disconnected")

    def _prune_stale_locked(self, now: float) -> None:
        stale = [
            tab_id for tab_id, tab in self._tabs.items()
            if now - float(tab.get("updated_at") or 0) > _TAB_TTL_SECONDS
        ]
        for tab_id in stale:
            self._disconnect_locked(
                tab_id, "semantic browser tab became stale")

    @staticmethod
    def _authorize_grant(
            grant: Dict[str, Any], operation: str,
            arguments: Dict[str, Any]) -> str:
        if not isinstance(grant, dict):
            raise SemanticBrowserError(
                "semantic browser permission grant is invalid")
        target_package = str(arguments.get("package") or "").strip()
        grant_package = str(grant.get("package") or "").strip()
        if not target_package or target_package != grant_package:
            raise SemanticBrowserError(
                "semantic browser target package is not granted")
        operations = grant.get("operations") or []
        if operation not in operations:
            raise SemanticBrowserError(
                f"semantic browser operation is not granted: {operation}")
        if operation in {"get", "invoke"}:
            node = str(arguments.get("node") or "").strip()
            if not node.startswith(target_package + ":"):
                raise SemanticBrowserError(
                    "semantic browser node does not belong to target package")
            nodes = grant.get("nodes") or []
            if "*" not in nodes and node not in nodes:
                raise SemanticBrowserError(
                    f"semantic browser node is not granted: {node}")
        return target_package

    @staticmethod
    def _bounded_json(value: Any, label: str) -> None:
        try:
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SemanticBrowserError(
                f"{label} must be JSON-serializable") from exc
        if len(encoded) > _MAX_JSON_BYTES:
            raise SemanticBrowserError(f"{label} is too large")

    @staticmethod
    def _audit(action: str, pending: _PendingRequest,
               request_id: str, status: str) -> None:
        AuditLog.get_instance().log(
            action,
            user=pending.user_id,
            resource_type="pfp_semantic_request",
            resource_id=request_id,
            details={
                "conversation_id": pending.conversation_id,
                "tab_id": pending.tab_id,
                "caller_package": pending.caller_package,
                "caller_object": pending.caller_object,
                "target_package": pending.target_package,
                "operation": pending.operation,
                "status": status,
            },
        )

    @staticmethod
    def _required_id(value: str, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or not _ID_RE.fullmatch(normalized):
            raise SemanticBrowserError(
                f"invalid semantic browser {field}")
        return normalized
