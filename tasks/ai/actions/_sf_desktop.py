"""AgentLoopTask actions — Desktop inventory and lifecycle (WS7).

Typed, authorization-scoped Desktop actions from
``docs/MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md`` §12.3,
applied to the current one-desktop-per-relay runtime:

- ``desktop_list_active``  — visibility-filtered canonical inventory;
- ``desktop_attach``       — viewer URL for a RUNNING desktop, never starts;
- ``desktop_stop_request`` — returns the exact session a stop would target;
- ``desktop_stop_confirm`` — compare-and-stop on the exact session ID.

Visibility is the relay visibility of the requesting principal
(``list_available_relays``); the inventory itself never returns rows
outside that set, and stale stop confirmations return a conflict so a
confirmation raced by a restart can never stop the newer session.
"""

import json
import logging

from tasks.ai.actions._sf_base import _UNHANDLED

logger = logging.getLogger(__name__)

_DESKTOP_ACTIONS = frozenset({
    "desktop_list_active", "desktop_attach",
    "desktop_stop_request", "desktop_stop_confirm",
})


def _conv_id(body, flowfile):
    return (body.get("conversation_id", "")
            or flowfile.get_attribute("http.conversation_id") or "")


def _visible_relay_ids(user_id, conv_id):
    from core.relay_bindings import list_available_relays
    return [r["relay_id"]
            for r in list_available_relays(user_id=user_id, conv_id=conv_id)]


def _publish_inventory_changed(conv_id, rows):
    if not conv_id:
        return
    try:
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conv_id, "desktop_inventory_changed", {"desktops": rows})
    except Exception:
        logger.debug("desktop_inventory_changed publish failed", exc_info=True)


def _probe(svc, relay_id, started_by=""):
    """Reconcile one relay's inventory from an authoritative status probe.

    Returns the status dict, or None when the relay is unreachable (the
    inventory rows are then marked ``unknown``, never silently dropped).
    """
    from services import desktop_inventory as inv
    try:
        status = svc._request("desktop_status")
    except Exception as exc:
        logger.info("[desktop] status probe failed for %s: %s", relay_id, exc)
        inv.mark_unknown(relay_id)
        return None
    if isinstance(status, dict):
        inv.reconcile_status(relay_id, status, started_by=started_by)
        return status
    inv.mark_unknown(relay_id)
    return None


def _audit(operation, *, user_id, relay_id, session_id="", outcome="",
           reason="", source=""):
    """Structured audit line (plan §12.4): no credentials, no host paths."""
    logger.info(
        "[desktop-audit] op=%s actor=%s relay=%s session=%s outcome=%s"
        " reason=%s source=%s",
        operation, user_id, relay_id, session_id, outcome, reason, source)


