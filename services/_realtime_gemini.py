"""Gemini Live realtime adapter — `gemini_live` protocol (P3).

Google's Live API (`BidiGenerateContent`) over WSS. Same normalized event
stream as every adapter (see `services/_realtime_adapters.py`); protocol
differences handled here:

- Audio: Gemini takes PCM16 mono **16 kHz** in and produces **24 kHz** out.
  PawFlow's uplink is 24 kHz everywhere (browser capture, Telegram ffmpeg
  decode), so `send_audio` resamples 24k→16k; downlink is 24 kHz already.
- Messages may arrive as TEXT or BINARY WS frames (both carry JSON).
- One server message can yield several normalized events (audio + final
  transcripts + turn end) — extras queue up and `recv_event` drains them.
- Transcriptions stream without a final flag: deltas accumulate here and
  flush as finals on `turnComplete` / `interrupted`.
- Barge-in is server-side (VAD interrupts generation): `interrupt()` is a
  no-op and the `interrupted` signal maps to `speech_started`.
- `sessionResumptionUpdate` handles are captured; `resumption_state()`
  exposes the latest one so the bridge can transparently reconnect.
"""

import base64
import collections
import json
import logging
import socket
import time

from services._realtime_adapters import RealtimeAdapter, RealtimeWSClient

logger = logging.getLogger(__name__)

_GEMINI_HOST = "generativelanguage.googleapis.com"
_BIDI_PATH = ("/ws/google.ai.generativelanguage.v1beta."
              "GenerativeService.BidiGenerateContent")
_IN_RATE = 16000    # Gemini Live input rate
_OUT_RATE = 24000   # Gemini Live output rate == PawFlow downlink rate
_UPLINK_RATE = 24000  # what PawFlow callers send (browser / ffmpeg decode)
_SETUP_TIMEOUT_S = 15.0


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of PCM16 mono little-endian.

    Small chunks on the mic hot path — pure Python, no dependency
    (audioop is removed in Python 3.13).
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    if len(pcm) % 2:
        pcm = pcm[:-1]  # defensive: PCM16 frames are always even
    import array
    src = array.array("h")
    src.frombytes(pcm)
    n_src = len(src)
    if n_src == 0:
        return b""
    n_dst = max(1, int(n_src * dst_rate / src_rate))
    step = n_src / n_dst
    out = array.array("h", bytes(2 * n_dst))
    for i in range(n_dst):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[j]
        b = src[j + 1] if j + 1 < n_src else a
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


