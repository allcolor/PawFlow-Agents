"""LiveKit realtime sessions — registry, start/stop API, worker-control WS.

P1 of docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md. PawFlow stays out of the
media path: this module authorizes sessions, mints scoped tokens
(services/_livekit_engine.py), tracks active sessions, and terminates the
worker-control WebSocket that the sidecar worker opens after joining the
room (services/_realtime_worker_protocol.py wire contract).

Routes (registered lazily on the request's HTTP listener, same pattern as
the /ws/realtime voice route):
  POST /api/realtime/livekit/start   session-authed browser API
  POST /api/realtime/livekit/stop    session-authed browser API
  GET  /ws/realtime-worker/{session_id}?token=...   public route, its own
       PawFlow-signed scoped token auth (worker is not a user session)

One active LiveKit session per conversation; the newcomer supersedes.
Force-stop sends `shutdown` on the control WS, closes it, and removes the
registry entry — it never poisons the next session (project convention).
"""

import json
import logging
import threading
import time
import urllib.parse
import uuid

from core import ServiceError

from services import _livekit_engine as engine
from services import _realtime_worker_protocol as proto
from services.audio_proxy import _ws_recv, _ws_close
from services._realtime_bridge import _ws_send_text

logger = logging.getLogger(__name__)

# session_id -> session dict
_sessions = {}
# conversation_id -> session_id (single active session per conversation)
_by_conversation = {}
_lock = threading.Lock()

# Worker gets a little longer than the browser room token: connect grace.
_WORKER_TOKEN_GRACE_S = 120


def _new_session(*, service_id: str, conversation_id: str, agent_name: str,
                 user_id: str, engine_cfg: dict) -> dict:
    session_id = str(uuid.uuid4())
    room_name = f"pawflow-{conversation_id[:8]}-{session_id[:8]}"
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "agent_name": agent_name,
        "user_id": user_id,
        "service_id": service_id,
        "room_name": room_name,
        "engine_cfg": engine_cfg,
        "created_at": time.time(),
        "state": "created",       # created -> worker_connected -> closed
        "worker_sock": None,       # set while the control WS is attached
        "worker_id": "",
        "events": [],              # bounded realtime.* event log
        "stop_reason": "",
    }


def get_session(session_id: str):
    with _lock:
        return _sessions.get(session_id)


def active_session_for_conversation(conversation_id: str):
    with _lock:
        sid = _by_conversation.get(conversation_id)
        return _sessions.get(sid) if sid else None


def start_livekit_session(*, service_id: str, conversation_id: str,
                          agent_name: str, user_id: str,
                          role: str = "") -> dict:
    """Authorize + register a session; mint browser/worker credentials.

    Returns the browser payload. The worker bootstrap (agent room token +
    control token) is kept on the registry entry for the P2 dispatcher.
    Raises ServiceError / PermissionError with actionable messages.
    """
    if not service_id or not conversation_id or not agent_name:
        raise ServiceError(
            "service, conversation_id and agent_name are required")
    if not user_id and (role or "").lower() != "admin":
        raise PermissionError(
            "realtime sessions require a user session (not an API key)")

    # Conversation ownership — same policy as the legacy voice bridge.
    from core.flow_runtime_access import conversation_owner
    owner = conversation_owner(conversation_id)
    if owner and owner != user_id and (role or "").lower() != "admin":
        raise PermissionError("not your conversation")

    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    svc_def = reg.resolve_definition(service_id, user_id=user_id,
                                     conv_id=conversation_id)
    if svc_def is None or getattr(svc_def, "service_type", "") != \
            "realtimeVoiceConnection":
        raise ServiceError(
            f"'{service_id}' is not a realtimeVoiceConnection service")
    engine_cfg = engine.resolve_livekit_config(svc_def.config or {})

    session = _new_session(service_id=service_id,
                           conversation_id=conversation_id,
                           agent_name=agent_name, user_id=user_id,
                           engine_cfg=engine_cfg)
    session_id = session["session_id"]
    room_name = session["room_name"]

    browser_token = engine.create_browser_room_token(
        engine_cfg, room_name=room_name, session_id=session_id,
        conversation_id=conversation_id, user_id=user_id)
    agent_token = engine.create_agent_room_token(
        engine_cfg, room_name=room_name, session_id=session_id,
        agent_name=agent_name)
    control_token = engine.create_worker_control_token(
        session_id=session_id, conversation_id=conversation_id,
        user_id=user_id, agent_name=agent_name,
        ttl_s=engine.room_token_ttl_seconds(
            engine_cfg["max_session_seconds"]) + _WORKER_TOKEN_GRACE_S)
    session["worker_bootstrap"] = {
        "session_id": session_id,
        "room_name": room_name,
        "livekit_url": engine_cfg["livekit_url"],
        "agent_room_token": agent_token,
        "control_token": control_token,
        # provider credentials are resolved and injected by the P2
        # dispatcher, never stored here and never sent to the browser
    }

    with _lock:
        previous_sid = _by_conversation.get(conversation_id)
        _sessions[session_id] = session
        _by_conversation[conversation_id] = session_id
    if previous_sid:
        # One active live session per conversation — the newcomer wins.
        stop_livekit_session(session_id=previous_sid, reason="superseded")

    logger.info("[livekit] session %s created cid=%s agent=%s service=%s",
                session_id[:8], conversation_id[:8], agent_name, service_id)
    _publish(conversation_id, "realtime.session.created", {
        "session_id": session_id, "agent_name": agent_name,
        "provider": engine_cfg["provider"]})
    return {
        "session_id": session_id,
        "room": room_name,
        "livekit_url": engine_cfg["livekit_url"],
        "token": browser_token,
        "provider": engine_cfg["provider"],
        "modalities": engine_cfg["modalities"],
        "video_input": engine_cfg["video_input"],
        "video_source": engine_cfg["video_source"],
        "max_session_seconds": engine_cfg["max_session_seconds"],
    }


