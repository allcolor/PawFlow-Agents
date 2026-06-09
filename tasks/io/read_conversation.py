"""ReadConversation Task — Read messages from a linked conversation.

For conversation-scoped flows: loads recent messages from the conversation
into the FlowFile content for processing by downstream tasks.

Flow pattern:
    trigger → readConversation → processMessages
"""

import json
import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ReadConversationTask(BaseTask):
    """Read messages from a linked conversation."""

    TYPE = "readConversation"
    VERSION = "1.0.0"
    NAME = "Read Conversation"
    DESCRIPTION = "Read messages from a linked conversation"
    ICON = "chat"

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        from core.flow_runtime_access import set_runtime_context
        set_runtime_context(
            self, user_id=user_id, conversation_id=conversation_id,
            scope=scope, agent_name=agent_name)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {
                "type": "string", "required": True,
                "default": "${_conversation_id}",
                "description": "Conversation to read from",
            },
            "limit": {
                "type": "integer", "required": False, "default": 20,
                "description": "Number of recent messages to read",
            },
            "format": {
                "type": "select", "required": False, "default": "json",
                "options": ["json", "text"],
                "description": "Output format",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        conv_id = flowfile.get_attribute("conversation_id") or self.config.get("conversation_id", "")
        limit = int(self.config.get("limit", 20))
        fmt = self.config.get("format", "json")

        if not conv_id or "${" in conv_id:
            flowfile.set_content(json.dumps({
                "error": "No conversation_id - set via FlowFile attribute or flow parameter",
            }).encode())
            return [flowfile]

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        try:
            from core.flow_runtime_access import (
                authorize_conversation_target, conversation_owner,
                runtime_context_from_task, trusted_requester_user_id,
            )
            conv_id = authorize_conversation_target(
                runtime_context_from_task(self), conv_id,
                requester_user_id=trusted_requester_user_id(flowfile),
                allow_global_admin=self.config.get("allow_global_admin"))
            owner = conversation_owner(conv_id)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        page = store.load_page(conv_id, limit=limit, offset=0, user_id=owner)
        if page is None:
            flowfile.set_content(json.dumps({
                "error": "Conversation not found",
            }).encode())
            return [flowfile]

        messages = page.get("messages", [])

        if fmt == "text":
            lines = []
            for m in messages:
                role = m.get("role", "?").upper()
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                source = m.get("source", {})
                agent = source.get("name", "") if isinstance(source, dict) else ""
                prefix = f"[{agent}]" if agent else f"[{role}]"
                lines.append(f"{prefix}: {content}")
            flowfile.set_content("\n\n".join(lines).encode("utf-8"))
        else:
            flowfile.set_content(json.dumps({
                "conversation_id": conv_id,
                "messages": messages,
                "total_count": page.get("total_count", len(messages)),
            }, ensure_ascii=False).encode("utf-8"))

        flowfile.set_attribute("conversation.message_count", str(len(messages)))
        return [flowfile]


TaskFactory.register(ReadConversationTask)
