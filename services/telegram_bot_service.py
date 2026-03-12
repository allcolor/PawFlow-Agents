"""TelegramBotService — shared Telegram Bot API client.

Provides long-polling for updates and message sending via the Telegram Bot API.
Multiple tasks can share the same bot token. The service manages:
- Long-polling thread for receiving updates
- Callback dispatch to registered handlers
- Message/file sending methods

Config:
    bot_token: str       — Telegram bot token (from @BotFather)
    poll_timeout: int    — Long-poll timeout in seconds (default: 30)
    allowed_users: str   — Comma-separated Telegram user IDs (optional, empty = all)
"""

import json
import logging
import http.client
import ssl
import threading
import time
import queue
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

_API_HOST = "api.telegram.org"


class TelegramBotService(BaseService):
    """Shared Telegram bot service with long-polling."""

    TYPE = "telegramBot"
    DESCRIPTION = "Telegram Bot API connection (long-polling)"
    TAGS = ["telegram", "bot", "messaging"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._bot_token = self.config.get("bot_token", "")
        self._poll_timeout = int(self.config.get("poll_timeout", 30))
        self._allowed_users: set = set()
        allowed = self.config.get("allowed_users", "")
        if allowed:
            self._allowed_users = {
                uid.strip() for uid in allowed.split(",") if uid.strip()
            }
        self._callbacks: Dict[str, Callable] = {}  # owner_id -> callback
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_update_id = 0
        self._lock = threading.Lock()

    def _create_connection(self):
        if not self._bot_token:
            raise ValueError("bot_token is required")
        # Test connection by calling getMe
        me = self._api_call("getMe")
        logger.info(f"Telegram bot connected: @{me.get('username', '?')}")
        return me

    def _close_connection(self):
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None

    def ensure_connected(self):
        if not self._initialized:
            self.connect()

    # ── Polling ────────────────────────────────────────────────────

    def register_handler(self, owner_id: str, callback: Callable):
        """Register a callback for incoming updates.

        callback receives (update: dict) and should handle it.
        """
        with self._lock:
            self._callbacks[owner_id] = callback
        self._ensure_polling()

    def unregister_handler(self, owner_id: str):
        with self._lock:
            self._callbacks.pop(owner_id, None)
            if not self._callbacks:
                self._stop_event.set()

    def _ensure_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="telegram-poll",
        )
        self._poll_thread.start()

    def _poll_loop(self):
        """Long-polling loop for Telegram updates."""
        logger.info("Telegram polling started")
        while not self._stop_event.is_set():
            try:
                updates = self._api_call("getUpdates", {
                    "offset": self._last_update_id + 1,
                    "timeout": self._poll_timeout,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                })
                if not updates:
                    continue
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id > self._last_update_id:
                        self._last_update_id = update_id
                    self._dispatch(update)
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"Telegram poll error: {e}")
                    time.sleep(5)
        logger.info("Telegram polling stopped")

    def _dispatch(self, update: dict):
        """Dispatch update to all registered callbacks."""
        # Check allowed_users filter
        msg = update.get("message") or update.get("callback_query", {}).get("message")
        if msg and self._allowed_users:
            user_id = str(msg.get("from", {}).get("id", ""))
            if user_id not in self._allowed_users:
                logger.debug(f"Telegram: ignoring message from {user_id} (not in allowed list)")
                return

        with self._lock:
            callbacks = list(self._callbacks.values())
        for cb in callbacks:
            try:
                cb(update)
            except Exception as e:
                logger.error(f"Telegram callback error: {e}")

    # ── Sending ────────────────────────────────────────────────────

    def send_message(self, chat_id: str, text: str,
                     parse_mode: str = "Markdown",
                     reply_to: int = 0) -> dict:
        """Send a text message."""
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_to:
            params["reply_to_message_id"] = reply_to
        # Split long messages (Telegram 4096 char limit)
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            result = None
            for chunk in chunks:
                params["text"] = chunk
                result = self._api_call("sendMessage", params)
            return result or {}
        return self._api_call("sendMessage", params)

    def send_document(self, chat_id: str, file_bytes: bytes,
                      filename: str, caption: str = "") -> dict:
        """Send a document/file."""
        # For simplicity, use base64 + sendDocument with multipart
        # Telegram requires multipart/form-data for file uploads
        import base64
        boundary = "----TelegramBotBoundary"
        body = b""
        # chat_id field
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
        # caption
        if caption:
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
        # document field
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += file_bytes
        body += f"\r\n--{boundary}--\r\n".encode()

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(_API_HOST, timeout=30, context=ctx)
        try:
            conn.request(
                "POST",
                f"/bot{self._bot_token}/sendDocument",
                body=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            resp = conn.getresponse()
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
            return data.get("result", {})
        finally:
            conn.close()

    def get_file_bytes(self, file_id: str) -> tuple:
        """Download a file from Telegram servers.

        Returns (bytes, file_path) or raises on error.
        """
        file_info = self._api_call("getFile", {"file_id": file_id})
        file_path = file_info.get("file_path", "")
        if not file_path:
            raise RuntimeError("No file_path in getFile response")

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(_API_HOST, timeout=60, context=ctx)
        try:
            conn.request("GET", f"/file/bot{self._bot_token}/{file_path}")
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"File download failed: HTTP {resp.status}")
            return resp.read(), file_path
        finally:
            conn.close()

    def send_typing(self, chat_id: str):
        """Send typing indicator."""
        try:
            self._api_call("sendChatAction", {
                "chat_id": chat_id, "action": "typing",
            })
        except Exception:
            pass  # Non-critical

    # ── API ────────────────────────────────────────────────────────

    def _api_call(self, method: str, params: Optional[Dict] = None) -> Any:
        """Call Telegram Bot API."""
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(
            _API_HOST, timeout=self._poll_timeout + 10, context=ctx,
        )
        try:
            if params:
                body = json.dumps(params).encode("utf-8")
                headers = {"Content-Type": "application/json"}
            else:
                body = None
                headers = {}

            conn.request(
                "POST" if params else "GET",
                f"/bot{self._bot_token}/{method}",
                body=body,
                headers=headers,
            )
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")

            if resp.status != 200:
                raise RuntimeError(f"Telegram API {method} returned {resp.status}: {raw[:200]}")

            data = json.loads(raw)
            if not data.get("ok"):
                raise RuntimeError(
                    f"Telegram API error: {data.get('description', 'unknown')}"
                )
            return data.get("result")
        finally:
            conn.close()


