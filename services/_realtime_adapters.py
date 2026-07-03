"""Realtime voice protocol adapters.

A realtime voice model is a speech-to-speech LLM living inside a
bidirectional provider session (WSS). Providers disagree on wire protocol
but converge on the same event stream shape, so the session bridge
(`services/_realtime_bridge.py`) consumes only NORMALIZED events produced
here:

    {"type": "audio", "data": bytes}                 agent speech chunk (PCM16)
    {"type": "transcript_user", "text": str, "final": bool}
    {"type": "transcript_agent", "text": str, "final": bool}
    {"type": "speech_started"}                        provider VAD: user speaks
    {"type": "response_done", "usage": dict}
    {"type": "tool_call", "call_id": str, "name": str, "arguments": str}
    {"type": "error", "message": str, "fatal": bool}

The WSS client is hand-rolled (TLS + RFC 6455 client handshake with masked
frames), mirroring `pawflow_relay/_relay_conn.py` — no new dependency.
"""

import base64
import json
import logging
import os
import socket
import ssl
import struct
import threading
import urllib.parse

logger = logging.getLogger(__name__)

# Sanity cap on a single WS message (frame or reassembled fragments): a
# corrupted/hostile length field must not grow the buffer without bound.
# The largest legitimate payloads are base64 audio deltas — a few 100 KiB.
_WS_MAX_MESSAGE_BYTES = 32 * 1024 * 1024


# ── Minimal RFC 6455 client ─────────────────────────────────────────

