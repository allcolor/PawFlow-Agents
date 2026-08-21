"""Versioned user-authority context for policy gating.

Implements docs/POLICY_GATING_SERVICE_PLAN.md sections 8.3-8.5 and 10:

- an ``AuthorizationContext`` is the persisted, versioned snapshot of the
  user-authored directives governing one work lineage (root request plus later
  corrections); only authenticated user ingress may add directives;
- an ``AuthorizationRef`` is the small immutable value (context id, revision,
  root turn) carried through runtime state, message ``source`` metadata and a
  contextvar so tool authorization can load the exact authority it must apply;
- ``AuthorizationContextStore`` is a file-backed store with atomic writes, a
  per-context lock, optimistic revision checks and a bounded snapshot cache.

Nothing here scans the transcript: authority is always loaded by explicit
reference.
"""

from __future__ import annotations

import contextvars
import copy
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

SOURCE_METADATA_KEY = "authorization"
DIRECTIVE_SOURCE_USER = "user"
MAX_DIRECTIVE_CHARS = 32_000
DEFAULT_ENVELOPE_CHARS = 8_000
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")


class AuthorizationContextError(ValueError):
    """Invalid authority operation (bad identifiers, non-user source, ...)."""


class StaleRevisionError(AuthorizationContextError):
    """The caller revised against a revision that is no longer current."""


@dataclass(frozen=True)
class AuthorizationRef:
    """Immutable handle to one revision of an authorization context."""

    context_id: str
    revision: int
    root_turn_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"context_id": self.context_id, "revision": int(self.revision),
                "root_turn_id": self.root_turn_id}

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["AuthorizationRef"]:
        if not isinstance(raw, dict):
            return None
        context_id = str(raw.get("context_id") or "")
        try:
            revision = int(raw.get("revision") or 0)
        except (TypeError, ValueError):
            return None
        if not context_id or revision < 1:
            return None
        return cls(context_id=context_id, revision=revision,
                   root_turn_id=str(raw.get("root_turn_id") or ""))


_current_ref: contextvars.ContextVar[Optional[AuthorizationRef]] = contextvars.ContextVar(
    "pawflow_authorization_ref", default=None)


def set_current_ref(ref: Optional[AuthorizationRef]):
    """Bind the authority of the running turn; returns the token to reset."""
    return _current_ref.set(ref)


def reset_current_ref(token) -> None:
    _current_ref.reset(token)


def get_current_ref() -> Optional[AuthorizationRef]:
    return _current_ref.get()


def ref_from_message_source(source: Any) -> Optional[AuthorizationRef]:
    """Read the ref a stamped message carries in ``source.authorization``."""
    if not isinstance(source, dict):
        return None
    return AuthorizationRef.from_dict(source.get(SOURCE_METADATA_KEY))


def _safe_part(value: str, label: str) -> str:
    value = str(value or "")
    if not _SAFE_PART.match(value):
        raise AuthorizationContextError(f"invalid {label}: {value!r}")
    return value


def _directive_text(content: Any) -> str:
    text = str(content or "").strip()
    if not text:
        raise AuthorizationContextError("a directive needs user-authored text")
    return text[:MAX_DIRECTIVE_CHARS]


def authority_envelope(doc: Dict[str, Any],
                       max_chars: int = DEFAULT_ENVELOPE_CHARS) -> Dict[str, Any]:
    """Bounded, evaluator-facing view of a context (plan section 8.6/13).

    ``truncated`` is set when directives had to be cut; the engine treats a
    truncated mandate as ambiguous (never as permission).
    """
    directives = [d for d in (doc.get("directives") or []) if isinstance(d, dict)]
    root = str(directives[0].get("content") or "") if directives else ""
    followups = [str(d.get("content") or "") for d in directives[1:]]
    truncated = False
    budget = max(256, int(max_chars))
    if len(root) > budget:
        root, truncated = root[:budget], True
    remaining = budget - len(root)
    bounded: List[str] = []
    for text in followups:
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            bounded.append(text[:remaining])
            truncated = True
            break
        bounded.append(text)
        remaining -= len(text)
    return {
        "context_id": str(doc.get("context_id") or ""),
        "revision": int(doc.get("revision") or 0),
        "root_request": root,
        "followups": bounded,
        "directive_ids": [str(d.get("id") or "") for d in directives],
        "optional_plan": (doc.get("optional_context") or {}).get("pawflow_plan_snapshot"),
        "truncated": truncated,
    }