ServiceFactory.register(TelegramBotService)


# ── TelegramBotPool — multi-bot single-poller ─────────────────────


class _BotState:
    """State for a single bot token in the pool."""
    __slots__ = ("token", "owner_user_id", "offset", "bot_username")

    def __init__(self, token: str, owner_user_id: str):
        self.token = token
        self.owner_user_id = owner_user_id
        self.offset = 0
        self.bot_username = ""


class TelegramBotPool:
    """Singleton pool that polls multiple Telegram bots in a single thread.

    User-owned bots are registered via IdentityService link.
    The pool polls each bot with timeout=0 (non-blocking), then sleeps once.
    """

    _instance: Optional["TelegramBotPool"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._bots: Dict[str, _BotState] = {}  # token -> BotState
        self._callbacks: List[Callable] = []  # shared callbacks (same as receiver)
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._store_lock = threading.Lock()
        self._poll_interval = 2  # seconds between full cycles

    @classmethod
    def instance(cls) -> "TelegramBotPool":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            if cls._instance:
                cls._instance.stop()
            cls._instance = None

    def register_bot(self, token: str, owner_user_id: str) -> str:
        """Register a bot token in the pool.

        Returns the bot username on success.
        Raises RuntimeError if token is invalid.
        """
        with self._store_lock:
            if token in self._bots:
                return self._bots[token].bot_username

        # Validate token by calling getMe (outside lock to avoid blocking)
        me = _api_call_static(token, "getMe")
        username = me.get("username", "")

        with self._store_lock:
            state = _BotState(token, owner_user_id)
            state.bot_username = username
            self._bots[token] = state

        logger.info(f"TelegramBotPool: registered @{username} for {owner_user_id}")
        self._ensure_polling()
        return username

    def unregister_bot(self, token: str):
        """Remove a bot from the pool."""
        with self._store_lock:
            state = self._bots.pop(token, None)
            if state:
                logger.info(f"TelegramBotPool: unregistered @{state.bot_username}")
            if not self._bots:
                self._stop_event.set()

    def register_callback(self, callback: Callable):
        """Register a callback for incoming updates from all bots."""
        with self._store_lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        with self._store_lock:
            self._callbacks = [c for c in self._callbacks if c is not callback]

    def send_message(self, token: str, chat_id: str, text: str,
                     parse_mode: str = "Markdown") -> dict:
        """Send a message via a specific bot token."""
        params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            result = None
            for chunk in chunks:
                params["text"] = chunk
                result = _api_call_static(token, "sendMessage", params)
            return result or {}
        return _api_call_static(token, "sendMessage", params)

    def get_file_bytes(self, token: str, file_id: str) -> tuple:
        """Download a file via a specific bot token."""
        file_info = _api_call_static(token, "getFile", {"file_id": file_id})
        file_path = file_info.get("file_path", "")
        if not file_path:
            raise RuntimeError("No file_path in getFile response")
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(_API_HOST, timeout=60, context=ctx)
        try:
            conn.request("GET", f"/file/bot{token}/{file_path}")
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"File download failed: HTTP {resp.status}")
            return resp.read(), file_path
        finally:
            conn.close()

    def get_bot_token_for_user(self, user_id: str) -> Optional[str]:
        """Find the bot token owned by a specific user."""
        with self._store_lock:
            for token, state in self._bots.items():
                if state.owner_user_id == user_id:
                    return token
        return None

    def stop(self):
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None

    def _ensure_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True,
            name="telegram-pool-poll",
        )
        self._poll_thread.start()

    def _poll_loop(self):
        """Single-thread poll loop iterating over all registered bots."""
        logger.info("TelegramBotPool: polling started")
        while not self._stop_event.is_set():
            with self._store_lock:
                bots = list(self._bots.values())

            for state in bots:
                if self._stop_event.is_set():
                    break
                try:
                    updates = _api_call_static(state.token, "getUpdates", {
                        "offset": state.offset + 1,
                        "timeout": 0,  # non-blocking
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    })
                    if not updates:
                        continue
                    for update in updates:
                        uid = update.get("update_id", 0)
                        if uid > state.offset:
                            state.offset = uid
                        # Inject bot owner info into the update
                        update["_bot_owner"] = state.owner_user_id
                        update["_bot_token"] = state.token
                        self._dispatch(update)
                except Exception as e:
                    logger.debug(f"TelegramBotPool: poll error for "
                                 f"@{state.bot_username}: {e}")

            # Sleep once per full cycle
            self._stop_event.wait(self._poll_interval)

        logger.info("TelegramBotPool: polling stopped")

    def _dispatch(self, update: dict):
        """Dispatch update to all registered callbacks."""
        with self._store_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(update)
            except Exception as e:
                logger.error(f"TelegramBotPool callback error: {e}")


def _api_call_static(token: str, method: str,
                     params: Optional[Dict] = None) -> Any:
    """Standalone Telegram API call (no service instance needed)."""
    ctx = ssl.create_default_context()
    timeout = 10 if params and params.get("timeout", 0) == 0 else 40
    conn = http.client.HTTPSConnection(_API_HOST, timeout=timeout, context=ctx)
    try:
        if params:
            body = json.dumps(params).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            body = None
            headers = {}
        conn.request(
            "POST" if params else "GET",
            f"/bot{token}/{method}",
            body=body, headers=headers,
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(
                f"Telegram API {method} returned {resp.status}: {raw[:200]}"
            )
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {data.get('description', 'unknown')}"
            )
        return data.get("result")
    finally:
        conn.close()
