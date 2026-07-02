"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
import base64
import json

from core import FlowFile

logger = logging.getLogger(__name__)
# Split out of telegram_agent_client.py for the <=800-line rule; re-exported
# from tasks.io.telegram_agent_client (invariant 1: import-path stability).


def _telegram_tts_enabled(conversation_id: str) -> bool:
    if not conversation_id:
        return False
    try:
        from core.conversation_store import ConversationStore
        return bool(ConversationStore.instance().get_extra(
            conversation_id, "telegram_tts_enabled"))
    except Exception:
        logger.debug("Telegram TTS enabled lookup failed", exc_info=True)
        return False


def _attach_telegram_tts_audio(
    flowfile: FlowFile, text: str, user_id: str, conversation_id: str,
    agent_name: str,
) -> None:
    if not text.strip() or not _telegram_tts_enabled(conversation_id):
        return
    service_id = _configured_tts_service_id(conversation_id, agent_name)
    if not service_id:
        return
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            service_id, user_id=user_id, conv_id=conversation_id)
        if not svc or not callable(getattr(svc, "speak", None)):
            logger.warning("Telegram TTS service not available: %s", service_id)
            return
        if hasattr(svc, "set_runtime_context"):
            svc.set_runtime_context(
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name)
        result = svc.speak(text=text)
        audio = (result or {}).get("audio_bytes") or (result or {}).get("bytes") or b""
        audio_path = (result or {}).get("audio_path") or (result or {}).get("path") or ""
        if not audio and audio_path:
            from pathlib import Path
            audio = Path(str(audio_path)).read_bytes()
            if (result or {}).get("_delete_media_path"):
                try:
                    Path(str(audio_path)).unlink()
                except OSError:
                    pass
        if not audio:
            logger.warning("Telegram TTS provider returned no audio: %s", service_id)
            return
        content_type = str((result or {}).get("content_type") or "audio/mpeg")
        ext = {
            "audio/mpeg": "mp3", "audio/mp3": "mp3",
            "audio/wav": "wav", "audio/x-wav": "wav",
            "audio/ogg": "ogg", "audio/opus": "ogg",
            "audio/flac": "flac", "audio/aac": "aac",
        }.get(content_type.split(";")[0].strip().lower(), "mp3")
        flowfile.set_attribute(
            "telegram.tts_audio_base64",
            base64.b64encode(audio).decode("ascii"))
        flowfile.set_attribute("telegram.tts_content_type", content_type)
        flowfile.set_attribute("telegram.tts_filename", f"telegram_reply.{ext}")
    except Exception as exc:
        logger.warning("Telegram TTS synthesis failed: %s", exc, exc_info=True)


def _transcribe_telegram_voice(
    content: str, user_id: str, conversation_id: str, agent_name: str,
) -> str:
    transcript, _error = _transcribe_telegram_voice_result(
        content, user_id, conversation_id, agent_name)
    return transcript


