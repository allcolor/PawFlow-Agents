"""Claude Code interactive MITM event ingest service.

The proxy inside the Claude Code container observes Anthropic SSE bytes and
posts scrubbed copies here over WebSocket. Providers consume per-session
queues; if a queue fills, the session is marked unreliable instead of
silently dropping events.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

_SENSITIVE_HEADER_RE = re.compile(
    rb"(?im)^(authorization|cookie|proxy-authorization|set-cookie|x-api-key|anthropic-api-key):[^\r\n]*"
)


def _redact_wire_bytes(data: bytes) -> bytes:
    return _SENSITIVE_HEADER_RE.sub(
        lambda match: match.group(1) + b": <redacted>", data)


def _safe_wire_field(data_b64: str, text_repr: str) -> tuple[str, str]:
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return "<invalid-base64>", "<invalid-base64>"
    redacted = _redact_wire_bytes(raw)
    return (
        base64.b64encode(redacted).decode("ascii"),
        repr(redacted.decode("utf-8", errors="replace")),
    )


@dataclass
class CCInteractiveSessionEvents:
    session_token: str
    events: "queue.Queue[dict]"
    container_id: str = ""
    user_id: str = ""
    conversation_id: str = ""
    agent_name: str = ""
    connected: bool = False
    unreliable: bool = False
    error: str = ""
    manual_capture_active: bool = False
    manual_capture_pending: int = 0
    injected_prompts: dict[str, float] = field(default_factory=dict)
    pending_injected_prompt_ignores: list[float] = field(default_factory=list)
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
        self._service_id = self.config.get("_service_id", "")
        self._connection = None
        self._route_path = ""
        self._sessions: Dict[str, CCInteractiveSessionEvents] = {}
        self._sessions_lock = threading.RLock()
        try:
            self._max_queue = int(self.config.get("max_queue", 4096) or 4096)
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
            ws_handler=self._handle_ws, public=True, private_only=True)
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

    def register_session(self, session_token: str, *, user_id: str = "",
                         conversation_id: str = "",
                         agent_name: str = "") -> CCInteractiveSessionEvents:
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
            if user_id:
                state.user_id = user_id
            if conversation_id:
                state.conversation_id = conversation_id
            if agent_name:
                state.agent_name = agent_name
            return state

    def unregister_session(self, session_token: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(session_token, None)

    def remember_injected_prompt(self, session_token: str, prompt: str) -> None:
        if not session_token or not prompt:
            return
        state = self.register_session(session_token)
        digest = self._prompt_digest(prompt)
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.injected_prompts = {
                key: ts for key, ts in state.injected_prompts.items()
                if ts >= cutoff
            }
            state.injected_prompts[digest] = now
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            state.pending_injected_prompt_ignores.append(now)

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
        self._log_event_summary(session_token, event)
        if event.get("type") == "wire":
            return
        self._maybe_ingest_manual_prompt(state, event)
        try:
            state.events.put(event, block=block, timeout=5 if block else 0)
        except queue.Full as exc:
            state.unreliable = True
            state.error = "CC interactive event queue overflow"
            raise RuntimeError(state.error) from exc

    @staticmethod
    def _log_event_summary(session_token: str, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "sse":
            payload = event.get("payload") or {}
            ptype = payload.get("type") or event.get("event", "")
            if ptype == "content_block_delta":
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                text = delta.get("text", "") if dtype == "text_delta" else ""
                logger.debug(
                    "CC interactive MITM event: session=%s request=%s type=%s delta=%s text_len=%d text_preview=%r",
                    session_token[:8], event.get("request_id", ""), ptype, dtype,
                    len(text), text[:24])
                return
            logger.debug(
                "CC interactive MITM event: session=%s request=%s type=%s payload_keys=%s",
                session_token[:8], event.get("request_id", ""), ptype,
                sorted(payload.keys())[:8])
        elif etype == "request_error":
            logger.warning(
                "CC interactive proxy event: session=%s type=%s request=%s path=%s status=%s ctype=%s encoding=%s reason=%s error=%s",
                session_token[:8], etype, event.get("request_id", ""),
                event.get("path", ""), event.get("status", ""),
                event.get("content_type", ""), event.get("content_encoding", ""),
                event.get("reason", ""), event.get("error", ""))
        elif etype in {"request_start", "request_stop", "response_start", "response_ignored"}:
            logger.debug(
                "CC interactive proxy event: session=%s type=%s request=%s path=%s status=%s ctype=%s encoding=%s reason=%s",
                session_token[:8], etype, event.get("request_id", ""),
                event.get("path", ""), event.get("status", ""),
                event.get("content_type", ""), event.get("content_encoding", ""),
                event.get("reason", ""))
        elif etype == "wire":
            safe_b64, safe_text = _safe_wire_field(
                str(event.get("data_b64", "")), str(event.get("text_repr", "")))
            logger.debug(
                "CC interactive proxy wire: session=%s request=%s direction=%s stage=%s seq=%s bytes=%s sha256=%s data_b64=%s text=%s",
                session_token[:8], event.get("request_id", ""),
                event.get("direction", ""), event.get("stage", ""),
                event.get("seq", ""), event.get("bytes", ""),
                event.get("sha256", ""), safe_b64, safe_text)
        elif etype == "hook":
            logger.info(
                "CC interactive hook event: session=%s hook=%s",
                session_token[:8], event.get("hook_event_name", ""))

    def _maybe_ingest_manual_prompt(self, state: CCInteractiveSessionEvents,
                                    event: dict) -> None:
        if event.get("type") != "hook" or event.get("hook_event_name") != "UserPromptSubmit":
            return
        data = event.get("input") or {}
        if not isinstance(data, dict):
            return
        prompt = data.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        if data.get("pawflow_injected_prompt"):
            self._consume_pending_injected_prompt(state)
            return
        if self._consume_injected_prompt(state, prompt):
            return
        if data.get("pawflow_managed_prompt"):
            return
        if not prompt.strip():
            return
        if not state.conversation_id or not state.agent_name:
            logger.debug("manual CC prompt ignored without session binding")
            return
        try:
            from core.conversation_writer import ConversationWriter
            from core.llm_client import stamp_message
            msg = stamp_message({
                "role": "user",
                "content": prompt,
                "source": {
                    "type": "user",
                    "name": state.user_id,
                    "target_agent": state.agent_name,
                    "input": "cc_interactive_tmux",
                },
                "channel": "tmux",
            }, state.conversation_id)
            ConversationWriter.for_conversation(
                state.conversation_id).enqueue_message(
                    msg, agent_name=state.agent_name, user_id=state.user_id)
            logger.info(
                "CC interactive manual tmux prompt persisted: conv=%s agent=%s msg=%s chars=%d",
                state.conversation_id[:8], state.agent_name, msg.get("msg_id", ""),
                len(prompt))
        except Exception:
            logger.warning("CC interactive manual prompt persist failed", exc_info=True)
            return
        self._start_manual_capture(state)

    @staticmethod
    def _prompt_digest(prompt: str) -> str:
        normalized = (prompt or "").rstrip("\r\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _consume_injected_prompt(self, state: CCInteractiveSessionEvents,
                                 prompt: str) -> bool:
        digest = self._prompt_digest(prompt) if prompt else ""
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.injected_prompts = {
                key: ts for key, ts in state.injected_prompts.items()
                if ts >= cutoff
            }
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            if digest and digest in state.injected_prompts:
                state.injected_prompts.pop(digest, None)
                if state.pending_injected_prompt_ignores:
                    state.pending_injected_prompt_ignores.pop(0)
                return True
            if state.pending_injected_prompt_ignores:
                state.pending_injected_prompt_ignores.pop(0)
                self._pop_oldest_injected_prompt_locked(state)
                return True
            return False

    @staticmethod
    def _pop_oldest_injected_prompt_locked(state: CCInteractiveSessionEvents) -> None:
        if not state.injected_prompts:
            return
        oldest = min(state.injected_prompts, key=state.injected_prompts.get)
        state.injected_prompts.pop(oldest, None)

    def _consume_pending_injected_prompt(self, state: CCInteractiveSessionEvents) -> bool:
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            if not state.pending_injected_prompt_ignores:
                return False
            state.pending_injected_prompt_ignores.pop(0)
            self._pop_oldest_injected_prompt_locked(state)
            return True

    def _start_manual_capture(self, state: CCInteractiveSessionEvents) -> None:
        with self._sessions_lock:
            if state.manual_capture_active:
                state.manual_capture_pending += 1
                return
            state.manual_capture_active = True
        thread = threading.Thread(
            target=self._run_manual_capture,
            args=(state.session_token,),
            name=f"cci-manual-capture-{state.session_token[:8]}",
            daemon=True,
        )
        thread.start()

    def _run_manual_capture(self, session_token: str) -> None:
        state = self.session_state(session_token)
        try:
            if not state:
                return
            from core.llm_client import stamp_message
            from core.conversation_writer import ConversationWriter
            from core.llm_providers.claude_code_interactive import _CCITurnCoordinator
            coord = _CCITurnCoordinator(self, session_token)
            response = coord.run()
            content = response.content or ""
            if not content.strip():
                return
            msg = stamp_message({
                "role": "assistant",
                "content": content,
                "source": {
                    "type": "agent",
                    "name": state.agent_name,
                    "input": "cc_interactive_tmux",
                },
                "channel": "tmux",
            }, state.conversation_id)
            ConversationWriter.for_conversation(
                state.conversation_id).enqueue_message(
                    msg, agent_name=state.agent_name, user_id=state.user_id)
            logger.info(
                "CC interactive manual tmux response persisted: conv=%s agent=%s msg=%s chars=%d",
                state.conversation_id[:8], state.agent_name, msg.get("msg_id", ""),
                len(content))
        except Exception:
            logger.warning("CC interactive manual response capture failed", exc_info=True)
        finally:
            state = self.session_state(session_token)
            if state:
                restart = False
                with self._sessions_lock:
                    if state.manual_capture_pending > 0:
                        state.manual_capture_pending -= 1
                        restart = True
                    else:
                        state.manual_capture_active = False
                if restart:
                    thread = threading.Thread(
                        target=self._run_manual_capture,
                        args=(session_token,),
                        name=f"cci-manual-capture-{session_token[:8]}",
                        daemon=True,
                    )
                    thread.start()

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

        session_token = ""  # nosec B105
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
                "CC interactive event client connected: session=%s kind=%s container=%s addr=%s",
                session_token[:8], client_kind, state.container_id, remote)

            while True:
                try:
                    opcode, payload = await _ws_recv_frame(reader)
                except asyncio.IncompleteReadError:
                    break
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


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
            if not getattr(svc, "_initialized", False) or not getattr(svc, "_route_path", ""):
                svc.connect()
            url = f"wss://localhost:{main_port}/ws/cc-interactive/events/{sdef.service_id}"
            return url, token, svc
        try:
            reg.uninstall(sdef.scope, sdef.scope_id, sdef.service_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
