"""AgentLoopTask actions — misc (model, theme)"""

import json
import logging
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_misc(self, action, body, store, user_id, flowfile):
    """Handle misc actions (model, theme). Returns [flowfile] or None."""

    if action == "model":
        model_value = body.get("model", "").strip()
        agent_name = body.get("agent", "").strip()
        conv_id = body.get("conversation_id", "")
        override_key = f"model_override:{agent_name}"
        if not model_value or model_value == "reset":
            if conv_id:
                store.set_extra(conv_id, override_key, None, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model override cleared for '{agent_name}'. Using default model.",
            }).encode())
        else:
            if conv_id:
                store.set_extra(conv_id, override_key, model_value, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model for '{agent_name}' set to '{model_value}' in this conversation.",
                "model": model_value,
            }).encode())
        return [flowfile]

    if action == "theme":
        conv_id = body.get("conversation_id", "")
        css = body.get("css", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        store.set_extra(conv_id, "custom_css", css, user_id=user_id)
        if css:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conv_id, "theme", {"css": css})
            except Exception:
                pass
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Theme applied",
            "css_length": len(css),
        }).encode())
        return [flowfile]

    return None
