"""telegramApi — generic Telegram Bot API call task.

Calls any Telegram Bot API method (service_id + method + params) and returns
the JSON result. The core stays verb-agnostic: moderation flows compose their
own calls (banChatMember, restrictChatMember, deleteMessage, getChatMember,
leaveChat, ...) rather than relying on hardcoded ban/mute helpers.

Config:
    service_id: str      — ID of the TelegramBotService
    method: str          — Bot API method name (e.g. banChatMember)
    params: str          — JSON object of params; string values support
                           ${attr} expressions ({"chat_id": "${telegram.chat_id}"})
    raise_on_error: bool — when true, a Telegram API error fails the task;
                           otherwise it sets telegram.api_ok=false and continues

FlowFile attributes set:
    telegram.api_ok      — "true"/"false"
    telegram.api_method  — the method called
    telegram.api_error   — error description when not ok
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class TelegramApiTask(BaseTask):
    """Call an arbitrary Telegram Bot API method."""

    TYPE = "telegramApi"
    VERSION = "1.0.0"
    NAME = "Telegram API"
    DESCRIPTION = "Call any Telegram Bot API method and return JSON"
    ICON = "telegram"
    TAGS = ["telegram", "io"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string",
                "description": "ID of the TelegramBotService",
                "required": True,
            },
            "method": {
                "type": "string",
                "description": "Bot API method (supports ${...} expressions)",
                "required": True,
            },
            "params": {
                "type": "string",
                "description": (
                    "JSON object of method params; string values support "
                    "${attr} expressions"
                ),
                "required": False,
                "default": "",
            },
            "raise_on_error": {
                "type": "boolean",
                "description": "Fail the task on a Telegram API error",
                "required": False,
                "default": False,
            },
        }

    def _resolve_params(self, raw: str,
                        flowfile: FlowFile) -> Optional[Dict[str, Any]]:
        raw = (raw or "").strip()
        if not raw:
            return None
        # Whole-string expansion first (e.g. params = "${telegram.raw}").
        rendered = self.resolve_value(raw, flowfile=flowfile)
        try:
            parsed = json.loads(rendered)
        except (ValueError, TypeError) as e:
            raise TaskError(f"telegramApi: params is not valid JSON: {e}")
        if not isinstance(parsed, dict):
            raise TaskError("telegramApi: params must be a JSON object")
        return self._resolve_leaves(parsed, flowfile)

    def _resolve_leaves(self, value: Any, flowfile: FlowFile) -> Any:
        if isinstance(value, str):
            return self.resolve_value(value, flowfile=flowfile)
        if isinstance(value, dict):
            return {k: self._resolve_leaves(v, flowfile) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_leaves(v, flowfile) for v in value]
        return value

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        method = self.resolve_value(
            self.config.get("method", ""), flowfile=flowfile).strip()
        if not method:
            raise TaskError("telegramApi: 'method' is required")

        params = self._resolve_params(self.config.get("params", ""), flowfile)

        svc = self.get_service(service_id) if service_id else None
        raise_on_error = bool(self.config.get("raise_on_error", False))
        flowfile.set_attribute("telegram.api_method", method)

        try:
            if svc is not None:
                result = svc.call_api(method, params)
            else:
                # Fall back to the multi-bot pool using the receiving token.
                token = flowfile.get_attribute("telegram.bot_token") or ""
                if not token:
                    raise TaskError(
                        f"telegramApi: service '{service_id}' not found and no "
                        "telegram.bot_token on the FlowFile")
                from services.telegram_bot_service import TelegramBotPool
                result = TelegramBotPool.instance().call_api(token, method, params)
        except TaskError:
            raise
        except Exception as e:
            logger.warning(f"telegramApi {method} failed: {e}")
            flowfile.set_attribute("telegram.api_ok", "false")
            flowfile.set_attribute("telegram.api_error", str(e))
            if raise_on_error:
                raise TaskError(f"telegramApi {method} failed: {e}")
            return [flowfile]

        flowfile.set_attribute("telegram.api_ok", "true")
        flowfile.set_content(
            json.dumps(result, ensure_ascii=False).encode("utf-8"))
        flowfile.set_attribute("mime.type", "application/json")
        return [flowfile]


TaskFactory.register(TelegramApiTask)