def _transcribe_telegram_voice_result(
    content: str, user_id: str, conversation_id: str, agent_name: str,
) -> tuple[str, str]:
    stt_file_id = ""
    try:
        payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(payload, dict) or payload.get("type") not in {"voice", "audio"}:
        return "", ""
    audio_b64 = str(payload.get("data_base64") or "")
    if not audio_b64:
        logger.info(
            "Telegram voice STT skipped for %s: empty audio payload",
            conversation_id,
        )
        return "", "voice download failed (empty audio payload)"
    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        logger.warning("Telegram voice STT skipped: invalid audio payload", exc_info=True)
        return "", "invalid audio payload"
    if not audio_bytes:
        logger.info(
            "Telegram voice STT skipped for %s: decoded audio payload is empty",
            conversation_id,
        )
        return "", "voice download failed (empty audio payload)"
    try:
        from tasks.ai.actions.media import resolve_stt_service

        svc, err = resolve_stt_service(
            user_id, conversation_id, agent_name, ("transcribe",))
        if not svc or not callable(getattr(svc, "transcribe", None)):
            logger.info(
                "Telegram voice STT skipped for %s: %s",
                conversation_id,
                err or "no STT service available",
            )
            return "", (err or "no STT service configured — deploy one, "
                        "then pick it with /sttservice")
        service_id = str(getattr(svc, "service_id", "") or getattr(svc, "NAME", "") or "<resolved>")
        if hasattr(svc, "set_runtime_context"):
            svc.set_runtime_context(
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name)
        mime_type = str(payload.get("mime_type") or "audio/ogg")
        filename = str(payload.get("file_name") or "telegram_voice.ogg")
        original_size = len(audio_bytes)
        original_mime_type = mime_type
        original_filename = filename
        try:
            from tasks.ai.actions.media import prepare_stt_audio_for_service
            audio_bytes, mime_type, filename = prepare_stt_audio_for_service(
                svc, audio_bytes, mime_type, filename)
        except Exception as exc:
            logger.warning(
                "Telegram voice STT audio conversion failed; forwarding original audio: %s",
                exc,
                exc_info=True,
            )
        logger.info(
            "Telegram voice STT transcribe requested: user=%s service=%s bytes=%d->%d mime=%s->%s filename=%s->%s conv=%s agent=%s",
            user_id,
            service_id,
            original_size,
            len(audio_bytes),
            original_mime_type,
            mime_type,
            original_filename,
            filename,
            conversation_id[:8],
            agent_name,
        )
        audio_path = ""
        if user_id and conversation_id:
            try:
                from core.file_store import FileStore
                stt_file_id = FileStore.instance().store(
                    filename, audio_bytes, mime_type,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    ttl=300,
                    agent_name=agent_name,
                    category="telegram_stt",
                )
                disk_path = FileStore.instance().get_disk_path(stt_file_id, user_id=user_id)
                audio_path = str(disk_path) if disk_path else ""
            except Exception as exc:
                logger.debug("Telegram voice STT transient FileStore staging skipped: %s", exc)
        result = svc.transcribe(
            audio_bytes=b"" if audio_path else audio_bytes,
            audio_path=audio_path,
            mime_type=mime_type,
            filename=filename,
        )
        transcript = str((result or {}).get("text") or "").strip()
        logger.info(
            "Telegram voice STT transcribe completed: user=%s service=%s chars=%d conv=%s agent=%s",
            user_id,
            service_id,
            len(transcript),
            conversation_id[:8],
            agent_name,
        )
        return transcript, ""
    except Exception as exc:
        logger.warning("Telegram voice STT failed: %s", exc, exc_info=True)
        return "", str(exc)
    finally:
        if stt_file_id:
            try:
                from core.file_store import FileStore
                FileStore.instance().delete(stt_file_id, user_id=user_id)
            except Exception:
                logger.debug("Telegram voice STT transient FileStore cleanup failed", exc_info=True)

def _configured_tts_service_id(conversation_id: str, agent_name: str) -> str:
    if not conversation_id:
        return ""
    try:
        from core.conversation_store import ConversationStore
        prefs = ConversationStore.instance().get_extra(conversation_id, "audio_services") or {}
    except Exception:
        logger.debug("Telegram TTS preference lookup failed", exc_info=True)
        return ""
    if not isinstance(prefs, dict):
        return ""
    return str(prefs.get(agent_name or "agent") or prefs.get("*") or "").strip()


# ── Realtime speech-to-speech voice notes (P2c) ─────────────────────────
#
# When the target agent carries a `realtime_voice_service` link, a Telegram
# voice note is answered by a one-shot realtime turn (audio in → spoken
# answer out as a voice note) instead of the STT → text agent → TTS
# pipeline. Transcripts persist as normal messages (the assistant text
# reaches Telegram through the live bridge), so only the voice note itself
# travels on the direct reply. Any failure returns False and the caller
# falls back to the STT pipeline.

_REALTIME_TURN_RATE = 24000  # PCM16 mono, OpenAI realtime native rate
# A hung ffmpeg (pathological container) must not pin a Telegram worker
# thread forever — a timeout degrades into the normal fallback paths
# (decode → STT pipeline, encode → text-only reply).
_FFMPEG_TIMEOUT_S = 60


def _ffmpeg_bin() -> str:
    import shutil
    return shutil.which("ffmpeg") or ""


def _decode_audio_to_pcm16(audio_bytes: bytes, suffix: str = ".ogg") -> bytes:
    """Any container (OGG/Opus voice note, m4a, mp3) → raw PCM16 mono 24k."""
    import os
    import subprocess  # nosec B404 - explicit ffmpeg argv, shell=False
    import tempfile
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable")
    src = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
            src = fh.name
            fh.write(audio_bytes)
        out = subprocess.run(  # nosec B603
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", src,
             "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1",
             "-ar", str(_REALTIME_TURN_RATE), "pipe:1"],
            check=True, capture_output=True,
            timeout=_FFMPEG_TIMEOUT_S).stdout
        if not out:
            raise RuntimeError("ffmpeg produced no PCM output")
        return out
    finally:
        if src:
            try:
                os.unlink(src)
            except OSError:
                pass


