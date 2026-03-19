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
        self.username = self.config.get('username', 'OpenPaw')
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
            'username': {'type': 'string', 'required': False, 'default': 'OpenPaw'},
            'icon_emoji': {'type': 'string', 'required': False, 'default': ':robot_face:'},
            'message': {'type': 'string', 'required': False, 'description': 'Custom message (default: FlowFile content)'},
            'use_flowfile_content': {'type': 'boolean', 'required': False, 'default': False},
        }


class SlackSendTask(BaseTask):
    """Send FlowFile content as a Slack message via SlackBotService (bidirectional)."""

    TYPE = "slackSend"
    VERSION = "1.0.0"
    NAME = "Slack Send"
    DESCRIPTION = "Send a message to Slack via bot (bidirectional)"
    ICON = "hash"
    TAGS = ["slack", "io"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise TaskError(f"SlackBotService '{service_id}' not found")
        svc.ensure_connected()

        channel_id_expr = self.config.get("channel_id", "${slack.channel_id}")
        channel_id = self._resolve_attribute_value(flowfile, channel_id_expr)
        if not channel_id:
            raise TaskError("slackSend: no channel_id available")

        text = flowfile.get_content().decode('utf-8', errors='replace')
        if not text.strip():
            logger.warning("slackSend: empty message, skipping")
            return [flowfile]

        thread_ts = flowfile.get_attribute("slack.thread_ts") or ""
        try:
            result = svc.send_message(channel_id, text, thread_ts=thread_ts)
            flowfile.set_attribute("slack.sent_message_id",
                                   str(result.get("message_id", "")))
            flowfile.set_attribute("slack.send_status", "sent")
        except Exception as e:
            logger.error(f"slackSend error: {e}")
            flowfile.set_attribute("slack.send_status", "error")
            flowfile.set_attribute("slack.send_error", str(e))

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'service_id': {'type': 'string', 'required': True, 'description': 'SlackBotService ID'},
            'channel_id': {'type': 'string', 'required': False, 'default': '${slack.channel_id}',
                           'description': 'Target channel ID'},
        }


class SlackSendHandler:
    """Agent tool handler for sending Slack messages via bot."""

    def __init__(self):
        self._service = None

    @property
    def name(self) -> str:
        return "send_slack"

    @property
    def description(self) -> str:
        return "Send a message to a Slack channel via bot"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Slack channel ID to send to",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Thread timestamp for threaded reply (optional)",
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
            return "Error: Slack bot service not configured"
        try:
            self._service.ensure_connected()
            thread_ts = arguments.get("thread_ts", "")
            result = self._service.send_message(
                channel_id, text, thread_ts=thread_ts,
            )
            return f"Message sent (ts: {result.get('message_id', '?')})"
        except Exception as e:
            return f"Error sending message: {e}"


from core import TaskFactory
TaskFactory.register(NotifySlackTask)
TaskFactory.register(SlackSendTask)
