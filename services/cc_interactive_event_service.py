"""Claude Code interactive MITM event ingest service.

The proxy inside the Claude Code container observes Anthropic SSE bytes and
posts scrubbed copies here over WebSocket. Providers consume per-session
queues; if a queue fills, the session is marked unreliable instead of
silently dropping events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)


@dataclass
class CCInteractiveSessionEvents:
    session_token: str
    events: "queue.Queue[dict]"
    container_id: str = ""
    connected: bool = False
    unreliable: bool = False
    error: str = ""
    created_at: float = field(default_factory=time.time)
    last_event_at: float = 0.0


class CCInteractiveEventService(BaseService):
    TYPE = "ccInteractiveEvents"
    VERSION = "1.0.0"
    NAME = "Claude Code Interactive Events"
    DESCRIPTION = "Receives MITM-observed Claude Code SSE events over WebSocket"

    _instances_lock = threading.Lock()
    _instances: Dict[str, "CCInteractiveEventService"] = {}

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._service_id = config.get("_service_id", "")
        self._connection = None
        self._route_path = ""
        self._sessions: Dict[str, CCInteractiveSessionEvents] = {}
        self._sessions_lock = threading.RLock()
        try:
            self._max_queue = int(config.get("max_queue", 4096) or 4096)
        except (TypeError, ValueError):
            self._max_queue = 4096

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "token": {"type": "string", "required": True, "sensitive": True,
                      "description": "Service token required by the container proxy"},
            "max_queue": {"type": "integer", "required": False, "default": 4096,
                          "description": "Maximum queued events per interactive session"},
        }

    @property
    def service_id(self) -> str:
        return self._service_id

    def connect(self):
        from services.http_listener_service import HTTPListenerService
        instances = HTTPListenerService.all_instances()
        if not instances:
            logger.warning(
                "CCInteractiveEventService %s: no HTTPListenerService running yet",
                self._service_id)
            self._initialized = False
            return
        listener = next(iter(instances.values()))
        route = f"/ws/cc-interactive/events/{self._service_id}"
        self._route_path = route
        listener.register_route(
            "GET", route, self._service_id, callback=None,
            ws_handler=self._handle_ws)
        self._connection = listener
        with self._instances_lock:
            self._instances[self._service_id] = self
        self._initialized = True
        logger.info("CC interactive event service registered at %s", route)

    def disconnect(self):
        if self._connection and self._route_path:
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception:
                logger.debug("CC interactive event route unregister failed", exc_info=True)
        with self._instances_lock:
            self._instances.pop(self._service_id, None)
        with self._sessions_lock:
            self._sessions.clear()
        self._connection = None
        self._route_path = ""
        self._initialized = False

    def register_session(self, session_token: str) -> CCInteractiveSessionEvents:
        if not session_token:
            raise ValueError("session_token is required")
        with self._sessions_lock:
            state = self._sessions.get(session_token)
            if state is None:
                state = CCInteractiveSessionEvents(
                    session_token=session_token,
                    events=queue.Queue(maxsize=self._max_queue),
                )
                self._sessions[session_token] = state
            return state

    def unregister_session(self, session_token: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(session_token, None)

    def session_state(self, session_token: str) -> Optional[CCInteractiveSessionEvents]:
        with self._sessions_lock:
            return self._sessions.get(session_token)

    def wait_event(self, session_token: str, timeout: Optional[float] = None) -> dict:
        state = self.session_state(session_token)
        if state is None:
            raise RuntimeError("Unknown CC interactive session")
        if state.unreliable:
            raise RuntimeError(state.error or "CC interactive session is unreliable")
        try:
            event = state.events.get(timeout=timeout)
        except queue.Empty:
            return {}
        if state.unreliable:
            raise RuntimeError(state.error or "CC interactive session is unreliable")
        return event

    def drain_session(self, session_token: str) -> int:
        state = self.session_state(session_token)
        if state is None:
            return 0
        drained = 0
        while True:
            try:
                state.events.get_nowait()
                drained += 1
            except queue.Empty:
                return drained

    def publish_event(self, session_token: str, event: dict, *, block: bool = True) -> None:
        state = self.session_state(session_token)
        if state is None:
            raise RuntimeError("Unknown CC interactive session")
        if state.unreliable:
            raise RuntimeError(state.error or "CC interactive session is unreliable")
        event.setdefault("session_token", session_token)
        event.setdefault("timestamp", time.time())
        state.last_event_at = time.time()
        try:
            state.events.put(event, block=block, timeout=5 if block else 0)
        except queue.Full as exc:
            state.unreliable = True
            state.error = "CC interactive event queue overflow"
            raise RuntimeError(state.error) from exc

    def _handle_ws(self, sock, path_params, meta):
        from services.filesystem_service import _attach_sync_sock_to_loop
        remote = meta.get("remote_addr", "?")
        try:
            loop = asyncio.new_event_loop()
            try:
                reader, writer = _attach_sync_sock_to_loop(sock, loop)
                loop.run_until_complete(self._serve(reader, writer, remote))
            finally:
                loop.close()
        except Exception:
            logger.error("CC interactive event WS handler failed (%s)", remote, exc_info=True)

    async def _serve(self, reader, writer, remote: str):
        from services.filesystem_service import _ws_recv_frame, _ws_send_frame

        session_token = ""
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode("utf-8"))
            if reg.get("type") != "register":
                return
            token = reg.get("token", "")
            if not token or token != self.config.get("token", ""):
                await _ws_send_frame(writer, json.dumps({
                    "type": "error", "message": "Token mismatch"}).encode())
                return
            session_token = reg.get("session_token", "")
            client_kind = reg.get("client_kind", "proxy")
            state = self.register_session(session_token)
            if client_kind == "proxy":
                state.container_id = reg.get("container_id", "")
                state.connected = True
            await _ws_send_frame(writer, json.dumps({"type": "registered"}).encode())
            logger.info(
                "CC interactive event proxy connected: session=%s container=%s addr=%s",
                session_token[:8], state.container_id, remote)

            while True:
                opcode, payload = await _ws_recv_frame(reader)
                if opcode == 0x08:
                    break
                if opcode == 0x09:
                    await _ws_send_frame(writer, payload, opcode=0x0A)
                    continue
                if opcode != 0x01:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("type") == "ping":
                    await _ws_send_frame(writer, json.dumps({"type": "pong"}).encode())
                    continue
                if msg.get("type") != "event":
                    continue
                event = msg.get("event") or {}
                if not isinstance(event, dict):
                    continue
                try:
                    self.publish_event(session_token, event, block=True)
                except Exception as exc:
                    await _ws_send_frame(writer, json.dumps({
                        "type": "error", "message": str(exc)}).encode())
                    break
        finally:
            if session_token:
                state = self.session_state(session_token)
                if state and locals().get("client_kind", "proxy") == "proxy":
                    state.connected = False
            try:
                writer.close()
            except Exception:
                pass


def get_or_create_cc_interactive_event_service() -> tuple[str, str, CCInteractiveEventService]:
    """Return ``(wss_url, token, service)`` for the shared event service."""
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
    from services.http_listener_service import HTTPListenerService

    instances = HTTPListenerService.all_instances()
    if not instances:
        raise RuntimeError("No HTTPListenerService running for CC interactive events")
    main_port = next(iter(instances.keys()))
    service_id = "_cc_interactive_events"
    reg = ServiceRegistry.get_instance()

    for sdef in reg.resolve_by_type(CCInteractiveEventService.TYPE):
        svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
        cfg = getattr(sdef, "config", {}) or {}
        token = cfg.get("token", "")
        if svc and token:
            url = f"wss://localhost:{main_port}/ws/cc-interactive/events/{sdef.service_id}"
            return url, token, svc
        try:
            reg.uninstall(sdef.scope, sdef.scope_id, sdef.service_id)
        except Exception:
            pass

    token = uuid.uuid4().hex
    reg.install(SCOPE_GLOBAL, "", service_id=service_id,
                service_type=CCInteractiveEventService.TYPE,
                config={"token": token, "_service_id": service_id},
                description="Auto-created event ingest for claude-code-interactive")
    svc = reg.get_live_instance(SCOPE_GLOBAL, "", service_id)
    if not svc:
        raise RuntimeError("CC interactive event service did not start")
    url = f"wss://localhost:{main_port}/ws/cc-interactive/events/{service_id}"
    return url, token, svc


ServiceFactory.register(CCInteractiveEventService)
