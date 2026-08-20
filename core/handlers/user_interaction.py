"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import logging
from typing import Dict, Any

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
        from core.handlers._arg_normalize import normalize_string_list
        question = arguments.get("question", "")
        options = normalize_string_list(arguments.get("options"))
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Return a message that tells the agent loop to pause and wait for user input
        options_text = ""
        if options:
            options_text = " Options: " + ", ".join(f"[{o}]" for o in options)
        return f"__ASK_USER__:{question}{options_text}"


class RequestConfirmationHandler(ToolHandler):
    """Create a DURABLE confirmation request the user answers whenever.

    Unlike ask_user (an ephemeral question in the live stream), the request
    survives reloads and restarts, shows in the pending-confirmations panel,
    and when the user answers — minutes or days later — the agent is WOKEN
    with the answer and continues from where it left off.
    """

    _conversation_id: str = ""
    _user_id: str = ""
    _agent_name: str = ""

    @property
    def name(self) -> str:
        return "request_confirmation"

    @property
    def description(self) -> str:
        return (
            "Ask the user for a durable confirmation: yes/no (mode "
            "'confirm'), one choice from a list (mode 'choice'), or several "
            "(mode 'multi'). The request stays pending until the user "
            "answers — possibly hours or days later; you will be WOKEN with "
            "the answer, so end your turn after calling this (optionally "
            "set wait_seconds to poll briefly for an immediate answer). "
            "Use it for approvals and decisions that must not be lost; use "
            "ask_user only for quick, live questions."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "The question shown to the user"},
                "title": {"type": "string",
                          "description": "Short title for the pending panel"},
                "mode": {"type": "string",
                         "enum": ["confirm", "choice", "multi"],
                         "description": "confirm = yes/no (default); choice "
                                        "= pick one option; multi = pick "
                                        "several"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "Choices for choice/multi (2+)"},
                "expires_in": {"type": "string",
                               "description": "Optional expiry: '2h', '3d', "
                                              "'1mo'... absent = no expiry"},
                "wait_seconds": {"type": "integer",
                                 "description": "Poll up to N seconds for an "
                                                "immediate answer (default 0: "
                                                "return at once, resume on "
                                                "wake-up)"},
            },
            "required": ["message"],
        }

    def set_conversation_id(self, conv_id: str):
        self._conversation_id = conv_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.confirmation_store import (
            ConfirmationStore, parse_timeout_seconds)
        message = str(arguments.get("message") or "").strip()
        if not message:
            return "Error: missing 'message' parameter"
        if not self._conversation_id or not self._user_id:
            return "Error: no conversation context for the confirmation"
        try:
            expires = parse_timeout_seconds(arguments.get("expires_in"))
        except ValueError as exc:
            return f"Error: invalid expires_in: {exc}"
        store = ConfirmationStore.instance()
        try:
            record = store.create_confirmation(
                conversation_id=self._conversation_id,
                user_id=self._user_id,
                requester_kind="agent",
                requester=self._agent_name or "assistant",
                message=message,
                title=str(arguments.get("title") or ""),
                mode=str(arguments.get("mode") or "confirm"),
                options=arguments.get("options"),
                expires_in_seconds=expires,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        request_id = record["request_id"]
        import json as _json
        import time as _time
        wait_seconds = min(int(arguments.get("wait_seconds") or 0), 120)
        deadline = _time.time() + wait_seconds
        while wait_seconds and _time.time() < deadline:
            _time.sleep(1.0)
            current = store.get_confirmation(request_id)
            if current and current["status"] == "answered":
                return (f"The user answered confirmation {request_id}: "
                        + _json.dumps(current["answer"], ensure_ascii=False))
            if current and current["status"] in ("cancelled", "expired"):
                return f"Confirmation {request_id} was {current['status']}."
        return (
            f"Confirmation {request_id} created and pending. The user can "
            "answer at any time (even days later); you will be woken with "
            "the answer. Do not invent it — finish your turn now."
        )
