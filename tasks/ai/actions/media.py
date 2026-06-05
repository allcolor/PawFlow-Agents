"""AgentLoopTask actions — media"""

import json
import logging
import time
import threading
import base64
import os
import shutil
import subprocess  # nosec B404 - ffmpeg conversion uses fixed argv without shell.
import tempfile
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _convert_stt_audio_to_wav(audio_bytes: bytes, mime_type: str, filename: str) -> tuple[bytes, str, str]:
    marker = f"{mime_type} {filename}".lower()
    if "wav" in marker or "wave" in marker:
        return audio_bytes, mime_type or "audio/wav", filename or "recording.wav"
    if not any(kind in marker for kind in ("webm", "ogg", "opus")):
        return audio_bytes, mime_type, filename
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("[STT] ffmpeg unavailable; forwarding browser audio without conversion mime=%s filename=%s", mime_type, filename)
        return audio_bytes, mime_type, filename
    src = dst = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as in_fh:
            src = in_fh.name
            in_fh.write(audio_bytes)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as out_fh:
            dst = out_fh.name
        subprocess.check_call([  # nosec B603 - fixed ffmpeg argv, temp files are local and shell=False.
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", src, "-ac", "1", "-ar", "16000", dst,
        ])
        with open(dst, "rb") as fh:
            converted = fh.read()
        logger.info("[STT] converted browser audio to wav: %d -> %d bytes", len(audio_bytes), len(converted))
        return converted, "audio/wav", "speech.wav"
    finally:
        for path in (src, dst):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _stt_service_accepts_browser_audio(service) -> bool:
    return bool(getattr(service, "ACCEPTS_BROWSER_STT_AUDIO", False))


