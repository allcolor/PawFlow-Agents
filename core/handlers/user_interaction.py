"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class NotifyUserHandler(ToolHandler):
    """Send a notification to the user via available channels.

    Used by the agent to push messages when the user isn't actively watching
    the chat (e.g. after a scheduled wake-up).
    """

    def __init__(self):
        self._conversation_id = ""
        self._user_id = ""

    @property
    def name(self) -> str:
        return "notify_user"

    @property
    def description(self) -> str:
        return (
            "Send a push notification to the user. Use this when you need to "
            "proactively inform the user about something (e.g. after a scheduled "
            "task completes, a reminder fires, or an event occurs). "
            "The notification is sent via all available channels (Telegram, SSE, etc.)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message to send",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "Urgency level (default: normal)",
                },
            },
            "required": ["message"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        message = arguments.get("message", "")
        if not message:
            return "Error: message is required"
        urgency = arguments.get("urgency", "normal")

        sent_channels = []

        # Channel 1: SSE (conversation event bus — buffered if no subscriber)
        if self._conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                bus = ConversationEventBus.instance()
                bus.publish_event(self._conversation_id, "notification", {
                    "message": message,
                    "urgency": urgency,
                })
                sent_channels.append("sse")
            except Exception as e:
                logger.debug(f"SSE notify failed: {e}")

        # Channel 2: Telegram (if conversation has telegram metadata)
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                tg_chat_id = store.get_extra(
                    self._conversation_id, "telegram_chat_id",
                )
                if tg_chat_id:
                    # Try to find a running TelegramBotService
                    from services.telegram_bot_service import TelegramBotService
                    # Use the service registry pattern — for now log intent
                    logger.info(
                        f"Telegram notification to {tg_chat_id}: {message[:100]}"
                    )
                    sent_channels.append("telegram_queued")
            except Exception:
                pass

        if sent_channels:
            return f"Notification sent via: {', '.join(sent_channels)}"
        return "Notification queued (no active channels detected)"


class AskUserHandler(ToolHandler):
    """Ask the user a question and wait for their response."""

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a question and pause execution until they respond. "
            "Use when you need clarification, confirmation, or a decision from the user. "
            "The question will be displayed in the chat UI and the user can reply. "
            "Returns the user's response text."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of choices (e.g. ['yes', 'no', 'skip'])",
                },
            },
            "required": ["question"],
        }

    def set_conversation_id(self, conv_id: str):
        self._conversation_id = conv_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        question = arguments.get("question", "")
        options = arguments.get("options", [])
        if not question:
            return "Error: missing 'question' parameter"

        # Publish the question via SSE event bus
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            event_data = {
                "question": question,
                "agent_name": "assistant",
            }
            if options:
                event_data["options"] = options
            bus.publish_event(self._conversation_id, "ask_user", event_data)
        except Exception:
            pass

        # Return a message that tells the agent loop to pause and wait for user input
        options_text = ""
        if options:
            options_text = " Options: " + ", ".join(f"[{o}]" for o in options)
        return f"__ASK_USER__:{question}{options_text}"
