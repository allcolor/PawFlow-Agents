"""slackReceiver — Self-triggering source task for Slack bot messages.

Config:
    service_id: str    — ID of the SlackBotService in the flow

FlowFile attributes set:
    slack.channel_id    — channel ID
    slack.user_id       — sender's Slack user ID
    slack.username      — sender's username (may be empty)
    slack.team_id       — workspace team ID
    slack.message_id    — message timestamp (ts)
    slack.thread_ts     — thread timestamp (for threaded replies)
"""

import logging
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from tasks.io.base_messaging_tasks import BaseReceiverTask

logger = logging.getLogger(__name__)


class SlackReceiverTask(BaseReceiverTask):
    """Self-triggering source task that receives Slack messages."""

    TYPE = "slackReceiver"
    VERSION = "1.0.0"
    NAME = "Slack Receiver"
    DESCRIPTION = "Receive messages from a Slack bot"
    ICON = "hash"
    TAGS = ["slack", "io", "source"]

    PARAMETERS = {
        "service_id": {
            "type": "string",
            "description": "ID of the SlackBotService",
            "required": True,
        },
    }

    def initialize(self):
        if self._registered:
            return
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"SlackBotService '{service_id}' not found")
        svc.ensure_connected()
        self._owner_id = f"slackReceiver_{id(self)}"
        svc.register_handler(self._owner_id, self._on_update)
        self._registered = True
        logger.info(f"slackReceiver registered on service '{service_id}'")

    def _parse_update(self, update: dict) -> Optional[FlowFile]:
        content = update.get("content", "")
        if not content:
            return None

        ff = FlowFile(content=content.encode("utf-8"))
        ff.set_attribute("slack.channel_id", update.get("channel_id", ""))
        ff.set_attribute("slack.user_id", update.get("user_id", ""))
        ff.set_attribute("slack.username", update.get("username", ""))
        ff.set_attribute("slack.team_id", update.get("team_id", ""))
        ff.set_attribute("slack.message_id", update.get("message_id", ""))
        ff.set_attribute("slack.thread_ts", update.get("thread_ts", ""))
        return ff

    def cleanup(self):
        if self._registered and self._owner_id:
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.unregister_handler(self._owner_id)
            self._registered = False


TaskFactory.register(SlackReceiverTask)