class GeminiLiveAdapter(RealtimeAdapter):
    """Gemini Live (BidiGenerateContent) protocol adapter."""

    def __init__(self, base_url: str, api_key: str):
        self._api_key = api_key or ""
        # base_url of the backing llmConnection may point at a proxy; preserve
        # host, port, and path prefix instead of collapsing to hostname only.
        self._scheme = "wss"
        self._netloc = _GEMINI_HOST
        self._base_path = ""
        if base_url:
            import urllib.parse
            parsed = urllib.parse.urlparse(
                base_url if "//" in base_url else "https://" + base_url)
            self._scheme = "wss" if parsed.scheme in ("https", "wss", "") else "ws"
            self._netloc = parsed.netloc or parsed.path or _GEMINI_HOST
            self._base_path = parsed.path.rstrip("/") if parsed.netloc else ""
        self._ws = None
        self._vad = "server"
        self._activity_open = False   # manual-VAD activityStart sent
        self._pending = collections.deque()  # normalized events to drain
        self._user_text = []          # input transcription accumulator
        self._agent_text = []         # output transcription accumulator
        self._usage = {}
        self._resume_handle = ""
        # FunctionResponse requires the function NAME alongside the id; the
        # bridge only carries call_id, so remember the mapping per call.
        self._call_names = {}

    # -- helpers -------------------------------------------------------

    def _url(self) -> str:
        # The key travels in the `x-goog-api-key` handshake header, NOT as
        # a query param — URLs leak (proxy logs, exception messages).
        return f"{self._scheme}://{self._netloc}{self._base_path}{_BIDI_PATH}"

    def _send_json(self, obj: dict):
        if self._ws is None:
            raise ConnectionError("Gemini Live session not connected")
        self._ws.send_text(json.dumps(obj))

    @staticmethod
    def _function_declarations(tools: list) -> list:
        """OpenAI flat tool defs → Gemini functionDeclarations."""
        decls = []
        for t in tools or []:
            decls.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object"},
            })
        return decls

    # -- RealtimeAdapter -----------------------------------------------

    def connect(self, *, model, voice, instructions, tools, vad,
                input_format, output_format, resume_handle=""):
        self._vad = vad or "server"
        self._ws = RealtimeWSClient(
            self._url(), {"x-goog-api-key": self._api_key}).connect()
        model_path = model if model.startswith("models/") \
            else f"models/{model}"
        setup = {
            "model": model_path,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            # Empty config asks the server to start issuing resumption
            # handles; a non-empty handle resumes the previous session.
            "sessionResumption": ({"handle": resume_handle}
                                  if resume_handle else {}),
        }
        if voice:
            setup["generationConfig"]["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
        if instructions:
            setup["systemInstruction"] = {"parts": [{"text": instructions}]}
        if tools:
            setup["tools"] = [
                {"functionDeclarations": self._function_declarations(tools)}]
        if self._vad == "manual":
            setup["realtimeInputConfig"] = {
                "automaticActivityDetection": {"disabled": True}}
        self._send_json({"setup": setup})
        # Fail fast: the server acks with setupComplete (or closes/errors).
        deadline = time.monotonic() + _SETUP_TIMEOUT_S
        while time.monotonic() < deadline:
            msg = self._recv_json(timeout=1.0)
            if msg is None:
                continue
            if "setupComplete" in msg:
                return
            if "error" in msg:
                raise ConnectionError(
                    f"Gemini Live setup rejected: {msg.get('error')}")
            # Anything else this early is unexpected but not fatal — queue
            # its normalized form for the pump.
            self._pending.extend(self._normalize_message(msg))
        raise ConnectionError("Gemini Live setup timed out")

    def send_audio(self, pcm_chunk: bytes):
        if self._vad == "manual" and not self._activity_open:
            self._send_json({"realtimeInput": {"activityStart": {}}})
            self._activity_open = True
        self._send_json({"realtimeInput": {"audio": {
            "mimeType": f"audio/pcm;rate={_IN_RATE}",
            "data": base64.b64encode(
                resample_pcm16(pcm_chunk, _UPLINK_RATE, _IN_RATE)
            ).decode("ascii"),
        }}})

    def commit_input(self):
        if self._vad == "manual":
            if not self._activity_open:
                # Nothing was sent this turn — an empty commit is a no-op.
                return
            self._send_json({"realtimeInput": {"activityEnd": {}}})
            self._activity_open = False
        # Server VAD needs no commit; generation follows detected turn end.

    def send_tool_result(self, call_id: str, result: str):
        self._send_json({"toolResponse": {"functionResponses": [{
            "id": call_id,
            "name": self._call_names.pop(call_id, ""),
            "response": {"output": result},
        }]}})

    def interrupt(self):
        # Gemini Live cancels generation server-side on VAD barge-in; there
        # is no client cancel message. The `interrupted` signal already
        # arrived (that is what triggered the bridge) — nothing to send.
        logger.debug("Gemini Live: interrupt() is server-side (no-op)")

    def inject_context(self, text: str):
        self._send_json({"clientContent": {
            "turns": [{"role": "user", "parts": [{"text": text}]}],
            "turnComplete": True,
        }})

    def close(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()

    def resumption_state(self) -> str:
        return self._resume_handle

    # -- receive / normalize ---------------------------------------------

    def _recv_json(self, timeout: float = 1.0):
        """One raw JSON message from the socket, or None on timeout/noise."""
        if self._ws is None:
            raise ConnectionError("Gemini Live session closed")
        try:
            opcode, payload = self._ws.recv_frame(timeout=timeout)
        except socket.timeout:
            return None
        if opcode is None:
            raise ConnectionError("Gemini Live provider socket closed")
        if opcode == 0x9:  # ping
            try:
                self._ws.send_pong(payload)
            except Exception:
                logger.debug("Gemini Live pong failed", exc_info=True)
            return None
        if opcode == 0x8:  # close
            raise ConnectionError("Gemini Live provider sent close")
        if opcode not in (0x1, 0x2):  # Gemini uses text AND binary frames
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.debug("Gemini Live: unparseable frame (%d bytes)",
                         len(payload))
            return None

    def recv_event(self, timeout: float = 1.0):
        if self._pending:
            return self._pending.popleft()
        msg = self._recv_json(timeout=timeout)
        if msg is None:
            return None
        events = self._normalize_message(msg)
        if not events:
            return None
        first = events[0]
        self._pending.extend(events[1:])
        return first

    def _flush_transcripts(self, events: list):
        """Emit accumulated transcriptions as finals (user before agent —
        the bridge's question→answer ordering expects it)."""
        user = "".join(self._user_text).strip()
        agent = "".join(self._agent_text).strip()
        self._user_text, self._agent_text = [], []
        if user:
            events.append({"type": "transcript_user", "text": user,
                           "final": True})
        if agent:
            events.append({"type": "transcript_agent", "text": agent,
                           "final": True})

    def _normalize_message(self, msg: dict) -> list:
        events = []
        if "usageMetadata" in msg and isinstance(msg["usageMetadata"], dict):
            self._usage = msg["usageMetadata"]
        content = msg.get("serverContent") or {}
        if content:
            it = (content.get("inputTranscription") or {}).get("text", "")
            if it:
                self._user_text.append(it)
                events.append({"type": "transcript_user", "text": it,
                               "final": False})
            ot = (content.get("outputTranscription") or {}).get("text", "")
            if ot:
                self._agent_text.append(ot)
                events.append({"type": "transcript_agent", "text": ot,
                               "final": False})
            parts = (content.get("modelTurn") or {}).get("parts") or []
            for part in parts:
                blob = part.get("inlineData") or {}
                if str(blob.get("mimeType", "")).startswith("audio/pcm"):
                    try:
                        events.append({
                            "type": "audio",
                            "data": base64.b64decode(blob.get("data", "")
                                                     or "")})
                    except (ValueError, TypeError):
                        logger.debug("Gemini Live: bad audio blob")
            if content.get("interrupted"):
                # Barge-in: what the agent DID say persists; the new user
                # utterance is transcribed next.
                self._flush_transcripts(events)
                events.append({"type": "speech_started"})
            if content.get("turnComplete"):
                self._flush_transcripts(events)
                events.append({"type": "response_done",
                               "usage": self._usage})
                self._usage = {}
        tool_call = msg.get("toolCall") or {}
        for fc in tool_call.get("functionCalls") or []:
            call_id = fc.get("id", "") or ""
            name = fc.get("name", "") or ""
            if call_id:
                self._call_names[call_id] = name
            events.append({
                "type": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(fc.get("args") or {}),
            })
            # Normalized-contract emulation: consumers expect each tool_call
            # to come with the function-call response's OWN response_done
            # (OpenAI semantics — the Telegram turn runner counts them to
            # skip past tool turns). Gemini ends the whole spoken turn with
            # a single turnComplete and emits nothing for the tool-call
            # segment, so synthesize the expected done here. To confirm
            # against the live endpoint.
            events.append({"type": "response_done", "usage": {}})
        resumption = msg.get("sessionResumptionUpdate") or {}
        if resumption:
            if resumption.get("resumable") and resumption.get("newHandle"):
                self._resume_handle = str(resumption["newHandle"])
        if msg.get("goAway"):
            # The server will close soon; the pump's reconnect path picks
            # the session up through the resumption handle.
            logger.info("Gemini Live: goAway received (timeLeft=%s)",
                        (msg["goAway"] or {}).get("timeLeft", "?"))
        if "error" in msg:
            err = msg.get("error")
            message = err.get("message", str(err)) \
                if isinstance(err, dict) else str(err)
            events.append({"type": "error",
                           "message": message or "provider error",
                           "fatal": True})
        return events
