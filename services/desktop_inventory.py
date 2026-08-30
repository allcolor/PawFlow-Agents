"""Canonical server-side Desktop session inventory.

Implements the inventory contract of
``docs/MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md`` §12 for the
current one-desktop-per-relay runtime: a short-lived, reconciled registry
keyed by ``(relay_id, kind)`` and populated only from authoritative
sources — start/stop action results and ``desktop_status`` probes. It is
never populated from open browser tabs.

Rules enforced here:
- entries carry the relay-minted ``desktop_session_id`` for exact
  compare-and-stop; a stop for a stale session must be rejected upstream;
- ``unknown`` is a real state (worker unreachable), distinct from
  ``stopped``; rows do not silently disappear while unknown;
- no canonical host paths, tokens, or ports are stored or returned;
- missing identities raise ``ValueError`` (no anonymous fallback).

The module is process-local, like the VNC/audio proxy registries it sits
beside. A change listener lets the action layer emit
``desktop_inventory_changed`` SSE events without this module importing
the event bus.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

KIND_DOCKER = "docker"
KIND_HOST = "host"
_KINDS = (KIND_DOCKER, KIND_HOST)

STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_STOPPED = "stopped"
STATE_UNKNOWN = "unknown"

_lock = threading.Lock()
_entries = {}  # (relay_id, kind) -> entry dict
_change_listener = None  # fn(relay_id, entry_dict) or None


def _require(value, name):
    if not value:
        raise ValueError(f"Missing required {name}")
    return value


def _validate_kind(kind):
    if kind not in _KINDS:
        raise ValueError(f"Invalid desktop kind: {kind!r}")
    return kind


def set_change_listener(fn):
    """Register the single change listener (or None to clear)."""
    global _change_listener
    _change_listener = fn


def _notify(relay_id, entry):
    listener = _change_listener
    if listener is None:
        return
    try:
        listener(relay_id, dict(entry))
    except Exception:
        logger.debug("desktop inventory listener failed", exc_info=True)


def _public(entry):
    """Display-safe projection (plan §12.1): no ports, paths, or tokens."""
    return {
        "desktop_session_id": entry["desktop_session_id"],
        "relay_id": entry["relay_id"],
        "mode": entry["mode"],
        "state": entry["state"],
        "started_at": entry["started_at"],
        "started_by": entry["started_by"],
        "last_heartbeat_at": entry["last_heartbeat_at"],
        "workspace_isolated": entry["workspace_isolated"],
        "can_stop": entry["state"] in (STATE_RUNNING, STATE_UNKNOWN),
    }


def record_running(relay_id, kind, session_id, started_at=None, started_by=""):
    """Record a Desktop session confirmed running by the relay."""
    _require(relay_id, "relay_id")
    _validate_kind(kind)
    _require(session_id, "desktop_session_id")
    now = time.time()
    entry = {
        "desktop_session_id": session_id,
        "relay_id": relay_id,
        "mode": kind,
        "state": STATE_RUNNING,
        "started_at": started_at or now,
        "started_by": started_by or "",
        "last_heartbeat_at": now,
        "workspace_isolated": kind == KIND_DOCKER,
    }
    with _lock:
        previous = _entries.get((relay_id, kind))
        # Keep the original initiator across reconcile refreshes.
        if (previous and not started_by
                and previous["desktop_session_id"] == session_id):
            entry["started_by"] = previous["started_by"]
            entry["started_at"] = previous["started_at"]
        _entries[(relay_id, kind)] = entry
    _notify(relay_id, entry)
    return _public(entry)


def record_stopping(relay_id, kind, session_id):
    """Mark a session as stopping; conflict if the session is stale."""
    _require(relay_id, "relay_id")
    _validate_kind(kind)
    _require(session_id, "desktop_session_id")
    with _lock:
        entry = _entries.get((relay_id, kind))
        if not entry or entry["state"] == STATE_STOPPED:
            return None
        if entry["desktop_session_id"] != session_id:
            raise SessionConflict(entry["desktop_session_id"])
        entry = dict(entry, state=STATE_STOPPING,
                     last_heartbeat_at=time.time())
        _entries[(relay_id, kind)] = entry
    _notify(relay_id, entry)
    return _public(entry)


def record_stopped(relay_id, kind, session_id=""):
    """Record a session as stopped (explicit stop or reconciled absence).

    ``session_id`` is optional: a reconciled absence stops whatever entry
    is current; an explicit stop should pass the exact session so a stale
    stop never erases a newer row.
    """
    _require(relay_id, "relay_id")
    _validate_kind(kind)
    with _lock:
        entry = _entries.get((relay_id, kind))
        if not entry:
            return None
        if session_id and entry["desktop_session_id"] != session_id:
            raise SessionConflict(entry["desktop_session_id"])
        entry = dict(entry, state=STATE_STOPPED,
                     last_heartbeat_at=time.time())
        _entries[(relay_id, kind)] = entry
    _notify(relay_id, entry)
    return _public(entry)


def mark_unknown(relay_id):
    """Mark every non-stopped session of a relay as unknown (worker lost).

    ``unknown`` is not ``stopped``: the row stays visible and reports that
    confirmation cannot currently reach the relay (plan §12.2).
    """
    _require(relay_id, "relay_id")
    changed = []
    with _lock:
        for (rid, kind), entry in list(_entries.items()):
            if rid != relay_id or entry["state"] == STATE_STOPPED:
                continue
            entry = dict(entry, state=STATE_UNKNOWN,
                         last_heartbeat_at=time.time())
            _entries[(rid, kind)] = entry
            changed.append(entry)
    for entry in changed:
        _notify(relay_id, entry)
    return [_public(e) for e in changed]


def reconcile_status(relay_id, status, started_by=""):
    """Reconcile from an authoritative ``desktop_status`` probe result.

    ``status`` is the relay's status data dict. Docker desktop maps to
    ``running``/``session_id``; host desktop to ``local_screen_running``/
    ``local_screen_session_id``. Absence reconciles to stopped.
    """
    _require(relay_id, "relay_id")
    if not isinstance(status, dict):
        raise ValueError("Missing required status dict")
    results = []
    pairs = (
        (KIND_DOCKER, status.get("running"), status.get("session_id"),
         status.get("started_at")),
        (KIND_HOST, status.get("local_screen_running"),
         status.get("local_screen_session_id"),
         status.get("local_screen_started_at")),
    )
    for kind, running, session_id, started_at in pairs:
        if running and session_id:
            results.append(record_running(
                relay_id, kind, session_id,
                started_at=started_at, started_by=started_by))
        else:
            with _lock:
                known = _entries.get((relay_id, kind))
            if known and known["state"] != STATE_STOPPED:
                results.append(record_stopped(relay_id, kind))
    return results


def get_active(relay_id, kind):
    """Return the live (non-stopped) public entry for one relay/kind."""
    _require(relay_id, "relay_id")
    _validate_kind(kind)
    with _lock:
        entry = _entries.get((relay_id, kind))
    if not entry or entry["state"] == STATE_STOPPED:
        return None
    return _public(entry)


def list_active(relay_ids):
    """List non-stopped sessions for the given visible relay IDs only.

    Visibility filtering is the caller's job (authorization lives in the
    action layer); this function never returns rows outside ``relay_ids``.
    """
    wanted = set(relay_ids or ())
    with _lock:
        rows = [dict(e) for (rid, _k), e in _entries.items() if rid in wanted]
    return [_public(e) for e in rows if e["state"] != STATE_STOPPED]


class SessionConflict(Exception):
    """Raised when an operation names a session that is no longer current."""

    def __init__(self, current_session_id):
        super().__init__("stale desktop session")
        self.current_session_id = current_session_id


def _reset_for_tests():
    global _change_listener
    with _lock:
        _entries.clear()
    _change_listener = None
