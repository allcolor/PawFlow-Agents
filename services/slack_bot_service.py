"""SlackBotService — Bidirectional Slack bot via slack_sdk (optional).

Two modes:
- Socket Mode (preferred, no public URL): uses app_token (xapp-)
- Events API (webhook): registers on HTTPListenerService

Config:
    bot_token: str        — Bot token (xoxb-)
    app_token: str        — App token for Socket Mode (xapp-)
    signing_secret: str   — Signing secret for Events API verification
    mode: str             — "socket" (default) or "events"
    webhook_port: int     — HTTPListener port for Events API (default: 9090)
    webhook_path: str     — Events API path (default: /slack/events)
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core import ServiceFactory
from services.base_messaging_service import BaseMessagingService

logger = logging.getLogger(__name__)


class SlackBotService(BaseMessagingService):
    """Bidirectional Slack bot service."""

    TYPE = "slackBot"
    DESCRIPTION = "Slack Bot (Socket Mode or Events API)"
    TAGS = ["slack", "bot", "messaging"]
    CHANNEL_NAME = "slack"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._bot_token = self.config.get("bot_token", "")
        self._app_token = self.config.get("app_token", "")
        self._signing_secret = self.config.get("signing_secret", "")
        self._mode = self.config.get("mode", "socket")
        self._webhook_port = int(self.config.get("webhook_port", 9090))
        self._webhook_path = self.config.get("webhook_path", "/slack/events")
        self._client = None
        self._socket_handler = None
        self._bot_user_id = ""

    def _create_connection(self):
        if not self._bot_token:
            raise ValueError("bot_token is required for Slack")

        try:
            from slack_sdk import WebClient
        except ImportError:
            raise ImportError(
                "slack_sdk is required for Slack bot. "
                "Install with: pip install slack_sdk"
            )

        self._client = WebClient(token=self._bot_token)

        # Get bot user ID to filter own messages
        try:
            auth = self._client.auth_test()
            self._bot_user_id = auth.get("user_id", "")
            logger.info(f"Slack bot connected: {auth.get('user', '?')}")
        except Exception as e:
            raise RuntimeError(f"Slack auth_test failed: {e}")

        if self._mode == "socket":
            self._start_socket_mode()
        else:
            self._register_events_api()

        return {"status": "connected", "bot_user_id": self._bot_user_id}

    def _start_socket_mode(self):
        """Start Socket Mode listener."""
        if not self._app_token:
            raise ValueError("app_token (xapp-) is required for Socket Mode")

        try:
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            raise ImportError("slack_sdk[socket-mode] required for Socket Mode")

        socket_client = SocketModeClient(
            app_token=self._app_token,
            web_client=self._client,
        )

        def handle_event(client, req: SocketModeRequest):
            if req.type == "events_api":
                event = req.payload.get("event", {})
                self._process_event(event)
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )

        socket_client.socket_mode_request_listeners.append(handle_event)

        # Start in daemon thread
        self._socket_handler = socket_client
        t = threading.Thread(target=socket_client.connect, daemon=True, name="slack-socket")
        t.start()
        logger.info("Slack Socket Mode started")

    def _register_events_api(self):
        """Register Events API webhook on HTTPListenerService."""
        try:
            from services.http_listener_service import HTTPListenerService
            listener = HTTPListenerService.get_instance(self._webhook_port)
            listener.register_route(
                self._webhook_path, "POST", self._handle_events_api,
            )
            logger.info(f"Slack Events API webhook at :{self._webhook_port}{self._webhook_path}")
        except Exception as e:
            logger.warning(f"Could not register Slack webhook: {e}")

    def _handle_events_api(self, request: dict) -> dict:
        """Handle Slack Events API POST."""
        body = request.get("body", "{}")
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"status": 400, "body": "Invalid JSON"}

        # URL verification challenge
        if data.get("type") == "url_verification":
            return {
                "status": 200,
                "body": json.dumps({"challenge": data.get("challenge", "")}),
                "content_type": "application/json",
            }

        event = data.get("event", {})
        self._process_event(event)
        return {"status": 200, "body": "OK"}

    def _process_event(self, event: dict):
        """Process a Slack event and dispatch to handlers."""
        if event.get("type") != "message":
            return

        # Filter bot messages (avoid loop)
        if event.get("bot_id") or event.get("user") == self._bot_user_id:
            return

        # Skip message subtypes (edited, deleted, etc.)
        if event.get("subtype"):
            return

        update = {
            "channel_id": event.get("channel", ""),
            "user_id": event.get("user", ""),
            "username": "",  # resolved later if needed
            "team_id": event.get("team", ""),
            "message_id": event.get("ts", ""),
            "thread_ts": event.get("thread_ts", ""),
            "content": event.get("text", ""),
            "message_type": "text",
        }
        self._dispatch(update)

    def _poll_loop(self):
        """Not used — Slack uses Socket Mode or Events API."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1)

    def send_message(self, channel_id: str, text: str, **kwargs) -> dict:
        """Send a message to a Slack channel."""
        if not self._client:
            raise RuntimeError("Slack bot not connected")

        thread_ts = kwargs.get("thread_ts", "")
        try:
            params = {
                "channel": channel_id,
                "text": text,
            }
            if thread_ts:
                params["thread_ts"] = thread_ts

            result = self._client.chat_postMessage(**params)
            return {
                "message_id": result.get("ts", ""),
                "channel_id": channel_id,
            }
        except Exception as e:
            raise RuntimeError(f"Slack send failed: {e}")

    def _close_connection(self):
        super()._close_connection()
        if self._socket_handler:
            try:
                self._socket_handler.close()
            except Exception:
                pass
            self._socket_handler = None
        self._client = None
        self._bot_user_id = ""

    def ensure_connected(self):
        if not self._initialized:
            self.connect()


ServiceFactory.register(SlackBotService)
