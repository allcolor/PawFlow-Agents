"""whatsappSend — Send messages via WhatsApp Cloud API.

Config (task mode):
    service_id: str    — ID of the WhatsAppService
    phone: str         — Target phone number (or ${whatsapp.phone} expression)
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.tool_registry import ToolHandler
from tasks.io.base_messaging_tasks import BaseSendTask

logger = logging.getLogger(__name__)


class WhatsAppSendTask(BaseSendTask):
    """Send FlowFile content as a WhatsApp message."""

    TYPE = "whatsappSend"
    VERSION = "1.0.0"
    NAME = "WhatsApp Send"
    DESCRIPTION = "Send a message via WhatsApp"
    ICON = "phone"
    TAGS = ["whatsapp", "io"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string",
                "description": "ID of the WhatsAppService",
                "required": True,
            },
            "phone": {
                "type": "string",
                "description": "Target phone number (supports ${whatsapp.phone})",
                "required": False,
                "default": "${whatsapp.phone}",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"WhatsAppService '{service_id}' not found")
        svc.ensure_connected()

        phone_expr = self.config.get("phone", "${whatsapp.phone}")
        phone = self._resolve_attr(flowfile, phone_expr)
        if not phone:
            raise ValueError("No phone number available")

        text = flowfile.get_content().decode("utf-8", errors="replace")
        if not text.strip():
            logger.warning("whatsappSend: empty message, skipping")
            return [flowfile]

        try:
            result = svc.send_message(phone, text)
            flowfile.set_attribute("whatsapp.sent_message_id",
                                   str(result.get("message_id", "")))
            flowfile.set_attribute("whatsapp.send_status", "sent")
        except Exception as e:
            logger.error(f"whatsappSend error: {e}")
            flowfile.set_attribute("whatsapp.send_status", "error")
            flowfile.set_attribute("whatsapp.send_error", str(e))

        return [flowfile]


    def _resolve_attr(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)


class WhatsAppSendHandler(ToolHandler):
    """Agent tool handler for sending WhatsApp messages."""

    def __init__(self):
        self._service = None

    @property
    def name(self) -> str:
        return "send_whatsapp"

    @property
    def description(self) -> str:
        return "Send a message via WhatsApp"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Phone number to send to (with country code)",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
            },
            "required": ["phone", "text"],
        }

    def set_service(self, svc):
        self._service = svc

    def execute(self, arguments: Dict[str, Any]) -> str:
        phone = arguments.get("phone", "")
        text = arguments.get("text", "")
        if not phone or not text:
            return "Error: phone and text are required"
        if not self._service:
            return "Error: WhatsApp service not configured"
        try:
            self._service.ensure_connected()
            result = self._service.send_message(phone, text)
            return f"Message sent (id: {result.get('message_id', '?')})"
        except Exception as e:
            return f"Error sending message: {e}"


TaskFactory.register(WhatsAppSendTask)
