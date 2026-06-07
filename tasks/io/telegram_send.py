"""telegramSend — Send messages back to Telegram.

Can be used as:
1. A standalone task in a flow (after agentLoop)
2. A ToolHandler for agent tool-use (send_telegram)

Config (task mode):
    service_id: str    — ID of the TelegramBotService
    chat_id: str       — Target chat ID (or ${telegram.chat_id} expression)
    parse_mode: str    — "Markdown" or "HTML" (default: Markdown)
"""

import json
import logging
import base64
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)


class TelegramSendTask(BaseTask):
    """Send FlowFile content as a Telegram message."""

    TYPE = "telegramSend"
    VERSION = "1.0.0"
    NAME = "Telegram Send"
    DESCRIPTION = "Send a message to a Telegram chat"
    ICON = "telegram"
    TAGS = ["telegram", "io"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string",
                "description": "ID of the TelegramBotService",
                "required": True,
            },
            "chat_id": {
                "type": "string",
                "description": "Target chat ID (supports expressions like ${telegram.chat_id})",
                "required": False,
                "default": "${telegram.chat_id}",
            },
            "parse_mode": {
                "type": "string",
                "description": "Message format",
                "required": False,
                "default": "Markdown",
                "allowable_values": ["Markdown", "HTML", ""],
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"TelegramBotService '{service_id}' not found")
        svc.ensure_connected()

        # Resolve chat_id from config or FlowFile attribute
        chat_id_expr = self.config.get("chat_id", "${telegram.chat_id}")
        chat_id = self.resolve_value(chat_id_expr, flowfile=flowfile)
        if not chat_id:
            raise ValueError("No chat_id available (configure or set telegram.chat_id)")

        parse_mode = self.config.get("parse_mode", "Markdown")
        text = flowfile.get_content().decode("utf-8", errors="replace")
        reply_markup = _parse_reply_markup(
            flowfile.get_attribute("telegram.reply_markup") or "")

        if not text.strip():
            logger.warning("telegramSend: empty message, skipping")
            return [flowfile]

        reply_to = int(flowfile.get_attribute("telegram.message_id") or 0)

        # Check for user-owned bot token (send via their personal bot)
        user_bot_token = self._resolve_user_bot_token(flowfile)

        try:
            if user_bot_token:
                from services.telegram_bot_service import TelegramBotPool
                bot_pool = TelegramBotPool.instance()
                result = bot_pool.send_message(
                    user_bot_token, chat_id, text, parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
                self._send_tts_audio(bot_pool, user_bot_token, chat_id, flowfile)
            else:
                result = svc.send_message(
                    chat_id, text, parse_mode=parse_mode, reply_to=reply_to,
                    reply_markup=reply_markup,
                )
                self._send_tts_audio(svc, "", chat_id, flowfile)
            flowfile.set_attribute("telegram.sent_message_id",
                                   str(result.get("message_id", "")))
            flowfile.set_attribute("telegram.send_status", "sent")
        except Exception as e:
            logger.error(f"telegramSend error: {e}")
            flowfile.set_attribute("telegram.send_status", "error")
            flowfile.set_attribute("telegram.send_error", str(e))

        return [flowfile]

    @staticmethod
    def _send_tts_audio(sender: Any, token: str, chat_id: str,
                        flowfile: FlowFile) -> None:
        raw = flowfile.get_attribute("telegram.tts_audio_base64") or ""
        if not raw:
            return
        try:
            audio = base64.b64decode(raw)
            filename = flowfile.get_attribute("telegram.tts_filename") or "speech.mp3"
            content_type = flowfile.get_attribute("telegram.tts_content_type") or "audio/mpeg"
            if token:
                sender.send_audio(token, chat_id, audio, filename=filename,
                                  content_type=content_type)
            else:
                sender.send_audio(chat_id, audio, filename=filename,
                                  content_type=content_type)
        except Exception as exc:
            logger.warning("telegramSend TTS audio failed: %s", exc, exc_info=True)

    def _resolve_user_bot_token(self, flowfile: FlowFile) -> Optional[str]:
        """Check if the target user has a personal bot token."""
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        if not tg_user_id:
            return None
        try:
            from core.identity_service import IdentityService
            ids = IdentityService.instance()
            resolved_user = ids.resolve_user("telegram", tg_user_id)
            if resolved_user:
                return ids.get_bot_token(resolved_user, "telegram")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None


def _parse_reply_markup(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("telegramSend: invalid telegram.reply_markup JSON")
        return None
    return value if isinstance(value, dict) else None


class TelegramSendHandler(ToolHandler):
    """Agent tool handler for sending Telegram messages."""

    def __init__(self):
        self._service = None

    @property
    def name(self) -> str:
        return "send_telegram"

    @property
    def description(self) -> str:
        return "Send a message to a Telegram chat"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID to send to",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send (Markdown supported)",
                },
            },
            "required": ["chat_id", "text"],
        }

    def set_service(self, svc):
        """Set the TelegramBotService instance."""
        self._service = svc

    def execute(self, arguments: Dict[str, Any]) -> str:
        chat_id = arguments.get("chat_id", "")
        text = arguments.get("text", "")
        if not chat_id or not text:
            return "Error: chat_id and text are required"

        if not self._service:
            return "Error: Telegram bot service not configured"

        try:
            self._service.ensure_connected()
            result = self._service.send_message(chat_id, text)
            return f"Message sent (id: {result.get('message_id', '?')})"
        except Exception as e:
            return f"Error sending message: {e}"


TaskFactory.register(TelegramSendTask)