class RealtimeWSClient:
    """Blocking WebSocket client over TLS for provider realtime sessions."""

    def __init__(self, url: str, headers: dict):
        self._url = url
        self._headers = dict(headers or {})
        self._sock = None
        self._send_lock = threading.Lock()
        # In-flight fragmented message (RFC 6455 §5.4): first-frame opcode
        # and accumulated payload, preserved across recv_frame timeouts.
        self._frag_opcode = 0
        self._frag = bytearray()

    def connect(self, timeout: float = 15.0):
        parsed = urllib.parse.urlparse(self._url)
        if parsed.scheme not in ("wss", "ws"):
            raise ValueError(f"Realtime URL must be ws(s)://, got {self._url}")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        sock = socket.create_connection((host, port), timeout=timeout)
        # Any failure past this point (TLS, send, rejected/garbled handshake)
        # must close the socket — connect() raising leaves no owner for it.
        try:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            if parsed.scheme == "wss":
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)
            ws_key = base64.b64encode(os.urandom(16)).decode()
            lines = [
                f"GET {path} HTTP/1.1",
                f"Host: {host}:{port}" if port not in (80, 443) else f"Host: {host}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {ws_key}",
                "Sec-WebSocket-Version: 13",
            ]
            for k, v in self._headers.items():
                lines.append(f"{k}: {v}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Realtime WS handshake failed (EOF)")
                resp += chunk
            status = resp.split(b"\r\n", 1)[0]
            if b"101" not in status:
                body_preview = resp[:400].decode("latin-1", errors="replace")
                raise ConnectionError(
                    f"Realtime WS handshake rejected: {body_preview}")
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise
        # Bytes after the 101 headers are the start of the first frame.
        leftover = resp[resp.index(b"\r\n\r\n") + 4:]
        self._sock = sock
        self._rxbuf = bytearray(leftover)
        return self

    # -- frame I/O ----------------------------------------------------

    def _try_parse_frame(self):
        """Parse ONE complete frame → (fin, opcode, payload), or None if
        incomplete.

        Nothing is consumed until the whole frame is buffered — a timeout
        mid-frame must leave the stream intact. (Consuming the header and
        then timing out on the payload desynchronizes the stream: the next
        read would parse payload bytes as a header.)
        """
        buf = self._rxbuf
        if len(buf) < 2:
            return None
        fin = bool(buf[0] & 0x80)
        opcode = buf[0] & 0x0F
        length = buf[1] & 0x7F
        off = 2
        if length == 126:
            if len(buf) < 4:
                return None
            length = struct.unpack("!H", bytes(buf[2:4]))[0]
            off = 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length = struct.unpack("!Q", bytes(buf[2:10]))[0]
            off = 10
        if length > _WS_MAX_MESSAGE_BYTES:
            raise ConnectionError(
                f"Realtime WS frame too large ({length} bytes)")
        # Server frames are unmasked (RFC 6455 §5.1) — no mask key bytes.
        if len(buf) < off + length:
            return None
        payload = bytes(buf[off:off + length])
        del buf[:off + length]
        return fin, opcode, payload

    def recv_frame(self, timeout: float = None):
        """Return (opcode, payload) for one complete MESSAGE — fragmented
        data frames are reassembled (RFC 6455 §5.4), control frames pass
        through immediately (they may interleave with fragments and never
        fragment themselves, §5.5). Returns (None, b"") on EOF; raises
        socket.timeout with any partial frame/message preserved."""
        if self._sock is None:
            return None, b""
        try:
            self._sock.settimeout(timeout)
            while True:
                frame = self._try_parse_frame()
                if frame is not None:
                    fin, opcode, payload = frame
                    if opcode >= 0x8:
                        return opcode, payload
                    if opcode:  # text/binary: first (or only) fragment
                        self._frag_opcode = opcode
                        self._frag = bytearray(payload)
                    elif not self._frag_opcode:
                        continue  # stray continuation — drop
                    else:
                        self._frag.extend(payload)
                        if len(self._frag) > _WS_MAX_MESSAGE_BYTES:
                            raise ConnectionError(
                                "Realtime WS message too large")
                    if fin:
                        opcode = self._frag_opcode
                        payload = bytes(self._frag)
                        self._frag_opcode = 0
                        self._frag = bytearray()
                        return opcode, payload
                    continue
                chunk = self._sock.recv(65536)
                if not chunk:
                    return None, b""
                self._rxbuf.extend(chunk)
        except (socket.timeout, ConnectionError):
            raise
        except (OSError, ValueError):
            return None, b""

    def _send_frame(self, opcode: int, payload: bytes):
        if self._sock is None:
            raise ConnectionError("Realtime WS not connected")
        mask = os.urandom(4)
        hdr = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr.extend(struct.pack("!H", n))
        else:
            hdr.append(0x80 | 127)
            hdr.extend(struct.pack("!Q", n))
        hdr.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self._sock.sendall(bytes(hdr) + masked)

    def send_text(self, text: str):
        self._send_frame(0x1, text.encode("utf-8"))

    def send_pong(self, payload: bytes = b""):
        self._send_frame(0xA, payload)

    def close(self):
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            payload = struct.pack("!H", 1000)
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            sock.sendall(bytes(bytearray([0x88, 0x80 | len(payload)]) + mask) + masked)
        except Exception:
            logger.debug("Realtime WS close frame failed", exc_info=True)
        try:
            sock.close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)


# ── Adapter interface ───────────────────────────────────────────────

