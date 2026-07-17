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
P2 additions: the sidecar worker fetches its bootstrap (control token,
provider credentials, resolved instructions, tool definitions) from
`POST /api/realtime/livekit/worker/bootstrap`, authenticated with the
deployment secret `PAWFLOW_REALTIME_WORKER_SECRET` (shared with the worker
container via compose). Provider credentials are resolved server-side from
the service's `llm_service` and go only to the trusted worker — never to
the browser. Worker tool calls run through the existing RealtimeToolBridge
(silent approval, long tools detach); final transcripts persist as normal
conversation messages.
"""

import json
import logging
import os
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


def find_session_by_room(room_name: str):
    with _lock:
        for session in _sessions.values():
            if session["room_name"] == room_name:
                return session
    return None


def active_session_for_conversation(conversation_id: str):
    with _lock:
        sid = _by_conversation.get(conversation_id)
        return _sessions.get(sid) if sid else None


def announce_to_conversation_session(conversation_id: str, agent_name: str,
                                     text: str) -> bool:
    """Inject text into the conversation's live voice session, if any.

    Out-of-band announcements (e.g. a background flash_delegate result
    landing while the user is on voice): the text is sent to the worker
    as a `context` message and spoken by the live agent. Returns True
    only when actually delivered to a connected session for the SAME
    agent — callers keep their normal text-channel delivery regardless.
    """
    session = active_session_for_conversation(conversation_id)
    if session is None or session.get("state") == "closed":
        return False
    if agent_name and session["agent_name"] != agent_name:
        return False
    sock = session.get("worker_sock")
    if sock is None:
        return False
    try:
        _ws_send_text(sock, proto.dumps(
            proto.make_message("context", text=str(text))))
        return True
    except Exception:
        logger.debug("[livekit] out-of-band announce failed", exc_info=True)
        return False


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

    if engine_cfg["livekit_managed"]:
        # Managed stack: make sure the containers are up (provisioning is
        # asynchronous — first run pulls/builds images for a few minutes).
        from core.realtime_stack_manager import RealtimeStackManager
        stack = RealtimeStackManager.get_instance().ensure_stack()
        if stack["state"] == "error":
            raise ServiceError(
                "managed realtime stack failed to provision: "
                + stack["detail"])
        if stack["state"] != "ready":
            raise ServiceError(
                "managed realtime stack is provisioning ("
                + (stack["detail"] or "starting")
                + ") — retry in a moment")

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
        # Managed stack: the browser connects same-origin through the
        # /livekit signal proxy (empty URL + path — the client derives
        # ws(s)://<page-host>/livekit). External stack: direct URL.
        "livekit_url": ("" if engine_cfg["livekit_managed"]
                        else engine_cfg["livekit_url"]),
        "livekit_path": ("/livekit" if engine_cfg["livekit_managed"]
                         else ""),
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


# -- worker bootstrap ----------------------------------------------------

# Env var NAME holding the deployment secret, not the secret itself.
_WORKER_SECRET_ENV = "PAWFLOW_REALTIME_WORKER_SECRET"  # nosec B105

# providers whose credentials come from a specific llmConnection provider
_PROVIDER_LLM_REQUIREMENT = {"openai": "openai", "gemini": "gemini"}


def _resolve_provider_credentials(engine_cfg: dict, *, user_id: str,
                                  conversation_id: str) -> dict:
    """Server-side provider credential resolution for the trusted worker.

    `llm_service` is the migration-path source of truth (plan §Service
    Model). local_pipeline uses it for the TEXT turn only — audio never
    leaves the deployment. Raises ServiceError with actionable messages.
    """
    llm_service = engine_cfg.get("llm_service", "")
    if not llm_service:
        if engine_cfg.get("provider_secret"):
            # New-style config without an llmConnection: the worker reads
            # the named secret from its own environment (deployment-provided,
            # never transits through PawFlow storage).
            return {"source": "env",
                    "env_var": engine_cfg["provider_secret"]}
        raise ServiceError(
            "llm_service (or provider_secret) is required to resolve "
            f"provider credentials for '{engine_cfg['provider']}'")
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    svc = reg.resolve(llm_service, user_id=user_id,
                      conv_id=conversation_id)
    if svc is None:
        raise ServiceError(
            f"LLM service '{llm_service}' could not connect")
    provider = getattr(svc, "provider", "") or ""
    required = _PROVIDER_LLM_REQUIREMENT.get(engine_cfg["provider"])
    if required and provider != required:
        raise ServiceError(
            f"livekit provider '{engine_cfg['provider']}' requires a "
            f"'{required}' llmConnection for credentials, got "
            f"'{provider or 'unknown'}'")
    api_key = getattr(svc, "api_key", "") or ""
    if not api_key:
        raise ServiceError(f"LLM service '{llm_service}' has no api_key")
    return {
        "source": "llm_service",
        "provider": provider,
        "api_key": api_key,
        "base_url": getattr(svc, "base_url", "") or "",
        "default_model": getattr(svc, "default_model", "") or "",
    }


def _session_instructions(session: dict) -> str:
    """Agent/custom instructions + bounded conversation context.

    Reuses the legacy bridge's resolver (instructions_mode / context_mode
    semantics are identical across engines).
    """
    from types import SimpleNamespace
    from services._realtime_bridge import resolve_session_instructions
    cfg = session["engine_cfg"]
    shim = SimpleNamespace(instructions_mode=cfg["instructions_mode"],
                           instructions=cfg["instructions"],
                           context_mode=cfg["context_mode"])
    return resolve_session_instructions(
        shim, session["conversation_id"], session["agent_name"],
        session["user_id"])


def _tool_bridge(session: dict):
    """Lazily build (and cache) the session's RealtimeToolBridge."""
    bridge = session.get("tool_bridge")
    if bridge is None:
        from services._realtime_tools import RealtimeToolBridge
        bridge = RealtimeToolBridge(
            session["engine_cfg"]["tool_profile"],
            session["conversation_id"], session["agent_name"],
            session["user_id"])
        session["tool_bridge"] = bridge
    return bridge


