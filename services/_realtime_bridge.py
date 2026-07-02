"""Realtime voice session bridge — browser WS ⇄ provider realtime session.

One `RealtimeSessionBridge` per accepted `/ws/realtime/{conversation_id}`
connection (threaded, blocking sockets — same model as
`services/audio_proxy.py`):

  pump A (handler thread): browser frames → adapter
      binary  = mic PCM16 chunks → adapter.send_audio
      text    = JSON control: {"type": "commit"|"interrupt"|"stop"}
  pump B (worker thread):  adapter.recv_event() → browser + side effects
      audio            → binary downlink
      speech_started   → adapter.interrupt() + event (client flushes ring)
      final transcripts→ ConversationStore via ConversationWriter (persisted
                         messages, visible to every attached client)
      response_done    → usage event
      error / caps     → error/closed event + teardown

Auth arrives resolved from the HTTP listener's WS path (`meta.auth_user_id`).
Force-stop kills the session and never poisons the next one.
"""

import json
import logging
import threading
import time
import urllib.parse

from services.audio_proxy import _ws_recv, _ws_send_binary, _ws_close

logger = logging.getLogger(__name__)

# conversation_id -> RealtimeSessionBridge (single active voice session per
# conversation; force-stop and duplicate-open both go through this).
_active_bridges = {}
_bridges_lock = threading.Lock()


def _ws_send_text(sock, text: str):
    """Send an unmasked server text frame (mirror of _ws_send_binary)."""
    import struct
    data = text.encode("utf-8")
    hdr = bytearray([0x81])
    if len(data) < 126:
        hdr.append(len(data))
    elif len(data) < 65536:
        hdr.append(126)
        hdr.extend(struct.pack("!H", len(data)))
    else:
        hdr.append(127)
        hdr.extend(struct.pack("!Q", len(data)))
    sock.sendall(bytes(hdr) + data)


def stop_realtime_session(conversation_id: str) -> bool:
    """Force-stop the active voice session of a conversation (if any)."""
    with _bridges_lock:
        bridge = _active_bridges.get(conversation_id)
    if bridge is None:
        return False
    bridge.stop("force_stop")
    return True


