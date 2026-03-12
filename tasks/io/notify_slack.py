# NotifySlack Task

"""Tâche NotifySlack - Envoyer des messages à Slack via webhook."""

import json
import logging
from typing import Dict, Any, List
from urllib.request import Request, urlopen
from urllib.error import URLError

from core import FlowFile, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class NotifySlackTask(BaseTask):
    """Envoyer un message à un channel Slack via webhook."""

    TYPE = "notifySlack"
    VERSION = "1.0.0"
    NAME = "Notify Slack"
    DESCRIPTION = "Envoyer un message à Slack via Incoming Webhook"
    ICON = "message-square"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook_url = self.config.get('webhook_url', '')
        self.channel = self.config.get('channel', '')
        self.username = self.config.get('username', 'PyFi2')
        self.icon_emoji = self.config.get('icon_emoji', ':robot_face:')
        self.message = self.config.get('message', '')
        self.use_flowfile_content = self.config.get('use_flowfile_content', False)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        webhook = self._resolve_attribute_value(flowfile, self.webhook_url)
        if not webhook:
            raise TaskError("notifySlack: webhook_url is required")

        if self.use_flowfile_content:
            text = flowfile.get_content().decode('utf-8', errors='replace')
        elif self.message:
            text = self._resolve_attribute_value(flowfile, self.message)
        else:
            text = flowfile.get_content().decode('utf-8', errors='replace')

        payload = {
            "text": text,
            "username": self.username,
            "icon_emoji": self.icon_emoji,
        }
        if self.channel:
            payload["channel"] = self.channel

        try:
            data = json.dumps(payload).encode('utf-8')
            req = Request(webhook, data=data, method='POST')
            req.add_header('Content-Type', 'application/json')
            with urlopen(req, timeout=15) as resp:
                resp.read()
        except URLError as e:
            raise TaskError(f"notifySlack: failed to send: {e}")

        flowfile.set_attribute('slack.sent', 'true')
        logger.info("Slack notification sent")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'webhook_url': {'type': 'secret', 'required': False, 'description': 'Slack Incoming Webhook URL'},
            'channel': {'type': 'string', 'required': False, 'description': 'Override channel (#channel)'},
            'username': {'type': 'string', 'required': False, 'default': 'PyFi2'},
            'icon_emoji': {'type': 'string', 'required': False, 'default': ':robot_face:'},
            'message': {'type': 'string', 'required': False, 'description': 'Custom message (default: FlowFile content)'},
            'use_flowfile_content': {'type': 'boolean', 'required': False, 'default': False},
        }


from core import TaskFactory
TaskFactory.register(NotifySlackTask)
