"""AgentLoopTask actions — media"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_media(self, action, body, store, user_id, flowfile):
    """Handle media actions. Returns [flowfile] or None."""

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
        except Exception:
            pass
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
        result = handler.execute(args)
        if result.startswith("Error:"):
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
                except Exception:
                    pass
        if not file_id:
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