class AuthorizationContextStore:
    """``<root>/<user_id>/<conversation_id>/<context_id>.json`` documents."""

    _instance: Optional["AuthorizationContextStore"] = None
    _instance_lock = threading.Lock()
    _CACHE_LIMIT = 256

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            from core.paths import RUNTIME_DIR
            root = Path(RUNTIME_DIR) / "authorization-contexts"
        self._root = Path(root)
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._cache: Dict[tuple, Dict[str, Any]] = {}

    @classmethod
    def instance(cls) -> "AuthorizationContextStore":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── paths and locks ──────────────────────────────────────────────

    def _path(self, user_id: str, conversation_id: str, context_id: str) -> Path:
        return (self._root / _safe_part(user_id, "user_id")
                / _safe_part(conversation_id, "conversation_id")
                / (_safe_part(context_id, "context_id") + ".json"))

    def _lock_for(self, path: Path) -> threading.RLock:
        key = str(path)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.RLock()
            return lock

    @staticmethod
    def _write(path: Path, doc: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read(path: Path) -> Optional[Dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def _remember(self, doc: Dict[str, Any]) -> None:
        key = (doc["user_id"], doc["conversation_id"], doc["context_id"], doc["revision"])
        self._cache[key] = copy.deepcopy(doc)
        while len(self._cache) > self._CACHE_LIMIT:
            self._cache.pop(next(iter(self._cache)))

    # ── lifecycle ────────────────────────────────────────────────────

    def create(self, *, user_id: str, conversation_id: str, root_turn_id: str,
               root_message_id: str, content: Any,
               optional_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start a new lineage from an authenticated root user request."""
        text = _directive_text(content)
        now = time.time()
        context_id = uuid.uuid4().hex
        doc = {
            "context_id": context_id,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "user_id": _safe_part(user_id, "user_id"),
            "conversation_id": _safe_part(conversation_id, "conversation_id"),
            "root_turn_id": str(root_turn_id or ""),
            "root_message_id": str(root_message_id or ""),
            "directives": [{
                "id": uuid.uuid4().hex,
                "message_id": str(root_message_id or ""),
                "timestamp": now,
                "content": text,
                "source_type": DIRECTIVE_SOURCE_USER,
                "operation": "root",
            }],
            "optional_context": dict(optional_context or {}),
        }
        path = self._path(user_id, conversation_id, context_id)
        with self._lock_for(path):
            self._write(path, doc)
            self._remember(doc)
        return copy.deepcopy(doc)

    def get(self, user_id: str, conversation_id: str,
            context_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(user_id, conversation_id, context_id)
        with self._lock_for(path):
            doc = self._read(path)
        return copy.deepcopy(doc) if doc else None

    def append_user_directive(self, user_id: str, conversation_id: str,
                              context_id: str, *, message_id: str, content: Any,
                              expected_revision: Optional[int] = None,
                              source_type: str = DIRECTIVE_SOURCE_USER) -> Dict[str, Any]:
        """Revise a lineage with a later authenticated user directive.

        Assistant prose, tool results, delegate messages and web content are
        evidence, never authority: any other ``source_type`` is refused.
        """
        if str(source_type or "") != DIRECTIVE_SOURCE_USER:
            raise AuthorizationContextError(
                "only authenticated user input may revise authority "
                f"(got source_type={source_type!r})")
        text = _directive_text(content)
        path = self._path(user_id, conversation_id, context_id)
        with self._lock_for(path):
            doc = self._read(path)
            if doc is None:
                raise AuthorizationContextError(
                    f"authorization context not found: {context_id}")
            current = int(doc.get("revision") or 0)
            if expected_revision is not None and int(expected_revision) != current:
                raise StaleRevisionError(
                    f"authorization context {context_id} is at revision "
                    f"{current}, caller expected {expected_revision}")
            now = time.time()
            doc.setdefault("directives", []).append({
                "id": uuid.uuid4().hex,
                "message_id": str(message_id or ""),
                "timestamp": now,
                "content": text,
                "source_type": DIRECTIVE_SOURCE_USER,
                "operation": "revise",
            })
            doc["revision"] = current + 1
            doc["updated_at"] = now
            self._write(path, doc)
            self._remember(doc)
        return copy.deepcopy(doc)

    def snapshot(self, user_id: str, conversation_id: str, context_id: str,
                 revision: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Immutable copy of one revision; None when that revision is unknown.

        The current revision is always served from disk; an older one only
        from the bounded cache, so a stale caller learns it must reload.
        """
        try:
            key_prefix = (_safe_part(user_id, "user_id"),
                          _safe_part(conversation_id, "conversation_id"),
                          _safe_part(context_id, "context_id"))
        except AuthorizationContextError:
            return None
        doc = self.get(user_id, conversation_id, context_id)
        if doc is None:
            return None
        if revision is None or int(revision) == int(doc.get("revision") or 0):
            return doc
        cached = self._cache.get(key_prefix + (int(revision),))
        return copy.deepcopy(cached) if cached else None

    @staticmethod
    def ref(doc: Dict[str, Any]) -> AuthorizationRef:
        return AuthorizationRef(
            context_id=str(doc.get("context_id") or ""),
            revision=int(doc.get("revision") or 0),
            root_turn_id=str(doc.get("root_turn_id") or ""))

    def list_for_conversation(self, user_id: str,
                              conversation_id: str) -> List[Dict[str, Any]]:
        folder = (self._root / _safe_part(user_id, "user_id")
                  / _safe_part(conversation_id, "conversation_id"))
        if not folder.is_dir():
            return []
        docs = []
        for path in sorted(folder.glob("*.json")):
            doc = self._read(path)
            if doc:
                docs.append(doc)
        docs.sort(key=lambda d: float(d.get("created_at") or 0))
        return docs

    def delete_for_conversation(self, user_id: str, conversation_id: str) -> int:
        """Conversation deletion removes its authority documents."""
        folder = (self._root / _safe_part(user_id, "user_id")
                  / _safe_part(conversation_id, "conversation_id"))
        removed = 0
        if folder.is_dir():
            for path in folder.glob("*.json"):
                with self._lock_for(path):
                    try:
                        path.unlink()
                        removed += 1
                    except FileNotFoundError:
                        pass
            try:
                folder.rmdir()
            except OSError:
                pass
        for key in [k for k in self._cache
                    if k[0] == user_id and k[1] == conversation_id]:
            self._cache.pop(key, None)
        return removed


# ── active authority per (conversation, agent) ───────────────────────
#
# Tool execution runs in worker threads where the contextvar set for the turn
# is not visible, so the ingress path also records the lineage an agent is
# currently working under in a conversation extra. This is an explicit record
# written when an authenticated user message arrives — never a transcript scan.

ACTIVE_AUTHORITY_EXTRA = "gating_authority"


def active_authority_ref(conversation_id: str, agent_name: str) -> Optional[AuthorizationRef]:
    if not conversation_id or not agent_name:
        return None
    try:
        from core.conversation_store import ConversationStore
        table = ConversationStore.instance().get_extra(conversation_id, ACTIVE_AUTHORITY_EXTRA) or {}
    except Exception:
        return None
    if not isinstance(table, dict):
        return None
    return AuthorizationRef.from_dict(table.get(agent_name) or table.get(agent_name.lower()))


def set_active_authority_ref(conversation_id: str, agent_name: str,
                             ref: AuthorizationRef) -> None:
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    table = store.get_extra(conversation_id, ACTIVE_AUTHORITY_EXTRA) or {}
    if not isinstance(table, dict):
        table = {}
    table[agent_name] = dict(ref.to_dict(), updated_at=time.time())
    store.set_extra(conversation_id, ACTIVE_AUTHORITY_EXTRA, table)


def record_user_ingress(*, user_id: str, conversation_id: str, agent_name: str,
                        message_id: str, turn_id: str, content: Any,
                        steering: bool) -> Optional[AuthorizationRef]:
    """Create or revise the lineage for an authenticated user message (plan 10.1/10.2, A4).

    ``steering`` is True when the addressed agent's turn is active: the message
    revises that agent's lineage. Otherwise a new lineage starts. Returns None
    (and touches nothing) without a user, agent or text.
    """
    text = str(content or "").strip()
    if not user_id or not conversation_id or not agent_name or not text:
        return None
    store = AuthorizationContextStore.instance()
    doc = None
    if steering:
        current = active_authority_ref(conversation_id, agent_name)
        if current is not None:
            try:
                doc = store.append_user_directive(
                    user_id, conversation_id, current.context_id,
                    message_id=message_id, content=text)
            except AuthorizationContextError:
                doc = None
    if doc is None:
        doc = store.create(user_id=user_id, conversation_id=conversation_id,
                           root_turn_id=turn_id, root_message_id=message_id, content=text)
    ref = store.ref(doc)
    set_active_authority_ref(conversation_id, agent_name, ref)
    return ref


__all__ = [
    "ACTIVE_AUTHORITY_EXTRA", "active_authority_ref", "record_user_ingress",
    "set_active_authority_ref",
    "AuthorizationContextError", "AuthorizationContextStore", "AuthorizationRef",
    "DEFAULT_ENVELOPE_CHARS", "SOURCE_METADATA_KEY", "StaleRevisionError",
    "authority_envelope", "get_current_ref", "ref_from_message_source",
    "reset_current_ref", "set_current_ref",
]
