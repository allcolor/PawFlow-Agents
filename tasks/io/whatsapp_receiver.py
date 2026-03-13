"""whatsappReceiver — Self-triggering source task for WhatsApp messages.

Config:
    service_id: str    — ID of the WhatsAppService in the flow

FlowFile attributes set:
    whatsapp.phone         — sender's phone number
    whatsapp.name          — sender's profile name
    whatsapp.message_id    — message ID
    whatsapp.message_type  — "text", "image", "document", etc.
"""

import logging
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from tasks.io.base_messaging_tasks import BaseReceiverTask

logger = logging.getLogger(__name__)


class WhatsAppReceiverTask(BaseReceiverTask):
    """Self-triggering source task that receives WhatsApp messages."""

    TYPE = "whatsappReceiver"
    VERSION = "1.0.0"
    NAME = "WhatsApp Receiver"
    DESCRIPTION = "Receive messages from WhatsApp"
    ICON = "phone"
    TAGS = ["whatsapp", "io", "source"]

    PARAMETERS = {
        "service_id": {
            "type": "string",
            "description": "ID of the WhatsAppService",
            "required": True,
        },
    }

    def initialize(self):
        if self._registered:
            return
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"WhatsAppService '{service_id}' not found")
        svc.ensure_connected()
        self._owner_id = f"whatsappReceiver_{id(self)}"
        svc.register_handler(self._owner_id, self._on_update)
        self._registered = True
        logger.info(f"whatsappReceiver registered on service '{service_id}'")

    def _parse_update(self, update: dict) -> Optional[FlowFile]:
        content = update.get("content", "")
        if not content:
            return None

        ff = FlowFile(content=content.encode("utf-8"))
        ff.set_attribute("whatsapp.phone", update.get("phone", ""))
        ff.set_attribute("whatsapp.name", update.get("name", ""))
        ff.set_attribute("whatsapp.message_id", update.get("message_id", ""))
        ff.set_attribute("whatsapp.message_type", update.get("message_type", "text"))
        return ff

    def cleanup(self):
        if self._registered and self._owner_id:
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.unregister_handler(self._owner_id)
            self._registered = False


TaskFactory.register(WhatsAppReceiverTask)
