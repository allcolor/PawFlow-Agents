"""WhatsAppService — WhatsApp Cloud API (Meta) via stdlib HTTP.

No external dependencies — uses http.client for all API calls.
Receives messages via webhook on HTTPListenerService.

Config:
    phone_number_id: str    — WhatsApp phone number ID
    access_token: str       — Meta access token
    verify_token: str       — Webhook verification token
    api_version: str        — Graph API version (default: v21.0)
    webhook_port: int       — HTTPListener port for webhook (default: 9090)
    webhook_path: str       — Webhook path (default: /whatsapp/webhook)
"""

import hashlib
import hmac
import json
import http.client
import logging
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core import ServiceFactory
from services.base_messaging_service import BaseMessagingService

logger = logging.getLogger(__name__)

_GRAPH_HOST = "graph.facebook.com"
_RATE_LIMIT = 80  # max messages per second


class WhatsAppService(BaseMessagingService):
    """WhatsApp Cloud API service."""

    TYPE = "whatsappCloud"
    DESCRIPTION = "WhatsApp Cloud API (Meta)"
    TAGS = ["whatsapp", "messaging"]
    CHANNEL_NAME = "whatsapp"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._phone_number_id = self.config.get("phone_number_id", "")
        self._access_token = self.config.get("access_token", "")
        self._verify_token = self.config.get("verify_token", "")
        self._api_version = self.config.get("api_version", "v21.0")
        self._webhook_port = int(self.config.get("webhook_port", 9090))
        self._webhook_path = self.config.get("webhook_path", "/whatsapp/webhook")
        self._webhook_registered = False
        self._rate_tokens = _RATE_LIMIT
        self._rate_last = time.time()
        self._rate_lock = threading.Lock()

    def _create_connection(self):
        if not self._phone_number_id or not self._access_token:
            raise ValueError("phone_number_id and access_token are required")

        # Register webhook on HTTPListenerService
        self._register_webhook()
        logger.info(f"WhatsApp service connected (phone: {self._phone_number_id})")
        return {"status": "connected"}

    def _register_webhook(self):
        """Register GET/POST handlers on HTTPListenerService."""
        if self._webhook_registered:
            return
        try:
            from services.http_listener_service import HTTPListenerService
            listener = HTTPListenerService.get_instance(self._webhook_port)
            listener.register_route(
                self._webhook_path, "GET", self._handle_verify,
            )
            listener.register_route(
                self._webhook_path, "POST", self._handle_webhook,
            )
            self._webhook_registered = True
            logger.info(f"WhatsApp webhook registered at :{self._webhook_port}{self._webhook_path}")
        except Exception as e:
            logger.warning(f"Could not register WhatsApp webhook: {e}")

    def _handle_verify(self, request: dict) -> dict:
        """Handle webhook verification GET request (hub.challenge)."""
        params = request.get("query_params", {})
        mode = params.get("hub.mode", "")
        token = params.get("hub.verify_token", "")
        challenge = params.get("hub.challenge", "")

        if mode == "subscribe" and token == self._verify_token:
            return {"status": 200, "body": challenge, "content_type": "text/plain"}
        return {"status": 403, "body": "Verification failed"}

    def _handle_webhook(self, request: dict) -> dict:
        """Handle incoming WhatsApp message POST."""
        try:
            body = request.get("body", "{}")
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            data = json.loads(body)

            # Parse WhatsApp Cloud API webhook format
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])

                    contact_map = {}
                    for c in contacts:
                        wa_id = c.get("wa_id", "")
                        name = c.get("profile", {}).get("name", "")
                        contact_map[wa_id] = name

                    for msg in messages:
                        phone = msg.get("from", "")
                        msg_type = msg.get("type", "text")

                        text = ""
                        if msg_type == "text":
                            text = msg.get("text", {}).get("body", "")
                        elif msg_type == "image":
                            text = msg.get("image", {}).get("caption", "(image)")
                        elif msg_type == "document":
                            text = msg.get("document", {}).get("caption", "(document)")
                        else:
                            text = f"({msg_type})"

                        update = {
                            "phone": phone,
                            "name": contact_map.get(phone, ""),
                            "message_id": msg.get("id", ""),
                            "message_type": msg_type,
                            "content": text,
                            "timestamp": msg.get("timestamp", ""),
                        }
                        self._dispatch(update)

        except Exception as e:
            logger.error(f"WhatsApp webhook parse error: {e}")

        return {"status": 200, "body": "OK"}

    def _poll_loop(self):
        """Not used — WhatsApp uses webhook for receiving."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1)

    def _check_rate_limit(self):
        """Simple token bucket rate limiter."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._rate_last
            self._rate_tokens = min(
                _RATE_LIMIT,
                self._rate_tokens + elapsed * _RATE_LIMIT,
            )
            self._rate_last = now
            if self._rate_tokens < 1:
                wait = (1 - self._rate_tokens) / _RATE_LIMIT
                time.sleep(wait)
                self._rate_tokens = 0
            else:
                self._rate_tokens -= 1

    def send_message(self, phone_number: str, text: str, **kwargs) -> dict:
        """Send a text message via WhatsApp Cloud API."""
        self._check_rate_limit()

        path = f"/{self._api_version}/{self._phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": text},
        }

        return self._graph_api_post(path, body)

    def _graph_api_post(self, path: str, body: dict) -> dict:
        """POST to Meta Graph API."""
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(_GRAPH_HOST, timeout=15, context=ctx)
        try:
            json_body = json.dumps(body).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(json_body)),
            }
            conn.request("POST", path, body=json_body, headers=headers)
            response = conn.getresponse()
            resp_body = response.read().decode("utf-8")
            if response.status >= 400:
                raise RuntimeError(f"WhatsApp API error {response.status}: {resp_body[:500]}")
            result = json.loads(resp_body)
            # Extract message ID from response
            messages = result.get("messages", [])
            msg_id = messages[0].get("id", "") if messages else ""
            return {"message_id": msg_id, "status": "sent"}
        finally:
            conn.close()

    def _close_connection(self):
        super()._close_connection()
        # Unregister webhook routes if possible
        self._webhook_registered = False

    def ensure_connected(self):
        if not self._initialized:
            self.connect()


ServiceFactory.register(WhatsAppService)
