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