class RealtimeSessionBridge:
    def __init__(self, sock, conversation_id: str, agent_name: str,
                 user_id: str, service):
        self._sock = sock
        self._cid = conversation_id
        self._agent = agent_name
        self._user_id = user_id
        self._service = service
        self._adapter = None
        self._stop_ev = threading.Event()
        self._stop_reason = ""
        self._send_lock = threading.Lock()
        self._started_at = time.monotonic()
        self._agent_text_parts = []

    # -- client frame helpers -------------------------------------------

    def _emit(self, obj: dict):
        try:
            with self._send_lock:
                _ws_send_text(self._sock, json.dumps(obj))
        except OSError:
            self._stop_ev.set()

    def _emit_audio(self, data: bytes):
        try:
            with self._send_lock:
                _ws_send_binary(self._sock, data)
        except OSError:
            self._stop_ev.set()

    # -- transcript persistence ------------------------------------------

    def _persist(self, role: str, text: str):
        """Persist one final transcript as a normal conversation message.

        Messages carry msg_id + ts at creation (store convention) and are
        routed/published by ConversationWriter, so every attached client
        (webchat SSE, Telegram bridge, PawCode) sees the voice exchange and
        the text agent resumes seamlessly after the session.
        """
        text = (text or "").strip()
        if not text:
            return
        try:
            from core.llm_client import LLMMessage
            from core.conversation_writer import ConversationWriter
            if role == "user":
                source = {"type": "user", "name": self._user_id or "user",
                          "target_agent": self._agent, "channel": "voice"}
            else:
                source = {"type": "agent", "name": self._agent,
                          "channel": "voice"}
            msg = LLMMessage(role=role, content=text, source=source,
                             conversation_id=self._cid)
            store_msg = {
                "role": role,
                "content": text,
                "source": source,
                "msg_id": msg.msg_id,
                "ts": msg.timestamp,
                "seq": None,
            }
            ConversationWriter.for_conversation(self._cid).enqueue_message(
                store_msg,
                agent_name=self._agent if role == "assistant" else "",
                user_id=self._user_id,
                sse_events=[{"type": "new_message", "data": {
                    "role": role,
                    "content": text,
                    "msg_id": msg.msg_id,
                    "ts": msg.timestamp,
                    "source": source,
                    "agent_name": self._agent if role == "assistant" else "",
                }}],
            )
        except Exception:
            logger.error("[realtime] transcript persistence failed for %s",
                         self._cid[:8], exc_info=True)

    # -- lifecycle ----------------------------------------------------------

    def _instructions(self) -> str:
        if getattr(self._service, "instructions_mode", "agent") == "custom":
            return getattr(self._service, "instructions", "") or ""
        # agent mode: best-effort reuse of the conversation agent's prompt.
        prompt = ""
        try:
            from core.conv_agent_config import get_agent_config
            cfg = get_agent_config(self._cid, self._agent) or {}
            for key in ("system_prompt", "systemPrompt", "prompt",
                        "instructions"):
                if cfg.get(key):
                    prompt = str(cfg[key])
                    break
        except Exception:
            logger.debug("[realtime] agent prompt lookup failed",
                         exc_info=True)
        if not prompt:
            prompt = (f"You are {self._agent or 'the assistant'}, a helpful "
                      "voice assistant. Keep spoken answers concise.")
        return prompt

    def run(self):
        """Blocking session loop; owns the browser socket until teardown."""
        max_seconds = int(getattr(self._service, "max_session_seconds", 600)
                          or 600)
        try:
            self._adapter = self._service.open_session(
                instructions=self._instructions())
        except Exception as exc:
            logger.warning("[realtime] session open failed for %s: %s",
                           self._cid[:8], exc)
            self._emit({"type": "error", "message": str(exc)})
            self._emit({"type": "closed", "reason": "open_failed"})
            return

        # `ready` goes out before the provider pump starts so the client
        # never sees session events ahead of the ready handshake.
        self._emit({"type": "ready", "state": "listening"})
        pump = threading.Thread(target=self._provider_pump,
                                name=f"realtime-pump-{self._cid[:8]}",
                                daemon=True)
        pump.start()
        deadline = self._started_at + max_seconds
        try:
            while not self._stop_ev.is_set():
                if time.monotonic() > deadline:
                    self.stop("max_session_seconds")
                    break
                opcode, payload = _ws_recv(self._sock)
                if opcode is None or opcode == 0x8:
                    self.stop("client_closed")
                    break
                if opcode == 0x2 and payload:
                    try:
                        self._adapter.send_audio(payload)
                    except Exception:
                        self.stop("provider_send_failed")
                        break
                elif opcode == 0x1:
                    self._handle_control(payload)
        finally:
            self._teardown(pump)

    def _handle_control(self, payload: bytes):
        try:
            ctl = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        ctype = ctl.get("type", "")
        try:
            if ctype == "commit":
                self._adapter.commit_input()
            elif ctype == "interrupt":
                self._adapter.interrupt()
                self._emit({"type": "state", "state": "listening"})
            elif ctype == "stop":
                self.stop("client_stop")
        except Exception:
            logger.debug("[realtime] control %s failed", ctype, exc_info=True)

    def _provider_pump(self):
        while not self._stop_ev.is_set():
            try:
                evt = self._adapter.recv_event(timeout=1.0)
            except ConnectionError:
                self.stop("provider_closed")
                break
            except Exception:
                logger.debug("[realtime] provider recv failed", exc_info=True)
                self.stop("provider_error")
                break
            if evt is None:
                continue
            etype = evt.get("type")
            if etype == "audio":
                if evt.get("data"):
                    self._emit_audio(evt["data"])
            elif etype == "speech_started":
                # Barge-in: cancel the in-flight response, tell the client
                # to flush its playback ring buffer.
                try:
                    self._adapter.interrupt()
                except Exception:
                    logger.debug("[realtime] interrupt failed", exc_info=True)
                self._emit({"type": "speech_started"})
                self._emit({"type": "state", "state": "listening"})
            elif etype == "transcript_user":
                self._emit({"type": "transcript_user",
                            "text": evt.get("text", ""),
                            "final": bool(evt.get("final"))})
                if evt.get("final"):
                    self._persist("user", evt.get("text", ""))
                    self._emit({"type": "state", "state": "thinking"})
            elif etype == "transcript_agent":
                self._emit({"type": "transcript_agent",
                            "text": evt.get("text", ""),
                            "final": bool(evt.get("final"))})
                if evt.get("final"):
                    self._persist("assistant", evt.get("text", ""))
                else:
                    self._emit({"type": "state", "state": "speaking"})
            elif etype == "response_done":
                self._emit({"type": "usage",
                            "usage": evt.get("usage") or {}})
                self._emit({"type": "state", "state": "listening"})
            elif etype == "tool_call":
                # P1 exposes no tools; a stray provider call gets an
                # explicit refusal so the session keeps flowing.
                try:
                    self._adapter.send_tool_result(
                        evt.get("call_id", ""),
                        "Tool execution is not enabled for this voice session.")
                except Exception:
                    logger.debug("[realtime] tool refusal failed",
                                 exc_info=True)
            elif etype == "error":
                self._emit({"type": "error",
                            "message": evt.get("message", "provider error")})
                if evt.get("fatal"):
                    self.stop("provider_error")
                    break

    def stop(self, reason: str):
        if not self._stop_ev.is_set():
            self._stop_reason = reason
            self._stop_ev.set()
            # The handler thread may be blocked reading the browser socket;
            # shutting down the read side unblocks it immediately while the
            # write side stays open for the final `closed` frame.
            try:
                import socket as _socket
                self._sock.shutdown(_socket.SHUT_RD)
            except OSError:
                pass

    def _teardown(self, pump: threading.Thread):
        try:
            if self._adapter is not None:
                self._adapter.close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        try:
            pump.join(timeout=3)
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        self._emit({"type": "closed",
                    "reason": self._stop_reason or "ended"})
        try:
            _ws_close(self._sock, 1000, self._stop_reason or "ended")
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        with _bridges_lock:
            if _active_bridges.get(self._cid) is self:
                del _active_bridges[self._cid]
        logger.info("[realtime] session ended cid=%s reason=%s dur=%.1fs",
                    self._cid[:8], self._stop_reason or "ended",
                    time.monotonic() - self._started_at)