def _encode_pcm16_to_ogg(pcm: bytes) -> bytes:
    """Raw PCM16 mono 24k → OGG/Opus (Telegram voice-note format)."""
    import os
    import subprocess  # nosec B404 - explicit ffmpeg argv, shell=False
    import tempfile
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable")
    dst = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as fh:
            dst = fh.name
        subprocess.run(  # nosec B603
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "s16le", "-ar", str(_REALTIME_TURN_RATE), "-ac", "1",
             "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", dst],
            check=True, input=pcm, capture_output=True,
            timeout=_FFMPEG_TIMEOUT_S)
        with open(dst, "rb") as fh:
            return fh.read()
    finally:
        if dst:
            try:
                os.unlink(dst)
            except OSError:
                pass


def _agent_realtime_service_id(conversation_id: str, agent_name: str) -> str:
    try:
        from core.conv_agent_config import get_agent_config
        return str(get_agent_config(conversation_id, agent_name).get(
            "realtime_voice_service", "") or "").strip()
    except Exception:
        logger.debug("Telegram realtime link lookup failed", exc_info=True)
        return ""


def _telegram_realtime_voice_reply(
    flowfile: FlowFile, content: str, user_id: str, conversation_id: str,
    agent_name: str,
) -> bool:
    """Answer a Telegram voice note through a realtime speech-to-speech turn.

    Returns True when handled (voice-note reply attached to `flowfile`);
    False on any precondition/error so the STT pipeline takes over.
    """
    service_id = _agent_realtime_service_id(conversation_id, agent_name)
    if not service_id:
        return False
    try:
        payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("type") not in {"voice", "audio"}:
        return False
    try:
        audio_bytes = base64.b64decode(str(payload.get("data_base64") or ""))
    except Exception:
        return False
    if not audio_bytes:
        return False
    turn_done = False
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(service_id, user_id=user_id,
                                         conv_id=conversation_id)
        if svc_def is None or getattr(svc_def, "service_type", "") != \
                "realtimeVoiceConnection":
            logger.warning(
                "Telegram realtime voice skipped: '%s' is not a "
                "realtimeVoiceConnection service", service_id)
            return False
        service = reg.resolve(service_id, user_id=user_id,
                              conv_id=conversation_id)
        if service is None:
            logger.warning("Telegram realtime voice skipped: service '%s' "
                           "could not connect", service_id)
            return False
        if hasattr(service, "set_runtime_context"):
            service.set_runtime_context(
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name)

        mime = str(payload.get("mime_type") or "audio/ogg").lower()
        suffix = ".ogg" if ("ogg" in mime or "opus" in mime) else ".m4a"
        pcm_in = _decode_audio_to_pcm16(audio_bytes, suffix=suffix)

        from services._realtime_turn import run_voice_turn
        result = run_voice_turn(
            service, conversation_id=conversation_id, agent_name=agent_name,
            user_id=user_id, pcm16=pcm_in, user_channel="telegram")
        turn_done = True

        # The turn succeeded: transcripts are already persisted, so from
        # here on we NEVER fall back to the STT pipeline (it would process
        # the same voice note a second time). An encode failure downgrades
        # to a text reply instead.
        try:
            ogg = _encode_pcm16_to_ogg(result["audio"]) \
                if result.get("audio") else b""
        except Exception:
            logger.warning("Telegram realtime voice: OGG encode failed — "
                           "replying with text only", exc_info=True)
            ogg = b""
        if ogg:
            flowfile.set_attribute(
                "telegram.tts_audio_base64",
                base64.b64encode(ogg).decode("ascii"))
            flowfile.set_attribute("telegram.tts_content_type", "audio/ogg")
            flowfile.set_attribute("telegram.tts_filename", "voice_reply.ogg")
            # The assistant transcript reaches Telegram as text through the
            # live bridge (persisted message) — the reply carries only audio.
            flowfile.set_content(b"")
        else:
            flowfile.set_content(
                str(result.get("agent_text") or "").encode("utf-8"))
        logger.info(
            "Telegram realtime voice turn done: user=%s service=%s conv=%s "
            "agent=%s audio=%dB", user_id, service_id, conversation_id[:8],
            agent_name, len(result.get("audio") or b""))
        return True
    except Exception as exc:
        if turn_done:
            # Transcripts are already persisted — an STT fallback would
            # answer the same voice note twice. The assistant text still
            # reaches Telegram through the persisted message.
            logger.warning(
                "Telegram realtime voice: post-turn delivery failed — "
                "suppressing STT fallback: %s", exc, exc_info=True)
            return True
        logger.warning(
            "Telegram realtime voice turn failed (falling back to STT): %s",
            exc, exc_info=True)
        return False