def _handle_media(self, action, body, store, user_id, flowfile):
    """Handle media actions. Returns [flowfile] or None."""

    if action == "stt_transcribe":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "") or "agent"
        service_name = body.get("service") or body.get("stt_service") or ""
        audio_b64 = body.get("audio_b64") or ""
        if not audio_b64:
            flowfile.set_content(json.dumps({"error": "audio_b64 required"}).encode())
            return [flowfile]
        try:
            if "," in audio_b64 and audio_b64.split(",", 1)[0].startswith("data:"):
                audio_b64 = audio_b64.split(",", 1)[1]
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as exc:
            flowfile.set_content(json.dumps({"error": f"invalid audio_b64: {exc}"}).encode())
            return [flowfile]
        if not audio_bytes:
            flowfile.set_content(json.dumps({"error": "empty audio"}).encode())
            return [flowfile]
        mime_type = body.get("mime_type", "") or "audio/webm"
        filename = body.get("filename", "recording.webm") or "recording.webm"
        if service_name:
            try:
                from core.service_registry import ServiceRegistry
                svc = ServiceRegistry.get_instance().resolve(
                    service_name, user_id=user_id, conv_id=conv_id)
                err = "" if svc else f"STT service '{service_name}' not found or not connected"
            except Exception as exc:
                svc = None
                err = f"STT service '{service_name}' failed to resolve: {exc}"
        else:
            resolver = self._make_stt_resolver(
                user_id, conv_id, agent_name, ("transcribe",))
            svc, err = resolver()
        if not svc:
            flowfile.set_content(json.dumps({"error": err or "no STT service available"}).encode())
            return [flowfile]
        if not _stt_service_accepts_browser_audio(svc):
            try:
                audio_bytes, mime_type, filename = _convert_stt_audio_to_wav(audio_bytes, mime_type, filename)
            except Exception as exc:
                logger.exception("[STT] audio conversion failed")
                flowfile.set_content(json.dumps({"error": f"audio conversion failed: {exc}"}).encode())
                return [flowfile]
        logger.info(
            "[STT] transcribe requested: service=%s bytes=%d mime=%s filename=%s conv=%s",
            service_name or getattr(svc, "NAME", "<auto>"), len(audio_bytes), mime_type, filename,
            conv_id[:8] if conv_id else "",
        )

        stt_file_id = ""
        stt_audio_path = ""
        if user_id and conv_id:
            try:
                from core.file_store import FileStore
                ttl = int(self.config.get("stt_transient_ttl")
                          or os.environ.get("PAWFLOW_WEBCHAT_STT_TTL_SECONDS", "300"))
                stt_file_id = FileStore.instance().store(
                    filename, audio_bytes, mime_type,
                    conversation_id=conv_id,
                    user_id=user_id,
                    ttl=max(60, ttl),
                    agent_name=agent_name,
                    category="webchat_stt",
                )
                disk_path = FileStore.instance().get_disk_path(stt_file_id, user_id=user_id)
                stt_audio_path = str(disk_path) if disk_path else ""
            except Exception as exc:
                logger.debug("[STT] transient FileStore staging skipped: %s", exc)
        try:
            if hasattr(svc, "set_runtime_context"):
                svc.set_runtime_context(
                    user_id=user_id, conversation_id=conv_id,
                    agent_name=agent_name)
            result = svc.transcribe(
                audio_bytes=b"" if stt_audio_path else audio_bytes,
                audio_path=stt_audio_path,
                mime_type=mime_type,
                filename=filename,
                language=body.get("language", "") or "",
                prompt=body.get("prompt", "") or "",
                model=body.get("model", "") or "",
            )
            logger.info(
                "[STT] transcribe completed: service=%s chars=%d duration=%s conv=%s",
                service_name or getattr(svc, "NAME", "<auto>"),
                len(str(result.get("text", "") or "")), result.get("duration", 0),
                conv_id[:8] if conv_id else "",
            )
            flowfile.set_content(json.dumps({
                "ok": True,
                "text": result.get("text", ""),
                "language": result.get("language", ""),
                "duration": result.get("duration", 0),
                "segments": result.get("segments", []),
            }, ensure_ascii=False).encode())
        except Exception as exc:
            flowfile.set_content(json.dumps({"error": str(exc)}).encode())
        finally:
            if stt_file_id:
                try:
                    from core.file_store import FileStore
                    FileStore.instance().delete(stt_file_id, user_id=user_id)
                except Exception as exc:
                    logger.debug("[STT] transient FileStore cleanup failed: %s", exc)
        return [flowfile]

    if action == "stt_warmup":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "") or "agent"
        service_name = body.get("service") or body.get("stt_service") or ""
        if service_name:
            try:
                from core.service_registry import ServiceRegistry
                svc = ServiceRegistry.get_instance().resolve(
                    service_name, user_id=user_id, conv_id=conv_id)
                err = "" if svc else f"STT service '{service_name}' not found or not connected"
            except Exception as exc:
                svc = None
                err = f"STT service '{service_name}' failed to resolve: {exc}"
        else:
            resolver = self._make_stt_resolver(
                user_id, conv_id, agent_name, ("transcribe",))
            svc, err = resolver()
        if not svc:
            flowfile.set_content(json.dumps({"ok": False, "error": err or "no STT service available"}).encode())
            return [flowfile]
        try:
            if hasattr(svc, "set_runtime_context"):
                svc.set_runtime_context(
                    user_id=user_id, conversation_id=conv_id,
                    agent_name=agent_name)
            warmup = getattr(svc, "warmup_stt", None)
            if callable(warmup):
                warmup(
                    language=body.get("language", "") or "",
                    model=body.get("model", "") or "",
                )
            else:
                ensure = getattr(svc, "ensure_connected", None)
                if callable(ensure):
                    ensure()
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as exc:
            flowfile.set_content(json.dumps({"ok": False, "error": str(exc)}).encode())
        return [flowfile]

    if action == "tts_synthesize":
        from core.handlers.capabilities import SpeakHandler
        import re

        conv_id = body.get("conversation_id", "")
        text = body.get("text", "") or ""
        if not text.strip():
            flowfile.set_content(json.dumps({"error": "text required"}).encode())
            return [flowfile]
        logger.info("[TTS] synthesize requested: service=%s voice=%s chars=%d conv=%s",
                    body.get("service", ""), body.get("voice", ""),
                    len(text), conv_id[:8] if conv_id else "")

        handler = SpeakHandler()
        file_base_url = self.config.get("file_base_url", "") or ""
        try:
            from core.expression import resolve_value
            file_base_url = resolve_value(file_base_url) or ""
        except Exception as exc:
            logger.debug("TTS file_base_url resolution failed: %s", exc)
        if file_base_url:
            handler.set_base_url(file_base_url)
        handler.set_user_id(user_id)
        handler.set_conversation_id(conv_id)
        agent_name = body.get("agent_name", "") or "agent"
        if hasattr(handler, "set_agent_name"):
            handler.set_agent_name(agent_name)
        handler.set_service_resolver(self._make_tts_resolver(
            user_id, conv_id, agent_name, ("speak",)))

        args = {k: v for k, v in body.items()
                if k not in {"action", "conversation_id", "_call_id", "_reply_conversation_id"}}
        transient = body.get("transient", True)
        if transient is not False:
            try:
                ttl = int(body.get("transient_ttl") or body.get("ttl")
                          or self.config.get("tts_transient_ttl")
                          or os.environ.get("PAWFLOW_WEBCHAT_TTS_TTL_SECONDS", "300"))
            except (TypeError, ValueError):
                ttl = 300
            args["_tts_storage_ttl"] = max(60, ttl)
        result = handler.execute(args)
        if result.startswith("Error:"):
            logger.warning(
                "[TTS] synthesize failed: service=%s voice=%s conv=%s error=%s",
                body.get("service", ""), body.get("voice", ""),
                conv_id[:8] if conv_id else "", result)
            flowfile.set_content(json.dumps({"error": result}).encode())
            return [flowfile]

        match = re.search(r"fs://filestore/([^/\s]+)/([^\s]+)", result)
        file_id = ""
        filename = "speech.mp3"
        if match:
            file_id = match.group(1)
            filename = match.group(2)
        else:
            fid_match = re.search(r"file_id:\s*([^\s]+)", result)
            if fid_match:
                file_id = fid_match.group(1)
                try:
                    from core.file_store import FileStore
                    meta = FileStore.instance().get_metadata(file_id) or {}
                    filename = meta.get("filename", filename)
                except Exception as exc:
                    logger.debug("TTS FileStore metadata lookup failed: %s", exc)
        if not file_id:
            logger.warning(
                "[TTS] synthesize returned no audio file: service=%s voice=%s conv=%s result=%s",
                body.get("service", ""), body.get("voice", ""),
                conv_id[:8] if conv_id else "", result)
            flowfile.set_content(json.dumps({"error": "no audio file returned"}).encode())
            return [flowfile]
        url = "/files/" + file_id + "/" + filename
        flowfile.set_content(json.dumps({
            "ok": True,
            "url": url,
            "file_id": file_id,
            "filename": filename,
            "provider_result": result,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "tts_delete":
        file_id = body.get("file_id", "") or ""
        if not file_id:
            flowfile.set_content(json.dumps({"ok": False, "error": "file_id required"}).encode())
            return [flowfile]
        try:
            from core.file_store import FileStore
            store = FileStore.instance()
            allowed = any(
                (entry.get("id") or entry.get("file_id")) == file_id
                and entry.get("user_id") == user_id
                and entry.get("ttl", 0) > 0
                for entry in store.list_by_category("voice_clone_tts")
            )
            deleted = store.delete(file_id, user_id=user_id) if allowed else False
            flowfile.set_content(json.dumps({"ok": True, "deleted": deleted}).encode())
        except Exception as exc:
            flowfile.set_content(json.dumps({"ok": False, "error": str(exc)}).encode())
        return [flowfile]

    if action == "tts_warmup":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "") or "agent"
        service_name = body.get("service") or body.get("audio_service") or ""
        if service_name:
            try:
                from core.service_registry import ServiceRegistry
                svc = ServiceRegistry.get_instance().resolve(
                    service_name, user_id=user_id, conv_id=conv_id)
                err = "" if svc else f"media service '{service_name}' not found or not connected"
            except Exception as exc:
                svc = None
                err = f"media service '{service_name}' failed to resolve: {exc}"
        else:
            resolver = self._make_tts_resolver(
                user_id, conv_id, agent_name, ("speak",))
            svc, err = resolver()
        if not svc:
            flowfile.set_content(json.dumps({
                "ok": False, "error": err or "no TTS service available",
            }).encode())
            return [flowfile]
        try:
            if hasattr(svc, "set_runtime_context"):
                svc.set_runtime_context(
                    user_id=user_id, conversation_id=conv_id,
                    agent_name=agent_name)
            warmup = getattr(svc, "warmup", None)
            if callable(warmup):
                warmup(
                    voice=body.get("voice", "") or "",
                    language=body.get("language", "") or "",
                )
            else:
                ensure = getattr(svc, "ensure_connected", None)
                if callable(ensure):
                    ensure()
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as exc:
            flowfile.set_content(json.dumps({
                "ok": False, "error": str(exc),
            }).encode())
        return [flowfile]


    if action == "list_image_services":
        from services.base_image_generation import BaseImageGenerationService
        conv_id = body.get("conversation_id", "")
        services = self._discover_media_services(
            user_id, BaseImageGenerationService, conv_id)
        prefs = {}
        if conv_id:
            prefs = store.get_extra(conv_id, "image_services") or {}
        result = [{
            "id": sid, "type": stype, "scope": scope,
            "selected_for": [
                k for k, v in prefs.items() if v == sid
            ],
        } for sid, stype, scope in services]
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_tts_services":
        from services.base_tts import BaseTTSService
        conv_id = body.get("conversation_id", "")
        services = self._discover_media_services(
            user_id, BaseTTSService, conv_id)
        prefs = store.get_extra(conv_id, "audio_services") or {} if conv_id else {}
        result = [{
            "id": sid, "type": stype, "scope": scope,
            "selected_for": [
                k for k, v in prefs.items() if v == sid
            ],
        } for sid, stype, scope in services]
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_stt_services":
        from services.base_stt import BaseSTTService
        conv_id = body.get("conversation_id", "")
        services = self._discover_media_services(
            user_id, BaseSTTService, conv_id)
        prefs = store.get_extra(conv_id, "stt_services") or {} if conv_id else {}
        result = [{
            "id": sid, "type": stype, "scope": scope,
            "selected_for": [
                k for k, v in prefs.items() if v == sid
            ],
        } for sid, stype, scope in services]
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "set_tts_service":
        conv_id = body.get("conversation_id", "")
        service_name = body.get("service_name", "") or body.get("service", "")
        agent = body.get("agent_name", "*") or "*"
        if not conv_id or not service_name:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and service_name required",
            }).encode())
            return [flowfile]
        prefs = store.get_extra(conv_id, "audio_services") or {}
        prefs[agent] = service_name
        store.set_extra(conv_id, "audio_services", prefs)
        flowfile.set_content(json.dumps({
            "ok": True, "service": service_name, "agent": agent,
        }).encode())
        return [flowfile]

    if action == "clear_tts_service":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({
                "error": "conversation_id required",
            }).encode())
            return [flowfile]
        if agent:
            prefs = store.get_extra(conv_id, "audio_services") or {}
            prefs.pop(agent, None)
            store.set_extra(conv_id, "audio_services", prefs)
        else:
            store.set_extra(conv_id, "audio_services", {})
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "set_stt_service":
        conv_id = body.get("conversation_id", "")
        service_name = body.get("service_name", "") or body.get("service", "")
        agent = body.get("agent_name", "*") or "*"
        if not conv_id or not service_name:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and service_name required",
            }).encode())
            return [flowfile]
        prefs = store.get_extra(conv_id, "stt_services") or {}
        prefs[agent] = service_name
        store.set_extra(conv_id, "stt_services", prefs)
        flowfile.set_content(json.dumps({
            "ok": True, "service": service_name, "agent": agent,
        }).encode())
        return [flowfile]

    if action == "clear_stt_service":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({
                "error": "conversation_id required",
            }).encode())
            return [flowfile]
        if agent:
            prefs = store.get_extra(conv_id, "stt_services") or {}
            prefs.pop(agent, None)
            store.set_extra(conv_id, "stt_services", prefs)
        else:
            store.set_extra(conv_id, "stt_services", {})
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "set_image_service":
        conv_id = body.get("conversation_id", "")
        service_name = body.get("service_name", "")
        agent = body.get("agent_name", "*")
        if not conv_id or not service_name:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and service_name required",
            }).encode())
            return [flowfile]
        prefs = store.get_extra(conv_id, "image_services") or {}
        prefs[agent] = service_name
        store.set_extra(conv_id, "image_services", prefs)
        flowfile.set_content(json.dumps({
            "ok": True, "service": service_name, "agent": agent,
        }).encode())
        return [flowfile]

    if action == "clear_image_service":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({
                "error": "conversation_id required",
            }).encode())
            return [flowfile]
        if agent:
            prefs = store.get_extra(conv_id, "image_services") or {}
            prefs.pop(agent, None)
            store.set_extra(conv_id, "image_services", prefs)
        else:
            store.set_extra(conv_id, "image_services", {})
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    # Video service management

    if action == "list_video_services":
        from services.base_video_generation import BaseVideoGenerationService
        conv_id = body.get("conversation_id", "")
        services = self._discover_media_services(
            user_id, BaseVideoGenerationService, conv_id)
        prefs = store.get_extra(conv_id, "video_services") or {} if conv_id else {}
        result = [{
            "id": sid, "type": stype, "scope": scope,
            "selected_for": [k for k, v in prefs.items() if v == sid],
        } for sid, stype, scope in services]
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "set_video_service":
        conv_id = body.get("conversation_id", "")
        service_name = body.get("service_name", "")
        agent = body.get("agent_name", "*")
        if not conv_id or not service_name:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and service_name required",
            }).encode())
            return [flowfile]
        prefs = store.get_extra(conv_id, "video_services") or {}
        prefs[agent] = service_name
        store.set_extra(conv_id, "video_services", prefs)
        flowfile.set_content(json.dumps({
            "ok": True, "service": service_name, "agent": agent,
        }).encode())
        return [flowfile]

    if action == "clear_video_service":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({
                "error": "conversation_id required",
            }).encode())
            return [flowfile]
        if agent:
            prefs = store.get_extra(conv_id, "video_services") or {}
            prefs.pop(agent, None)
            store.set_extra(conv_id, "video_services", prefs)
        else:
            store.set_extra(conv_id, "video_services", {})
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    return None