def build_worker_bootstrap(session: dict) -> dict:
    """Full bootstrap payload for the trusted sidecar worker."""
    cfg = session["engine_cfg"]
    credentials = _resolve_provider_credentials(
        cfg, user_id=session["user_id"],
        conversation_id=session["conversation_id"])
    tool_definitions = []
    if cfg["tool_profile"]:
        tool_definitions = _tool_bridge(session).tool_definitions()
    return {
        **session["worker_bootstrap"],
        "conversation_id": session["conversation_id"],
        "agent_name": session["agent_name"],
        "provider": cfg["provider"],
        "model": cfg["model"],
        "voice": cfg["voice"],
        "modalities": cfg["modalities"],
        "video_input": cfg["video_input"],
        "video_fps_active": cfg["video_fps_active"],
        "video_fps_idle": cfg["video_fps_idle"],
        "local_pipeline": {k: cfg[k] for k in (
            "local_stt_url", "local_stt_model", "local_tts_url",
            "local_tts_model", "local_tts_voice") if cfg.get(k)},
        "turn_detection": cfg["turn_detection"],
        "max_session_seconds": cfg["max_session_seconds"],
        "instructions": _session_instructions(session),
        "tools": tool_definitions,
        "credentials": credentials,
    }


def _worker_bootstrap_endpoint(req):
    """POST /api/realtime/livekit/worker/bootstrap  {"room": ...}

    Public route; auth is the deployment secret shared with the worker
    container (header X-PawFlow-Worker-Secret): the env var for external
    deployments, else the generated managed-stack secret. Neither set —
    the endpoint refuses; no anonymous fallback.
    """
    import hmac as _hmac
    secret = os.environ.get(_WORKER_SECRET_ENV, "")
    if not secret:
        from core.realtime_stack_manager import RealtimeStackManager
        mgr = RealtimeStackManager.get_instance()
        if mgr.has_state():
            secret = mgr.credentials()["worker_secret"]
    if not secret:
        return _json_response(req, 503, {
            "error": f"{_WORKER_SECRET_ENV} is not configured on the "
                     "PawFlow server and no managed realtime stack exists "
                     "— worker bootstrap is disabled"})
    provided = (req.headers.get("X-PawFlow-Worker-Secret")
                or req.headers.get("x-pawflow-worker-secret") or "")
    if not _hmac.compare_digest(provided, secret):
        return _json_response(req, 403, {"error": "bad worker secret"})
    try:
        body = json.loads(req.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_response(req, 400, {"error": "invalid JSON body"})
    room = str(body.get("room", "") or "").strip()
    session = find_session_by_room(room) if room else None
    if session is None or session["state"] == "closed":
        return _json_response(req, 404, {"error": f"no active session for "
                                                  f"room '{room}'"})
    try:
        payload = build_worker_bootstrap(session)
    except ServiceError as e:
        return _json_response(req, 400, {"error": str(e)})
    except Exception as e:
        logger.error("[livekit] bootstrap failed", exc_info=True)
        return _json_response(req, 500, {"error": str(e)})
    _json_response(req, 200, payload)


# -- worker-control WebSocket ------------------------------------------

_EVENT_LOG_CAP = 500

_TRANSCRIPT_EVENTS = {
    "realtime.user.transcript.final": "user",
    "realtime.agent.transcript.final": "assistant",
}


def _handle_worker_tool_call(session: dict, sock, message: dict) -> None:
    """Run one worker tool call through the RealtimeToolBridge.

    Silent approval semantics are the bridge's own (exempt/pre-approved
    tools run, dialog-requiring tools get a spoken-friendly refusal). Long
    tools detach: the provider receives an interim tool_result immediately
    and the real result arrives later as a `context` message — or persists
    as a system message when the session ended meanwhile.
    """
    call_id = message["call_id"]
    name = str(message["name"])
    cid = session["conversation_id"]
    session_id = session["session_id"]
    _publish(cid, "realtime.tool.started",
             {"session_id": session_id, "tool": name})

    def _send_result(cid_, result_text):
        try:
            _ws_send_text(sock, proto.dumps(proto.make_message(
                "tool_result", call_id=cid_, ok=True,
                result={"text": str(result_text)})))
        except Exception:
            logger.debug("[livekit] tool_result send failed", exc_info=True)

    def _announce(text):
        # Late result of a detached tool: inject into the live session, or
        # persist as a system message when the session is already gone.
        current = get_session(session_id)
        if current is not None and current.get("worker_sock") is not None:
            try:
                _ws_send_text(current["worker_sock"], proto.dumps(
                    proto.make_message("context", text=str(text))))
                return
            except Exception:
                logger.debug("[livekit] context send failed", exc_info=True)
        from services._realtime_bridge import persist_voice_transcript
        persist_voice_transcript(cid, session["agent_name"],
                                 session["user_id"], "system", str(text))

    try:
        status = _tool_bridge(session).handle_call(
            call_id, name, message["arguments"],
            send_result=_send_result, announce=_announce)
    except Exception as exc:
        logger.warning("[livekit] tool call '%s' failed: %s", name, exc,
                       exc_info=True)
        try:
            _ws_send_text(sock, proto.dumps(proto.make_message(
                "tool_result", call_id=call_id, ok=False,
                result={"error": str(exc)})))
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        status = "error"
    _publish(cid, "realtime.tool.completed" if status in ("done",
                                                          "background")
             else "realtime.tool.rejected",
             {"session_id": session_id, "tool": name, "status": status})


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
                role = _TRANSCRIPT_EVENTS.get(str(message["name"]))
                if role:
                    # Final transcripts become normal conversation messages
                    # (UUID + timestamp, SSE fan-out) — deltas are UI-only.
                    from services._realtime_bridge import \
                        persist_voice_transcript
                    persist_voice_transcript(
                        cid, session["agent_name"], session["user_id"],
                        role, str((message["data"] or {}).get("text", "")))
            elif message["type"] == "tool_call":
                _handle_worker_tool_call(session, sock, message)
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


_SDK_CACHE = {"body": b"", "mtime": 0.0}


def _sdk_endpoint(req):
    """GET /api/realtime/livekit/sdk.js — vendored livekit-client UMD.

    Served from tasks/io/chat_ui/vendor/ (pinned version, see
    THIRD_PARTY_NOTICES.md). Session-authenticated like the chat UI it is
    part of; cached in memory, revalidated on mtime.
    """
    from pathlib import Path
    path = (Path(__file__).resolve().parents[1] / "tasks" / "io"
            / "chat_ui" / "vendor" / "livekit-client.umd.min.js")
    try:
        mtime = path.stat().st_mtime
        if not _SDK_CACHE["body"] or _SDK_CACHE["mtime"] != mtime:
            _SDK_CACHE["body"] = path.read_bytes()
            _SDK_CACHE["mtime"] = mtime
    except OSError:
        return _json_response(req, 404, {
            "error": "livekit-client bundle missing "
                     "(tasks/io/chat_ui/vendor/)"})
    req.complete(200, {
        "Content-Type": "application/javascript; charset=utf-8",
        "Cache-Control": "public, max-age=86400",
    }, _SDK_CACHE["body"])


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
    if "/api/realtime/livekit/worker/bootstrap" not in existing:
        # Public: the worker has no user session; auth is the deployment
        # secret header checked in the endpoint (503 when unconfigured).
        http_service.register_route(
            "POST", "/api/realtime/livekit/worker/bootstrap",
            "_realtime_livekit", callback=_worker_bootstrap_endpoint,
            public=True)
    if "/api/realtime/livekit/sdk.js" not in existing:
        http_service.register_route(
            "GET", "/api/realtime/livekit/sdk.js", "_realtime_livekit",
            callback=_sdk_endpoint)
    if not any(p.startswith("/ws/realtime-worker/") for p in existing):
        # Public: the sidecar worker has no user session. The handler
        # enforces the PawFlow-signed scoped token minted at session start.
        http_service.register_route(
            "GET", "/ws/realtime-worker/{session_id}", "_realtime_livekit",
            callback=None, ws_handler=worker_control_ws_handler, public=True)
    if not any(p.startswith("/livekit/") for p in existing):
        # Public: same-origin signal proxy for the MANAGED stack only
        # (dumb pipe to the local livekit-server; auth is the LiveKit
        # access token in the query, verified by livekit-server itself).
        # The handler refuses when no managed stack has been provisioned.
        from services.livekit_signal_proxy import livekit_signal_ws_proxy
        http_service.register_route(
            "GET", "/livekit/{path+}", "_realtime_livekit",
            callback=None, ws_handler=livekit_signal_ws_proxy, public=True)
    logger.info("[livekit] realtime routes registered")
