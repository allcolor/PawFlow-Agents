"""discordSend — Send messages to Discord channels.

Can be used as a flow task or as an agent tool handler.

Config (task mode):
    service_id: str     — ID of the DiscordBotService
    channel_id: str     — Target channel ID (or ${discord.channel_id} expression)
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.tool_registry import ToolHandler
from tasks.io.base_messaging_tasks import BaseSendTask

logger = logging.getLogger(__name__)


class DiscordSendTask(BaseSendTask):
    """Send FlowFile content as a Discord message."""

    TYPE = "discordSend"
    VERSION = "1.0.0"
    NAME = "Discord Send"
    DESCRIPTION = "Send a message to a Discord channel"
    ICON = "message-circle"
    TAGS = ["discord", "io"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string",
                "description": "ID of the DiscordBotService",
                "required": True,
            },
            "channel_id": {
                "type": "string",
                "description": "Target channel ID (supports ${discord.channel_id})",
                "required": False,
                "default": "${discord.channel_id}",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"DiscordBotService '{service_id}' not found")
        svc.ensure_connected()

        channel_id_expr = self.config.get("channel_id", "${discord.channel_id}")
        channel_id = self._resolve_attr(flowfile, channel_id_expr)
        if not channel_id:
            raise ValueError("No channel_id available")

        text = flowfile.get_content().decode("utf-8", errors="replace")
        if not text.strip():
            logger.warning("discordSend: empty message, skipping")
            return [flowfile]

        try:
            result = svc.send_message(channel_id, text)
            flowfile.set_attribute("discord.sent_message_id",
                                   str(result.get("message_id", "")))
            flowfile.set_attribute("discord.send_status", "sent")
        except Exception as e:
            logger.error(f"discordSend error: {e}")
            flowfile.set_attribute("discord.send_status", "error")
            flowfile.set_attribute("discord.send_error", str(e))

        return [flowfile]


    def _resolve_attr(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)


class DiscordSendHandler(ToolHandler):
    """Agent tool handler for sending Discord messages."""

    def __init__(self):
        self._service = None

    @property
    def name(self) -> str:
        return "send_discord"

    @property
    def description(self) -> str:
        return "Send a message to a Discord channel"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Discord channel ID to send to",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
            },
            "required": ["channel_id", "text"],
        }

    def set_service(self, svc):
        self._service = svc

    def execute(self, arguments: Dict[str, Any]) -> str:
        channel_id = arguments.get("channel_id", "")
        text = arguments.get("text", "")
        if not channel_id or not text:
            return "Error: channel_id and text are required"
        if not self._service:
            return "Error: Discord bot service not configured"
        try:
            self._service.ensure_connected()
            result = self._service.send_message(channel_id, text)
            return f"Message sent (id: {result.get('message_id', '?')})"
        except Exception as e:
            return f"Error sending message: {e}"


TaskFactory.register(DiscordSendTask)