def _handle_sf_desktop(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster: Desktop inventory. Returns result or _UNHANDLED."""
    if action not in _DESKTOP_ACTIONS:
        return _UNHANDLED
    (_find_relay_svc, *_rest) = _helpers
    from services import desktop_inventory as inv

    conv_id = _conv_id(body, flowfile)

    if action == "desktop_list_active":
        visible = _visible_relay_ids(user_id, conv_id)
        requested = body.get("relay_id", "")
        if requested:
            visible = [rid for rid in visible if rid == requested]
        if body.get("probe"):
            for rid in visible:
                svc = _find_relay_svc(rid)
                if svc is not None and getattr(svc, "connected", True):
                    _probe(svc, rid)
                else:
                    # Unreachable relay: its rows must show as unknown,
                    # never keep claiming running (no-op without rows).
                    inv.mark_unknown(rid)
        rows = sorted(inv.list_active(visible),
                      key=lambda r: (r["relay_id"], r["mode"]))
        flowfile.set_content(json.dumps({"desktops": rows}).encode())
        return [flowfile]

    # Every remaining action addresses one relay.
    relay_id = body.get("relay_id", "")
    if not relay_id:
        flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]
    if relay_id not in _visible_relay_ids(user_id, conv_id):
        flowfile.set_content(json.dumps(
            {"error": f"Relay '{relay_id}' not found"}).encode())
        flowfile.set_attribute("http.response.status", "404")
        return [flowfile]
    mode = body.get("mode", "") or inv.KIND_DOCKER
    if mode not in (inv.KIND_DOCKER, inv.KIND_HOST):
        flowfile.set_content(json.dumps(
            {"error": f"Invalid mode '{mode}'"}).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]
    svc = _find_relay_svc(relay_id)
    if svc is None:
        inv.mark_unknown(relay_id)
        flowfile.set_content(json.dumps(
            {"error": f"Relay '{relay_id}' not connected"}).encode())
        flowfile.set_attribute("http.response.status", "409")
        return [flowfile]

    if action == "desktop_attach":
        # Attach never changes lifecycle: refuse unless the probe proves a
        # live session, then reuse the open flow with its start path fenced
        # off (open_desktop honors no_start).
        status = _probe(svc, relay_id, started_by=user_id)
        running = bool(status and status.get(
            "local_screen_running" if mode == inv.KIND_HOST else "running"))
        if not running:
            flowfile.set_content(json.dumps({
                "error": "No running desktop to attach",
                "code": "not_running", "relay_id": relay_id,
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        from tasks.ai.actions._sf_k7 import _handle_sf_k7
        _audit("desktop_attach", user_id=user_id, relay_id=relay_id,
               session_id=(status.get("session_id") or ""), outcome="attached",
               source=body.get("source", ""))
        return _handle_sf_k7(self, "open_desktop", {
            "relay_id": relay_id,
            "local_screen": mode == inv.KIND_HOST,
            "no_start": True,
        }, store, user_id, flowfile, _helpers)

    if action == "desktop_stop_request":
        _probe(svc, relay_id, started_by=user_id)
        row = inv.get_active(relay_id, mode)
        if row is None:
            flowfile.set_content(json.dumps({
                "error": "No active desktop", "code": "not_running",
                "relay_id": relay_id,
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        _audit("desktop_stop_request", user_id=user_id, relay_id=relay_id,
               session_id=row["desktop_session_id"], outcome="pending",
               source=body.get("source", ""))
        flowfile.set_content(json.dumps({
            "ok": True, "confirm_required": True, "desktop": row,
        }).encode())
        return [flowfile]

    if action == "desktop_stop_confirm":
        session_id = body.get("desktop_session_id", "")
        if not session_id:
            flowfile.set_content(json.dumps(
                {"error": "Missing desktop_session_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _probe(svc, relay_id, started_by=user_id)
        row = inv.get_active(relay_id, mode)
        if row is None:
            # Already gone: idempotent success (plan §21, lost-ack retry).
            _audit("desktop_stop_confirm", user_id=user_id, relay_id=relay_id,
                   session_id=session_id, outcome="already_stopped",
                   source=body.get("source", ""))
            flowfile.set_content(json.dumps(
                {"ok": True, "was_running": False}).encode())
            return [flowfile]
        if row["desktop_session_id"] != session_id:
            _audit("desktop_stop_confirm", user_id=user_id, relay_id=relay_id,
                   session_id=session_id, outcome="conflict",
                   reason="stale session", source=body.get("source", ""))
            flowfile.set_content(json.dumps({
                "error": "Stale desktop session", "code": "session_conflict",
                "current_session_id": row["desktop_session_id"],
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        try:
            inv.record_stopping(relay_id, mode, session_id)
            _server_local = bool(
                mode == inv.KIND_HOST and svc.config.get("server_managed")
                and svc.config.get("server_local_exec"))
            if mode == inv.KIND_HOST and not _server_local:
                result = svc._request("stop_local_desktop",
                                      session_id=session_id)
            else:
                result = svc._request(
                    "stop_desktop", session_id=session_id,
                    **({"local": True} if _server_local else {}))
            # The relay's compare-and-stop answers a conflict as DATA
            # ({stopped: false, conflict: true, current_session_id}) because
            # the transport strips error envelopes (see stop_desktop in
            # pawflow_relay/_relay_desktop.py).
            if isinstance(result, dict) and result.get("conflict"):
                _probe(svc, relay_id, started_by=user_id)
                _audit("desktop_stop_confirm", user_id=user_id,
                       relay_id=relay_id, session_id=session_id,
                       outcome="conflict", reason="relay reported newer session",
                       source=body.get("source", ""))
                flowfile.set_content(json.dumps({
                    "error": "Stale desktop session",
                    "code": "session_conflict",
                    "current_session_id": result.get("current_session_id", ""),
                }).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]
            # Release the viewer routes exactly like close_desktop does.
            _prefix = "local_desktop" if mode == inv.KIND_HOST else "desktop"
            _vnc_sid = f"{_prefix}_{relay_id}"
            from services.vnc_proxy import unregister_session
            unregister_session(_vnc_sid)
            try:
                from services.audio_proxy import unregister_audio_source
                unregister_audio_source(_vnc_sid)
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
            inv.record_stopped(relay_id, mode, session_id)
            _publish_inventory_changed(conv_id, inv.list_active(
                _visible_relay_ids(user_id, conv_id)))
            _audit("desktop_stop_confirm", user_id=user_id, relay_id=relay_id,
                   session_id=session_id, outcome="stopped",
                   source=body.get("source", ""))
            flowfile.set_content(json.dumps(
                {"ok": True, "stopped_session_id": session_id}).encode())
        except inv.SessionConflict as conflict:
            flowfile.set_content(json.dumps({
                "error": "Stale desktop session", "code": "session_conflict",
                "current_session_id": conflict.current_session_id,
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
        except Exception as e:
            _audit("desktop_stop_confirm", user_id=user_id, relay_id=relay_id,
                   session_id=session_id, outcome="error", reason=str(e),
                   source=body.get("source", ""))
            # Do not leave the row stuck in 'stopping': re-probe so the
            # inventory reflects what the relay actually did.
            try:
                _probe(svc, relay_id, started_by=user_id)
            except Exception:
                logger.debug("post-failure reprobe failed", exc_info=True)
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "502")
        return [flowfile]

    return _UNHANDLED