def realtime_ws_handler(sock, path_params: dict, meta: dict):
    """WS handler for GET /ws/realtime/{conversation_id}.

    Session auth + private gateway already ran in the HTTP listener's WS
    path; `meta["auth_user_id"]` carries the resolved identity.
    """
    conversation_id = (path_params or {}).get("conversation_id", "")
    query = urllib.parse.parse_qs(meta.get("query", "") or "")
    service_id = (query.get("service", [""])[0] or "").strip()
    agent_name = (query.get("agent", [""])[0] or "").strip()
    user_id = meta.get("auth_user_id", "") or ""

    def _reject(message: str):
        try:
            _ws_send_text(sock, json.dumps({"type": "error",
                                            "message": message}))
            _ws_close(sock, 1008, "rejected")
        except Exception:
            logger.debug("Ignored exception", exc_info=True)

    if not conversation_id or not service_id or not agent_name:
        _reject("conversation_id, service and agent are required")
        return

    # Conversation ownership: an authenticated user may only voice-attach
    # to their own conversations (admins may attach to any).
    try:
        from core.flow_runtime_access import conversation_owner
        owner = conversation_owner(conversation_id)
        role = (meta.get("auth_role", "") or "").lower()
        if owner and owner != user_id and role != "admin":
            _reject("not your conversation")
            return
    except Exception:
        logger.warning("[realtime] ownership check failed", exc_info=True)
        _reject("conversation access check failed")
        return

    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(service_id, user_id=user_id,
                                         conv_id=conversation_id)
        if svc_def is None or getattr(svc_def, "service_type", "") != \
                "realtimeVoiceConnection":
            _reject(f"'{service_id}' is not a realtimeVoiceConnection service")
            return
        service = reg.resolve(service_id, user_id=user_id,
                              conv_id=conversation_id)
        if service is None:
            _reject(f"realtime service '{service_id}' could not connect")
            return
        if hasattr(service, "set_runtime_context"):
            service.set_runtime_context(user_id=user_id,
                                        conversation_id=conversation_id,
                                        agent_name=agent_name)
    except Exception as exc:
        logger.warning("[realtime] service resolution failed: %s", exc)
        _reject(str(exc))
        return

    bridge = RealtimeSessionBridge(sock, conversation_id, agent_name,
                                   user_id, service)
    with _bridges_lock:
        previous = _active_bridges.get(conversation_id)
        _active_bridges[conversation_id] = bridge
    if previous is not None:
        # One active voice session per conversation — the newcomer wins.
        previous.stop("superseded")
    logger.info("[realtime] session start cid=%s agent=%s service=%s user=%s",
                conversation_id[:8], agent_name, service_id, user_id or "?")
    bridge.run()


def register_realtime_route(http_service) -> None:
    """Idempotently register the realtime WS route on a live listener."""
    existing = [r for r in http_service.get_routes()
                if r.get("pattern", "").startswith("/ws/realtime/")]
    if existing:
        return
    http_service.register_route(
        "GET", "/ws/realtime/{conversation_id}", "_realtime_voice",
        callback=None, ws_handler=realtime_ws_handler)
    logger.info("[realtime] /ws/realtime route registered")