def stop_livekit_session(*, session_id: str = "", conversation_id: str = "",
                         reason: str = "stopped") -> bool:
    """Stop a session by id or by conversation. Safe to call twice."""
    with _lock:
        if not session_id and conversation_id:
            session_id = _by_conversation.get(conversation_id, "")
        session = _sessions.pop(session_id, None) if session_id else None
        if session is not None:
            cid = session["conversation_id"]
            if _by_conversation.get(cid) == session_id:
                del _by_conversation[cid]
    if session is None:
        return False
    session["state"] = "closed"
    session["stop_reason"] = reason
    sock = session.get("worker_sock")
    if sock is not None:
        try:
            _ws_send_text(sock, proto.dumps(
                proto.make_message("shutdown", reason=reason)))
            _ws_close(sock, 1000, reason)
        except Exception:
            logger.debug("[livekit] worker WS close failed", exc_info=True)
    logger.info("[livekit] session %s stopped (%s)", session_id[:8], reason)
    _publish(session["conversation_id"], "realtime.session.closed", {
        "session_id": session_id, "reason": reason})
    return True


def _publish(conversation_id: str, event_type: str, data: dict) -> None:
    """Best-effort realtime.* event to every attached client."""
    try:
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.get_instance().publish_event(
            conversation_id, event_type, dict(data))
    except Exception:
        logger.debug("[livekit] event publish failed", exc_info=True)


# -- worker-control WebSocket ------------------------------------------

_EVENT_LOG_CAP = 500


