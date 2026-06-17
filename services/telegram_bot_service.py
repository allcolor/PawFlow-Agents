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
import re
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

_API_HOST = "api.telegram.org"
_TELEGRAM_TEXT_LIMIT = 4096
_TELEGRAM_SPLIT_LIMIT = 4000
_DEFAULT_ALLOWED_UPDATES = ["message", "callback_query"]


def _normalize_allowed_updates(value, default):
    """Coerce an allowed_updates config value (csv string or list) to a list.

    Empty/invalid values fall back to ``default``. Order is preserved and
    duplicates are dropped so callers can union sets safely.
    """
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value]
    else:
        return list(default)
    out = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out or list(default)


class TelegramBotService(BaseService):
    """Shared Telegram bot service with long-polling."""

    TYPE = "telegramBot"
    DESCRIPTION = "Telegram Bot API connection (long-polling)"
    TAGS = ["telegram", "bot", "messaging"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "bot_token": {
                "type": "string",
                "required": True,
                "sensitive": True,
                "description": "Telegram Bot API token from BotFather",
            },
            "poll_timeout": {
                "type": "integer",
                "required": False,
                "default": 30,
                "description": "Long-poll timeout in seconds",
            },
            "allowed_updates": {
                "type": "string",
                "required": False,
                "description": (
                    "Comma-separated Telegram update types to receive "
                    "(e.g. message,callback_query,my_chat_member,chat_member). "
                    "Empty = message,callback_query."
                ),
            },
        }

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
        self._allowed_updates = _normalize_allowed_updates(
            self.config.get("allowed_updates"), _DEFAULT_ALLOWED_UPDATES)
        self._callbacks: Dict[str, Callable] = {}  # owner_id -> callback
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_update_id = 0
        self._lock = threading.Lock()

    def add_allowed_updates(self, updates) -> None:
        """Union extra update types into the receive filter (e.g. from a task).

        Lets a flow declare the update types it needs on the receiver task
        without separately reconfiguring the shared service.
        """
        with self._lock:
            for u in _normalize_allowed_updates(updates, []):
                if u not in self._allowed_updates:
                    self._allowed_updates.append(u)

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
                with self._lock:
                    allowed = list(self._allowed_updates)
                updates = self._api_call("getUpdates", {
                    "offset": self._last_update_id + 1,
                    "timeout": self._poll_timeout,
                    "allowed_updates": json.dumps(allowed),
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
        callback = update.get("callback_query") or {}
        msg = update.get("message") or callback.get("message")
        if msg and self._allowed_users:
            user = callback.get("from") or msg.get("from", {})
            user_id = str(user.get("id", ""))
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
                     reply_to: int = 0,
                     reply_markup: Optional[Dict[str, Any]] = None) -> dict:
        """Send a text message."""
        chunks = _split_telegram_text(text, parse_mode)
        result = None
        for idx, chunk in enumerate(chunks):
            params: Dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                params["parse_mode"] = parse_mode
            if reply_to and idx == 0:
                params["reply_to_message_id"] = reply_to
            if reply_markup and idx == len(chunks) - 1:
                params["reply_markup"] = json.dumps(reply_markup)
            result = _send_api_call(self._bot_token, "sendMessage", params)
        return result or {}

    def send_document(self, chat_id: str, file_bytes: bytes,
                      filename: str, caption: str = "") -> dict:
        """Send a document/file."""
        return _api_upload(
            self._bot_token, "sendDocument", chat_id, "document",
            file_bytes, filename, "application/octet-stream",
            caption=caption)

    def send_photo(self, chat_id: str, file_bytes: bytes,
                   filename: str = "image.png", caption: str = "",
                   content_type: str = "image/png") -> dict:
        """Send an image attachment."""
        return _api_upload(
            self._bot_token, "sendPhoto", chat_id, "photo",
            file_bytes, filename, content_type or "image/png", caption=caption)

    def send_video(self, chat_id: str, file_bytes: bytes,
                   filename: str = "video.mp4", caption: str = "",
                   content_type: str = "video/mp4") -> dict:
        """Send a video attachment."""
        return _api_upload(
            self._bot_token, "sendVideo", chat_id, "video",
            file_bytes, filename, content_type or "video/mp4", caption=caption)

    def send_audio(self, chat_id: str, file_bytes: bytes,
                   filename: str = "speech.mp3", caption: str = "",
                   content_type: str = "audio/mpeg") -> dict:
        """Send synthesized speech as Telegram voice/audio.

        Telegram voice notes require OGG/OPUS. Other audio formats are sent as
        regular audio attachments so TTS providers can work without transcoding.
        """
        ct = (content_type or "audio/mpeg").split(";")[0].strip().lower()
        as_voice = ct in {"audio/ogg", "audio/opus"} or filename.lower().endswith((".ogg", ".opus"))
        return _api_upload(
            self._bot_token, "sendVoice" if as_voice else "sendAudio", chat_id,
            "voice" if as_voice else "audio", file_bytes, filename,
            content_type or "audio/mpeg", caption=caption)

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
            _send_api_call(self._bot_token, "sendChatAction", {
                "chat_id": chat_id, "action": "typing",
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    # ── API ────────────────────────────────────────────────────────

    def call_api(self, method: str, params: Optional[Dict] = None) -> Any:
        """Public passthrough to any Telegram Bot API method.

        Generic by design: the core exposes the raw API (no ban/mute/kick
        verbs baked in) so flows can call e.g. banChatMember, restrictChatMember,
        deleteMessage, getChatMember, leaveChat with their own params.
        """
        self.ensure_connected()
        return self._api_call(method, params)

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
        self._allowed_updates = list(_DEFAULT_ALLOWED_UPDATES)

    def add_allowed_updates(self, updates) -> None:
        """Union extra update types into the pool's receive filter.

        The pool polls many bots in one loop, so the filter is the union of
        what every registered receiver asks for.
        """
        with self._store_lock:
            for u in _normalize_allowed_updates(updates, []):
                if u not in self._allowed_updates:
                    self._allowed_updates.append(u)

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
            if callback not in self._callbacks:
                self._callbacks.append(callback)
            has_bots = bool(self._bots)
        if has_bots:
            self._ensure_polling()

    def unregister_callback(self, callback: Callable):
        with self._store_lock:
            self._callbacks = [c for c in self._callbacks if c is not callback]
            if not self._callbacks:
                self._stop_event.set()

    def send_message(self, token: str, chat_id: str, text: str,
                     parse_mode: str = "Markdown",
                     reply_markup: Optional[Dict[str, Any]] = None) -> dict:
        """Send a message via a specific bot token."""
        chunks = _split_telegram_text(text, parse_mode)
        result = None
        for idx, chunk in enumerate(chunks):
            params: Dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                params["parse_mode"] = parse_mode
            if reply_markup and idx == len(chunks) - 1:
                params["reply_markup"] = json.dumps(reply_markup)
            result = _send_api_call(token, "sendMessage", params)
        return result or {}

    def send_document(self, token: str, chat_id: str, file_bytes: bytes,
                      filename: str, caption: str = "") -> dict:
        """Send a document/file via a specific bot token."""
        return _api_upload(
            token, "sendDocument", chat_id, "document", file_bytes, filename,
            "application/octet-stream", caption=caption)

    def send_audio(self, token: str, chat_id: str, file_bytes: bytes,
                   filename: str = "speech.mp3", caption: str = "",
                   content_type: str = "audio/mpeg") -> dict:
        """Send synthesized speech via a specific bot token."""
        ct = (content_type or "audio/mpeg").split(";")[0].strip().lower()
        as_voice = ct in {"audio/ogg", "audio/opus"} or filename.lower().endswith((".ogg", ".opus"))
        return _api_upload(
            token, "sendVoice" if as_voice else "sendAudio", chat_id,
            "voice" if as_voice else "audio", file_bytes, filename,
            content_type or "audio/mpeg", caption=caption)

    def send_photo(self, token: str, chat_id: str, file_bytes: bytes,
                   filename: str = "image.png", caption: str = "",
                   content_type: str = "image/png") -> dict:
        """Send an image via a specific bot token."""
        return _api_upload(
            token, "sendPhoto", chat_id, "photo", file_bytes, filename,
            content_type or "image/png", caption=caption)

    def send_video(self, token: str, chat_id: str, file_bytes: bytes,
                   filename: str = "video.mp4", caption: str = "",
                   content_type: str = "video/mp4") -> dict:
        """Send a video via a specific bot token."""
        return _api_upload(
            token, "sendVideo", chat_id, "video", file_bytes, filename,
            content_type or "video/mp4", caption=caption)

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

    def call_api(self, token: str, method: str,
                 params: Optional[Dict] = None) -> Any:
        """Public passthrough to any Telegram Bot API method for one bot token."""
        return _api_call_static(token, method, params)

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
                with self._store_lock:
                    if not self._callbacks:
                        self._stop_event.set()
                        break
                try:
                    with self._store_lock:
                        allowed = list(self._allowed_updates)
                    updates = _api_call_static(state.token, "getUpdates", {
                        "offset": state.offset + 1,
                        "timeout": 0,  # non-blocking
                        "allowed_updates": json.dumps(allowed),
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


# ── Persistent send transport ─────────────────────────────────────
#
# Message sends run on the ConversationEventBus per-conversation listener lane
# (one in-flight drain per conversation). Opening a fresh TLS connection per
# message added a full handshake (~200-400ms) to every send; under a burst the
# lane could not drain fast enough and Telegram fell minutes behind the webchat
# (which is delivered directly via SSE). A persistent keep-alive connection per
# bot token removes the per-message handshake. It is kept SEPARATE from the
# long-poll getUpdates connection so a 30s long-poll never blocks a send. Each
# token serializes its own sends under a lock (Telegram rate-limits per bot
# anyway) and 429 responses are honoured with retry_after backoff.

_SEND_MAX_RETRIES = 3
_SEND_MAX_BACKOFF = 30.0


class _SendChannel:
    __slots__ = ("lock", "conn")

    def __init__(self):
        self.lock = threading.Lock()
        self.conn = None


_SEND_CHANNELS: Dict[str, _SendChannel] = {}
_SEND_CHANNELS_LOCK = threading.Lock()


def _send_channel(token: str) -> _SendChannel:
    with _SEND_CHANNELS_LOCK:
        ch = _SEND_CHANNELS.get(token)
        if ch is None:
            ch = _SendChannel()
            _SEND_CHANNELS[token] = ch
        return ch


def _close_send_conn(ch: _SendChannel) -> None:
    try:
        if ch.conn is not None:
            ch.conn.close()
    except Exception:
        pass
    ch.conn = None


def _parse_retry_after(raw: str) -> float:
    try:
        data = json.loads(raw)
        ra = (data.get("parameters") or {}).get("retry_after")
        if ra is not None:
            return float(ra)
    except Exception:
        pass
    return 1.0


def _send_api_call(token: str, method: str,
                   params: Optional[Dict] = None, timeout: int = 40) -> Any:
    """Telegram API call over a persistent per-token keep-alive connection.

    Reconnects on a stale/broken connection and honours 429 retry_after. Used
    for message sends (NOT long-poll getUpdates, which keeps its own short-lived
    connection so it never blocks a send).
    """
    ch = _send_channel(token)
    body = json.dumps(params).encode("utf-8") if params else None
    headers = {"Content-Type": "application/json"} if params else {}
    path = f"/bot{token}/{method}"
    verb = "POST" if params else "GET"
    with ch.lock:
        attempt = 0
        while True:
            attempt += 1
            try:
                if ch.conn is None:
                    ch.conn = http.client.HTTPSConnection(
                        _API_HOST, timeout=timeout,
                        context=ssl.create_default_context())
                ch.conn.request(verb, path, body=body, headers=headers)
                resp = ch.conn.getresponse()
                raw = resp.read().decode("utf-8")
                status = resp.status
            except (http.client.HTTPException, OSError) as e:
                # Broken/stale keep-alive socket: drop it and reconnect.
                _close_send_conn(ch)
                if attempt <= _SEND_MAX_RETRIES:
                    continue
                raise RuntimeError(
                    f"Telegram API {method} connection failed: {e}")
            if status == 429:
                retry_after = _parse_retry_after(raw)
                if attempt <= _SEND_MAX_RETRIES:
                    time.sleep(min(retry_after, _SEND_MAX_BACKOFF))
                    continue
                raise RuntimeError(
                    f"Telegram API {method} rate-limited (429)")
            if status != 200:
                raise RuntimeError(
                    f"Telegram API {method} returned {status}: {raw[:200]}")
            data = json.loads(raw)
            if not data.get("ok"):
                raise RuntimeError(
                    f"Telegram API error: {data.get('description', 'unknown')}")
            return data.get("result")


def _reset_send_channels() -> None:
    """Close all persistent send connections (test teardown / shutdown)."""
    with _SEND_CHANNELS_LOCK:
        channels = list(_SEND_CHANNELS.values())
        _SEND_CHANNELS.clear()
    for ch in channels:
        with ch.lock:
            _close_send_conn(ch)


def _split_telegram_text(text: str, parse_mode: Optional[str] = None) -> List[str]:
    """Split Bot API text into complete Telegram-sized messages.

    For ``parse_mode=HTML`` the split is tag-aware: it never cuts inside a tag,
    closes any tags still open at a chunk boundary, and reopens them at the
    start of the next chunk. Splitting raw HTML on whitespace alone produces
    chunks like ``...<blockquote>foo`` whose dangling tag makes the Telegram API
    reject the whole message (400 "Can't find end tag"). Non-HTML text keeps
    the plain whitespace-boundary split.
    """
    if not text:
        return [""]
    if len(text) <= _TELEGRAM_TEXT_LIMIT:
        return [text]
    if parse_mode and parse_mode.lower() == "html":
        return _split_telegram_html(text, _TELEGRAM_SPLIT_LIMIT)
    return _split_telegram_plain(text, _TELEGRAM_SPLIT_LIMIT)


def _split_telegram_plain(text: str, limit: int) -> List[str]:
    chunks: List[str] = []
    remaining = text
    while len(remaining) > _TELEGRAM_TEXT_LIMIT:
        split_at = _best_telegram_split(remaining, limit)
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _best_telegram_split(text: str, limit: int) -> int:
    window = text[:limit]
    for marker in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = window.rfind(marker)
        if idx >= max(1, limit // 2):
            return idx + len(marker)
    return limit


# Telegram HTML tags are all paired (no void tags); a tag is <name ...> or
# </name>. We only need name + whether it's a closer to balance them.
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")


def _tokenize_html(text: str) -> List[tuple]:
    """Split into ('text', s) and ('tag', name, is_close, full) tokens."""
    tokens: List[tuple] = []
    pos = 0
    for m in _HTML_TAG_RE.finditer(text):
        if m.start() > pos:
            tokens.append(("text", text[pos:m.start()]))
        tokens.append(("tag", m.group(2).lower(), bool(m.group(1)), m.group(0)))
        pos = m.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))
    return tokens


def _text_cut(run: str, avail: int) -> int:
    """Index to cut a text run at, preferring a whitespace boundary."""
    if avail >= len(run):
        return len(run)
    window = run[:avail]
    for marker in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = window.rfind(marker)
        if idx >= max(1, avail // 2):
            return idx + len(marker)
    return max(1, avail)


def _split_telegram_html(text: str, limit: int) -> List[str]:
    """Tag-aware split: every chunk is independently well-formed HTML."""
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    stack: List[tuple] = []  # (name, opening_str) of tags open in cur

    def flush() -> None:
        nonlocal cur, cur_len
        closers = "".join("</%s>" % name for name, _ in reversed(stack))
        body = "".join(cur) + closers
        if body.strip():
            chunks.append(body)
        openers = "".join(opening for _, opening in stack)
        cur = [openers] if openers else []
        cur_len = len(openers)

    for tok in _tokenize_html(text):
        if tok[0] == "tag":
            _, name, is_close, full = tok
            if cur_len + len(full) > limit and cur_len > 0:
                flush()
            cur.append(full)
            cur_len += len(full)
            if is_close:
                for j in range(len(stack) - 1, -1, -1):
                    if stack[j][0] == name:
                        del stack[j:]
                        break
            else:
                stack.append((name, full))
        else:
            run = tok[1]
            while run:
                avail = limit - cur_len
                if avail <= 0:
                    flush()
                    avail = limit - cur_len
                if len(run) <= avail:
                    cur.append(run)
                    cur_len += len(run)
                    run = ""
                else:
                    cut = _text_cut(run, avail)
                    cur.append(run[:cut])
                    cur_len += cut
                    run = run[cut:]
                    flush()
    if cur:
        closers = "".join("</%s>" % name for name, _ in reversed(stack))
        body = "".join(cur) + closers
        if body.strip():
            chunks.append(body)
    return chunks or [""]


def _api_upload(token: str, method: str, chat_id: str, field_name: str,
                file_bytes: bytes, filename: str, content_type: str,
                caption: str = "") -> dict:
    """Upload a file to Telegram with multipart/form-data."""
    boundary = "----TelegramBotBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="{field_name}"; '
        f'filename="{filename}"\r\n'
    ).encode()
    body += f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode()
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(_API_HOST, timeout=60, context=ctx)
    try:
        conn.request(
            "POST", f"/bot{token}/{method}", body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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
        return data.get("result", {})
    finally:
        conn.close()
