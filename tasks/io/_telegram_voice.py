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