def worker_control_ws_handler(sock, path_params: dict, meta: dict):
    """GET /ws/realtime-worker/{session_id}?token=...

    The route is public (a worker has no user session); authentication is
    the PawFlow-signed scoped token minted at session start. Fail closed on
    any mismatch.
    """
    session_id = (path_params or {}).get("session_id", "")
    query = urllib.parse.parse_qs(meta.get("query", "") or "")
    token = (query.get("token", [""])[0] or "").strip()

    def _reject(message: str):
        try:
            _ws_send_text(sock, proto.dumps(proto.make_message(
                "shutdown", reason=message)))
            _ws_close(sock, 1008, "rejected")
        except Exception:
            logger.debug("Ignored exception", exc_info=True)

    if not session_id or not token:
        _reject("session_id and token are required")
        return
    try:
        engine.verify_worker_control_token(token, session_id)
    except ValueError as e:
        logger.warning("[livekit] worker WS rejected: %s", e)
        _reject(str(e))
        return
    session = get_session(session_id)
    if session is None or session["state"] == "closed":
        _reject("session is not active")
        return

    session["worker_sock"] = sock
    cid = session["conversation_id"]
    logger.info("[livekit] worker WS attached sid=%s", session_id[:8])
    try:
        while True:
            opcode, payload = _ws_recv(sock)
            if opcode is None or opcode == 0x8:   # EOF / close
                break
            if opcode != 0x1:   # only text frames carry protocol messages
                continue
            try:
                message = proto.parse_message(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                logger.warning("[livekit] bad worker message: %s", e)
                continue
            if message["type"] not in proto.WORKER_TO_PAWFLOW:
                logger.warning("[livekit] unexpected worker message type %s",
                               message["type"])
                continue
            if message["type"] == "hello":
                session["worker_id"] = message["worker_id"]
                session["state"] = "worker_connected"
                _ws_send_text(sock, proto.dumps(proto.make_message(
                    "hello_ack", session_id=session_id)))
                _publish(cid, "realtime.session.ready",
                         {"session_id": session_id})
            elif message["type"] == "event":
                events = session["events"]
                events.append((message["name"], message["data"]))
                del events[:-_EVENT_LOG_CAP]
                _publish(cid, str(message["name"]),
                         dict(message["data"] or {},
                              session_id=session_id))
            elif message["type"] == "tool_call":
                # Tool bridge wiring lands in P2 — refuse explicitly rather
                # than time the worker out.
                _ws_send_text(sock, proto.dumps(proto.make_message(
                    "tool_result", call_id=message["call_id"], ok=False,
                    result={"error": "PawFlow tool bridge not wired yet "
                                     "(P2)"})))
            elif message["type"] == "bye":
                session["stop_reason"] = message["reason"]
                break
    except Exception:
        logger.warning("[livekit] worker WS error sid=%s", session_id[:8],
                       exc_info=True)
    finally:
        if session.get("worker_sock") is sock:
            session["worker_sock"] = None
        # Worker gone = session over (media follows the worker).
        stop_livekit_session(session_id=session_id,
                             reason=session.get("stop_reason")
                             or "worker_disconnected")


# -- HTTP API ------------------------------------------------------------

def _json_response(req, status: int, payload: dict) -> None:
    req.complete(status, {"Content-Type": "application/json"},
                 json.dumps(payload, ensure_ascii=False).encode())


def _start_endpoint(req):
    try:
        body = json.loads(req.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_response(req, 400, {"error": "invalid JSON body"})
    try:
        payload = start_livekit_session(
            service_id=str(body.get("service", "") or "").strip(),
            conversation_id=str(body.get("conversation_id", "") or "").strip(),
            agent_name=str(body.get("agent_name", "") or "").strip(),
            user_id=req.auth_user_id or "",
            role=req.auth_role or "")
    except PermissionError as e:
        return _json_response(req, 403, {"error": str(e)})
    except ServiceError as e:
        return _json_response(req, 400, {"error": str(e)})
    except Exception as e:
        logger.error("[livekit] start failed", exc_info=True)
        return _json_response(req, 500, {"error": str(e)})
    _json_response(req, 200, payload)


def _stop_endpoint(req):
    try:
        body = json.loads(req.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_response(req, 400, {"error": "invalid JSON body"})
    session_id = str(body.get("session_id", "") or "").strip()
    conversation_id = str(body.get("conversation_id", "") or "").strip()
    if not session_id and not conversation_id:
        return _json_response(
            req, 400, {"error": "session_id or conversation_id required"})
    # Ownership: stopping by conversation follows the same policy as start;
    # stopping by session id checks the recorded session owner.
    user_id = req.auth_user_id or ""
    role = (req.auth_role or "").lower()
    session = (get_session(session_id) if session_id
               else active_session_for_conversation(conversation_id))
    if session is not None and session["user_id"] and \
            session["user_id"] != user_id and role != "admin":
        return _json_response(req, 403, {"error": "not your session"})
    stopped = stop_livekit_session(session_id=session_id,
                                   conversation_id=conversation_id,
                                   reason="user_stop")
    _json_response(req, 200, {"stopped": stopped})


def register_livekit_routes(http_service) -> None:
    """Idempotently register the LiveKit realtime routes on a listener."""
    existing = {r.get("pattern", "") for r in http_service.get_routes()}
    if "/api/realtime/livekit/start" not in existing:
        http_service.register_route(
            "POST", "/api/realtime/livekit/start", "_realtime_livekit",
            callback=_start_endpoint)
    if "/api/realtime/livekit/stop" not in existing:
        http_service.register_route(
            "POST", "/api/realtime/livekit/stop", "_realtime_livekit",
            callback=_stop_endpoint)
    if not any(p.startswith("/ws/realtime-worker/") for p in existing):
        # Public: the sidecar worker has no user session. The handler
        # enforces the PawFlow-signed scoped token minted at session start.
        http_service.register_route(
            "GET", "/ws/realtime-worker/{session_id}", "_realtime_livekit",
            callback=None, ws_handler=worker_control_ws_handler, public=True)
    logger.info("[livekit] realtime routes registered")
