"""CreateConversation Task — Create a new conversation from a flow.

For user-scoped or conversation-scoped flows: creates a new conversation
in the ConversationStore and returns the conversation_id.

Flow pattern:
    trigger → createConversation → publishMessage / spawnAgent
"""

import json
import logging
import uuid
import time
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class CreateConversationTask(BaseTask):
    """Create a new conversation and return its ID."""

    TYPE = "createConversation"
    VERSION = "1.0.0"
    NAME = "Create Conversation"
    DESCRIPTION = "Create a new conversation for publishing messages or spawning agents"
    ICON = "chat"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "user_id": {
                "type": "string", "required": True,
                "default": "${_user_id}",
                "description": "Owner of the conversation",
            },
            "preview": {
                "type": "string", "required": False, "default": "",
                "description": "Preview text shown in conversation list",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        user_id = self.config.get("user_id", "")
        if not user_id or "${" in user_id:
            flowfile.set_content(json.dumps({
                "error": "No user_id — requires user or conversation-scoped flow",
            }).encode())
            return [flowfile]

        preview = self.config.get("preview", "")
        # Override preview from FlowFile content if present
        content = flowfile.get_content().decode("utf-8", errors="replace").strip()
        if content and not preview:
            preview = content[:100]

        conv_id = uuid.uuid4().hex[:16]

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        # Initialize with an empty message list
        store.save(conv_id, [], user_id=user_id)

        # Set preview if provided
        if preview:
            store.set_extra(conv_id, "_preview", preview)

        logger.info(f"[createConversation] Created {conv_id[:8]} for user {user_id}")

        # Set conversation_id as FlowFile attribute so downstream tasks can use it
        flowfile.set_attribute("conversation_id", conv_id)
        flowfile.set_content(json.dumps({
            "conversation_id": conv_id,
            "user_id": user_id,
        }).encode())
        return [flowfile]


TaskFactory.register(CreateConversationTask)
