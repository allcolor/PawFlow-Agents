"""PublishMessage Task — Publish a message into a linked conversation.

For conversation-scoped flows: takes FlowFile content and publishes it
as a message in the conversation, visible to the user via SSE.

Flow pattern:
    someTask → publishMessage

Config:
    conversation_id: "${_conversation_id}"
    agent_name: Source agent name for the message badge (default: "flow")
    role: Message role — "assistant" or "system" (default: "assistant")
"""

import json
import logging
import time
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class PublishMessageTask(BaseTask):
    """Publish a message into a conversation from a flow."""

    TYPE = "publishMessage"
    VERSION = "1.0.0"
    NAME = "Publish Message"
    DESCRIPTION = "Publish a message into a linked conversation"
    ICON = "chat"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {
                "type": "string", "required": True,
                "default": "${_conversation_id}",
                "description": "Target conversation ID",
            },
            "agent_name": {
                "type": "string", "required": False, "default": "flow",
                "description": "Source agent name (shown as badge in chat)",
            },
            "role": {
                "type": "select", "required": False, "default": "assistant",
                "options": ["assistant", "system"],
                "description": "Message role",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # FlowFile attribute takes priority (set by createConversation or upstream)
        conv_id = flowfile.get_attribute("conversation_id") or self.config.get("conversation_id", "")
        if not conv_id or "${" in conv_id:
            flowfile.set_content(json.dumps({
                "error": "No conversation_id — set via FlowFile attribute or flow parameter",
            }).encode())
            return [flowfile]

        agent_name = self.config.get("agent_name", "flow")
        role = self.config.get("role", "assistant")
        text = flowfile.get_content().decode("utf-8", errors="replace")

        if not text.strip():
            return [flowfile]  # Nothing to publish

        # 1. Persist to ConversationStore
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        import uuid as _uuid_pm
        _pm_msg_id = _uuid_pm.uuid4().hex[:12]
        source = {"type": "agent", "name": agent_name}
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        # Persist + publish SSE atomically: the writer fires `done` ONLY
        # after the message is on disk (visible ⇒ persisted invariant).
        _done_evt = {
            "response": text,
            "msg_id": _pm_msg_id,
            "all_msg_ids": [_pm_msg_id],
            "conversation_id": conv_id,
            "agent_name": agent_name,
            "source": source,
            "model": "",
            "provider": "flow",
            "tokens_in": 0,
            "tokens_out": 0,
            "tools_called": [],
            "iterations": 0,
            "duration_ms": 0,
        }
        ConversationWriter.for_conversation(conv_id).enqueue_message(
            stamp_message({
                "role": role,
                "content": text,
                "source": source,
                "msg_id": _pm_msg_id,
            }),
            agent_name=agent_name,
            sse_events=[{"type": "done", "data": _done_evt}])

        logger.info(f"[publishMessage] Published to {conv_id[:8]} as {agent_name}")
        return [flowfile]


TaskFactory.register(PublishMessageTask)
