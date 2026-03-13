"""BaseMessagingService — Abstract base for bidirectional messaging services.

Provides common patterns for Telegram-like messaging services:
- Callback registration/dispatch
- Background polling thread management
- Thread safety

Subclasses implement: send_message(), _poll_loop()
"""

import logging
import threading
from abc import abstractmethod
from typing import Any, Callable, Dict, Optional

from core.base_service import BaseService

logger = logging.getLogger(__name__)


class BaseMessagingService(BaseService):
    """Abstract base for messaging channel services."""

    CHANNEL_NAME: str = ""  # override: "discord", "whatsapp", "slack"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._callbacks: Dict[str, Callable] = {}  # owner_id -> callback
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def register_handler(self, owner_id: str, callback: Callable):
        """Register a callback for incoming messages.

        callback receives (update: dict) with channel-specific fields.
        """
        with self._lock:
            self._callbacks[owner_id] = callback
        self._ensure_polling()

    def unregister_handler(self, owner_id: str):
        """Remove a registered callback."""
        with self._lock:
            self._callbacks.pop(owner_id, None)

    def _ensure_polling(self):
        """Start the polling/listener thread if not running."""
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True,
            name=f"{self.CHANNEL_NAME}-poll",
        )
        self._poll_thread.start()

    def _dispatch(self, update: dict):
        """Dispatch an incoming update to all registered callbacks."""
        with self._lock:
            callbacks = list(self._callbacks.values())
        for cb in callbacks:
            try:
                cb(update)
            except Exception as e:
                logger.error(f"{self.CHANNEL_NAME} callback error: {e}")

    @abstractmethod
    def send_message(self, channel_id: str, text: str, **kwargs) -> dict:
        """Send a text message to a channel/user. Returns API response dict."""

    @abstractmethod
    def _poll_loop(self):
        """Background thread for receiving messages. Must check self._stop_event."""

    def _close_connection(self):
        """Stop polling on service close."""
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None