class RealtimeAdapter:
    """Provider-agnostic realtime session. One instance per session."""

    def connect(self, *, model: str, voice: str, instructions: str,
                tools: list, vad: str, input_format: str,
                output_format: str, resume_handle: str = "") -> None:
        """`resume_handle` resumes a previous provider session when the
        protocol supports it (Gemini Live); adapters without resumption
        ignore it."""
        raise NotImplementedError

    def resumption_state(self) -> str:
        """Opaque provider resumption handle, or "" when the protocol has
        none / no handle was issued yet. The bridge uses it to reconnect
        transparently after a provider-side disconnect."""
        return ""

    def send_audio(self, pcm_chunk: bytes) -> None:
        raise NotImplementedError

    def commit_input(self) -> None:
        """Manual-VAD end of user turn."""
        raise NotImplementedError

    def send_tool_result(self, call_id: str, result: str) -> None:
        raise NotImplementedError

    def interrupt(self) -> None:
        """Barge-in: cancel the in-flight agent response."""
        raise NotImplementedError

    def inject_context(self, text: str) -> None:
        """Add out-of-band context (delegated tool result, note) to the
        session and prompt the model to speak about it."""
        raise NotImplementedError

    def recv_event(self, timeout: float = 1.0):
        """Next normalized event dict, None on timeout, or raises
        ConnectionError when the provider socket is gone."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class OpenAIRealtimeAdapter(RealtimeAdapter):
    """OpenAI Realtime API protocol (also Azure OpenAI + compatibles).

    Wire protocol: JSON events over WSS. Audio is base64 PCM16.
    The `base_url` of the backing llmConnection selects the endpoint:
    `https://api.openai.com/v1` → `wss://api.openai.com/v1/realtime?model=…`.
    """

    def __init__(self, base_url: str, api_key: str,
                 transcription_model: str = "whisper-1",
                 extra_headers: dict = None):
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._api_key = api_key or ""
        self._transcription_model = transcription_model or "whisper-1"
        self._extra_headers = dict(extra_headers or {})
        self._ws = None
        self._vad = "server"
        # The provider rejects `response.create` while a response is active
        # (send_tool_result / inject_context race against the function-call
        # response's own lifecycle) and the rejected create is simply lost —
        # the agent would never speak the tool result. Track the active
        # response from provider events and defer creates until it is done.
        # Guarded by _resp_lock: sends come from tool threads, events from
        # the pump thread.
        self._resp_lock = threading.Lock()
        self._response_active = False
        self._response_pending = False

    # -- helpers -------------------------------------------------------

    def _realtime_url(self, model: str) -> str:
        parsed = urllib.parse.urlparse(self._base_url)
        scheme = "wss" if parsed.scheme in ("https", "wss", "") else "ws"
        netloc = parsed.netloc or parsed.path  # bare host in path if no scheme
        path = parsed.path if parsed.netloc else ""
        base_path = (path or "").rstrip("/")
        if not base_path:
            base_path = "/v1"
        return (f"{scheme}://{netloc}{base_path}/realtime?"
                + urllib.parse.urlencode({"model": model}))

    def _send_json(self, obj: dict):
        self._ws.send_text(json.dumps(obj))

    def _create_response(self):
        """`response.create`, serialized against the active response.

        A create sent while a response is active is rejected by the
        provider (benign error) and silently lost; deferring it until the
        `response.done` of the active response keeps the follow-up spoken
        response alive.
        """
        with self._resp_lock:
            if self._response_active:
                self._response_pending = True
                return
        self._send_json({"type": "response.create"})

    # -- RealtimeAdapter -----------------------------------------------

    def connect(self, *, model, voice, instructions, tools, vad,
                input_format, output_format, resume_handle=""):
        # resume_handle ignored: OpenAI realtime has no session resumption.
        self._vad = vad or "server"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        headers.update(self._extra_headers)
        self._ws = RealtimeWSClient(self._realtime_url(model), headers).connect()
        session = {
            "modalities": ["audio", "text"],
            "voice": voice or "alloy",
            "instructions": instructions or "",
            "input_audio_format": input_format or "pcm16",
            "output_audio_format": output_format or "pcm16",
            "input_audio_transcription": {"model": self._transcription_model},
            "turn_detection": ({"type": "server_vad"}
                               if self._vad == "server" else None),
        }
        if tools:
            session["tools"] = tools
            session["tool_choice"] = "auto"
        self._send_json({"type": "session.update", "session": session})

    def send_audio(self, pcm_chunk: bytes):
        self._send_json({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm_chunk).decode("ascii"),
        })

    def commit_input(self):
        self._send_json({"type": "input_audio_buffer.commit"})
        self._create_response()

    def send_tool_result(self, call_id: str, result: str):
        self._send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": result,
            },
        })
        self._create_response()

    def interrupt(self):
        self._send_json({"type": "response.cancel"})

    def inject_context(self, text: str):
        self._send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": text}],
            },
        })
        self._create_response()

    def close(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()

    def recv_event(self, timeout: float = 1.0):
        if self._ws is None:
            raise ConnectionError("Realtime session closed")
        try:
            opcode, payload = self._ws.recv_frame(timeout=timeout)
        except socket.timeout:
            return None
        if opcode is None:
            raise ConnectionError("Realtime provider socket closed")
        if opcode == 0x9:  # ping
            try:
                self._ws.send_pong(payload)
            except Exception:
                logger.debug("Realtime pong failed", exc_info=True)
            return None
        if opcode == 0x8:  # close
            raise ConnectionError("Realtime provider sent close")
        if opcode != 0x1:  # only text frames carry protocol events
            return None
        try:
            evt = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.debug("Realtime: unparseable event frame (%d bytes)",
                         len(payload))
            return None
        return self._normalize(evt)

    # -- protocol → normalized events -----------------------------------

    def _normalize(self, evt: dict):
        etype = evt.get("type", "")
        # Audio out — GA name and the earlier preview name.
        if etype in ("response.output_audio.delta", "response.audio.delta"):
            try:
                return {"type": "audio",
                        "data": base64.b64decode(evt.get("delta", "") or "")}
            except (ValueError, TypeError):
                return None
        if etype in ("response.output_audio_transcript.delta",
                     "response.audio_transcript.delta"):
            return {"type": "transcript_agent",
                    "text": evt.get("delta", "") or "", "final": False}
        if etype in ("response.output_audio_transcript.done",
                     "response.audio_transcript.done"):
            return {"type": "transcript_agent",
                    "text": evt.get("transcript", "") or "", "final": True}
        if etype == "conversation.item.input_audio_transcription.completed":
            return {"type": "transcript_user",
                    "text": evt.get("transcript", "") or "", "final": True}
        if etype == "conversation.item.input_audio_transcription.delta":
            return {"type": "transcript_user",
                    "text": evt.get("delta", "") or "", "final": False}
        if etype == "input_audio_buffer.speech_started":
            return {"type": "speech_started"}
        if etype == "response.function_call_arguments.done":
            return {"type": "tool_call",
                    "call_id": evt.get("call_id", "") or "",
                    "name": evt.get("name", "") or "",
                    "arguments": evt.get("arguments", "") or ""}
        if etype == "response.created":
            with self._resp_lock:
                self._response_active = True
            return None
        if etype == "response.done":
            with self._resp_lock:
                self._response_active = False
                pending, self._response_pending = self._response_pending, False
            if pending:
                # A create deferred while this response was active — send it
                # now so the follow-up spoken response actually happens.
                try:
                    self._send_json({"type": "response.create"})
                except Exception:
                    logger.debug("Realtime: deferred response.create failed",
                                 exc_info=True)
            usage = {}
            try:
                usage = (evt.get("response") or {}).get("usage") or {}
            except AttributeError:
                usage = {}
            return {"type": "response_done", "usage": usage}
        if etype == "error":
            err = evt.get("error") or {}
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            # Benign races: cancel without an active response (barge-in
            # timing), and response.create colliding with a response that is
            # still active (send_tool_result / inject_context each trigger
            # one — the created item stays in the conversation and is picked
            # up on the next turn, so the session must survive).
            code = err.get("code", "") if isinstance(err, dict) else ""
            fatal = code not in ("response_cancel_not_active",
                                 "conversation_already_has_active_response")
            return {"type": "error", "message": msg or "provider error",
                    "fatal": fatal}
        return None  # session.created / rate_limits / deltas we don't consume


def build_adapter(protocol: str, *, base_url: str, api_key: str,
                  transcription_model: str = "whisper-1",
                  extra_headers: dict = None) -> RealtimeAdapter:
    """Adapter factory — the multi-provider seam."""
    proto = (protocol or "openai_realtime").strip().lower()
    if proto == "openai_realtime":
        return OpenAIRealtimeAdapter(
            base_url, api_key, transcription_model=transcription_model,
            extra_headers=extra_headers)
    if proto == "gemini_live":
        # Imported lazily: _realtime_gemini imports RealtimeAdapter/WSClient
        # from this module.
        from services._realtime_gemini import GeminiLiveAdapter
        return GeminiLiveAdapter(base_url, api_key)
    raise ValueError(
        f"Unknown realtime protocol '{protocol}'. "
        "Supported: openai_realtime, gemini_live")
