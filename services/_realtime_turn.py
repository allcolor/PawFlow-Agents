"""One-shot realtime voice turn — async channels (Telegram voice notes).

Speech-to-speech through the same `realtimeVoiceConnection` service the
webchat uses, but request/response: one PCM16 payload in, one spoken
answer out. No browser socket — the caller supplies audio and gets audio
back (Telegram encodes it as an OGG/Opus voice note).

Semantics match the live bridge: transcripts persist as normal
conversation messages (the user side with its origin channel so the
Telegram bridge does not echo it back), tool calls run through the same
silent-approval `RealtimeToolBridge` — executed inline with a generous
soft timeout since nobody is waiting on a live audio stream; a tool that
outlives even that detaches and its result lands as a system message.
"""

import logging
import time

from services import _realtime_bridge as _bridge_mod

logger = logging.getLogger(__name__)

_AUDIO_CHUNK_BYTES = 32768
_TOOL_SOFT_TIMEOUT_S = 60.0
_TRANSCRIPT_GRACE_S = 3.0


def run_voice_turn(service, *, conversation_id: str, agent_name: str,
                   user_id: str, pcm16: bytes, timeout_s: float = 120.0,
                   user_channel: str = "voice") -> dict:
    """Run one speech-to-speech turn. Returns
    {"audio": bytes, "user_text": str, "agent_text": str}.

    Raises on session-open failure or fatal provider error; the caller
    decides how to fall back (Telegram falls back to the STT pipeline).
    """
    if not pcm16:
        raise ValueError("empty audio payload")

    tools = None
    tool_defs = []
    profile = (getattr(service, "tool_profile", "") or "").strip()
    if profile:
        try:
            from services._realtime_tools import RealtimeToolBridge
            tools = RealtimeToolBridge(profile, conversation_id, agent_name,
                                       user_id)
            tool_defs = tools.tool_definitions()
        except Exception:
            logger.warning("[realtime-turn] tool bridge init failed — turn "
                           "continues without tools", exc_info=True)
            tools, tool_defs = None, []

    adapter = service.open_session(
        instructions=_bridge_mod.resolve_session_instructions(
            service, conversation_id, agent_name),
        tools=tool_defs,
        vad="manual",  # one committed turn, no live VAD
        user_id=user_id, conversation_id=conversation_id,
    )

    audio_out = bytearray()
    user_text = ""
    agent_text = ""
    response_done = False
    # A tool call means the CURRENT response ends as a function call: its
    # own `response_done` is not the end of the turn — the follow-up spoken
    # response (triggered by send_tool_result) is. Count the dones to skip.
    pending_tool_dones = 0
    error_message = ""
    deadline = time.monotonic() + timeout_s

    def _persist(role, text, channel="voice"):
        _bridge_mod.persist_voice_transcript(
            conversation_id, agent_name, user_id, role, text,
            channel=channel)

    try:
        for off in range(0, len(pcm16), _AUDIO_CHUNK_BYTES):
            adapter.send_audio(pcm16[off:off + _AUDIO_CHUNK_BYTES])
        adapter.commit_input()

        grace_until = None
        while time.monotonic() < deadline:
            if response_done:
                # Response finished; the user transcription is asynchronous
                # (separate whisper pass) — give it a short grace window.
                if user_text or time.monotonic() > grace_until:
                    break
            try:
                evt = adapter.recv_event(timeout=1.0)
            except ConnectionError:
                break
            if evt is None:
                continue
            etype = evt.get("type")
            if etype == "audio":
                audio_out.extend(evt.get("data") or b"")
            elif etype == "transcript_user" and evt.get("final"):
                user_text = evt.get("text", "") or user_text
            elif etype == "transcript_agent" and evt.get("final"):
                agent_text = (agent_text + "\n" + evt.get("text", "")).strip()
            elif etype == "tool_call":
                if tools is None:
                    adapter.send_tool_result(
                        evt.get("call_id", ""),
                        "Tool execution is not enabled for this voice "
                        "session.")
                else:
                    # Inline: turn-based callers can afford to wait; a tool
                    # outliving the soft timeout detaches and lands as a
                    # system message.
                    tools.handle_call(
                        evt.get("call_id", ""), evt.get("name", ""),
                        evt.get("arguments", ""),
                        send_result=adapter.send_tool_result,
                        announce=lambda text: _persist("system", text),
                        soft_timeout_s=_TOOL_SOFT_TIMEOUT_S)
                response_done = False  # a follow-up response is coming
                pending_tool_dones += 1
            elif etype == "response_done":
                if pending_tool_dones > 0:
                    # `done` of the function-call response itself (may land
                    # before OR after the tool executes) — not the turn end.
                    pending_tool_dones -= 1
                    continue
                response_done = True
                if grace_until is None:
                    grace_until = time.monotonic() + _TRANSCRIPT_GRACE_S
            elif etype == "error" and evt.get("fatal"):
                error_message = evt.get("message", "provider error")
                break
    finally:
        try:
            adapter.close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)

    if error_message and not audio_out:
        raise RuntimeError(f"realtime voice turn failed: {error_message}")
    if not audio_out and not agent_text:
        raise RuntimeError("realtime voice turn produced no response"
                           + (f" ({error_message})" if error_message else ""))

    _persist("user", user_text, channel=user_channel)
    _persist("assistant", agent_text)
    logger.info("[realtime-turn] cid=%s agent=%s in=%dB out=%dB user=%dch "
                "agent=%dch", conversation_id[:8], agent_name, len(pcm16),
                len(audio_out), len(user_text), len(agent_text))
    return {"audio": bytes(audio_out), "user_text": user_text,
            "agent_text": agent_text}
