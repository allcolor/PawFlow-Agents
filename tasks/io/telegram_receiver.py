"""telegramReceiver — self-triggering source task for Telegram bot messages.

Listens for incoming Telegram messages via TelegramBotService and converts
them into FlowFiles for processing by downstream tasks (e.g. agentLoop).

Config:
    service_id: str    — ID of the TelegramBotService in the flow

The task sets these FlowFile attributes:
    telegram.chat_id       — chat ID for reply
    telegram.user_id       — sender's Telegram user ID
    telegram.username      — sender's username (may be empty)
    telegram.first_name    — sender's first name
    telegram.message_id    — original message ID (for reply_to)
    telegram.message_type  — "text", "document", "photo", "voice", "audio", etc.
"""

import json
import logging
import queue
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class TelegramReceiverTask(BaseTask):
    """Self-triggering source task that receives Telegram messages."""

    TYPE = "telegramReceiver"
    VERSION = "1.0.0"
    NAME = "Telegram Receiver"
    DESCRIPTION = "Receive messages from a Telegram bot"
    ICON = "telegram"
    TAGS = ["telegram", "io", "source"]

    PARAMETERS = {
        "service_id": {
            "type": "string",
            "description": "ID of the TelegramBotService",
            "required": True,
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._registered = False
        self._owner_id: Optional[str] = None
        self._pool_registered = False

    def initialize(self):
        self._ensure_registered()

    def has_pending_input(self) -> bool:
        return not self._queue.empty()

    @property
    def is_persistent_source(self) -> bool:
        return True

    def _ensure_registered(self):
        if self._registered:
            return

        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"TelegramBotService '{service_id}' not found")

        svc.ensure_connected()
        self._owner_id = f"telegramReceiver_{id(self)}"
        svc.register_handler(self._owner_id, self._on_update)
        self._registered = True
        logger.info(f"telegramReceiver registered on service '{service_id}'")

        # Also register with the bot pool for user-owned bots
        self._register_pool_bots()

    def _register_pool_bots(self):
        """Register any user-owned bot tokens with the TelegramBotPool."""
        try:
            from core.identity_service import IdentityService
            from services.telegram_bot_service import TelegramBotPool
            ids = IdentityService.instance()
            all_links = ids.list_all()
            pool = TelegramBotPool.instance()
            if not self._pool_registered:
                pool.register_callback(self._on_update)
                self._pool_registered = True
            for user_id, links in all_links.items():
                bot_token = ids.get_bot_token(user_id, "telegram")
                if bot_token:
                    try:
                        pool.register_bot(bot_token, user_id)
                    except Exception as e:
                        logger.warning(
                            f"Failed to register bot for {user_id}: {e}"
                        )
        except Exception as e:
            logger.debug(f"Pool bot registration skipped: {e}")

    def _on_update(self, update: dict):
        """Called by TelegramBotService when a message arrives."""
        callback = update.get("callback_query") or {}
        msg = update.get("message") or callback.get("message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id", ""))
        user = callback.get("from") or msg.get("from", {})
        user_id = str(user.get("id", ""))
        username = user.get("username", "")
        first_name = user.get("first_name", "")
        bot_token = str(update.get("_bot_token") or "")

        # Determine content and type; download media files
        if callback:
            content = str(callback.get("data") or "").encode("utf-8")
            msg_type = "callback_query"
        elif "text" in msg:
            content = msg["text"].encode("utf-8")
            msg_type = "text"
        elif "document" in msg:
            caption = msg.get("caption", "")
            file_id = msg["document"].get("file_id", "")
            file_name = msg["document"].get("file_name", "unknown")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content = json.dumps({
                "type": "document",
                "file_id": file_id,
                "file_name": file_name,
                "caption": caption,
                "data_base64": file_data,
            }).encode("utf-8")
            msg_type = "document"
        elif "photo" in msg:
            # Use largest photo
            photos = msg["photo"]
            largest = photos[-1] if photos else {}
            caption = msg.get("caption", "")
            file_id = largest.get("file_id", "")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content_text = caption or "(photo)"
            content = content_text.encode("utf-8")
            msg_type = "photo"
        elif "voice" in msg:
            file_id = msg["voice"].get("file_id", "")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content = json.dumps({
                "type": "voice",
                "file_id": file_id,
                "duration": msg["voice"].get("duration", 0),
                "data_base64": file_data,
            }).encode("utf-8")
            msg_type = "voice"
        elif "audio" in msg:
            file_id = msg["audio"].get("file_id", "")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content = json.dumps({
                "type": "audio",
                "file_id": file_id,
                "file_name": msg["audio"].get("file_name", "telegram_audio.ogg"),
                "duration": msg["audio"].get("duration", 0),
                "mime_type": msg["audio"].get("mime_type", "audio/ogg"),
                "data_base64": file_data,
            }).encode("utf-8")
            msg_type = "audio"
        else:
            content = json.dumps(msg).encode("utf-8")
            msg_type = "other"

        ff = FlowFile(content=content)
        ff.set_attribute("telegram.chat_id", chat_id)
        ff.set_attribute("telegram.user_id", user_id)
        ff.set_attribute("telegram.username", username)
        ff.set_attribute("telegram.first_name", first_name)
        ff.set_attribute("telegram.message_id", str(msg.get("message_id", "")))
        ff.set_attribute("telegram.message_type", msg_type)
        if callback:
            ff.set_attribute("telegram.callback_query_id", str(callback.get("id", "")))
            ff.set_attribute("telegram.callback_data", str(callback.get("data", "")))

        # For photos, store base64 data for LLM vision
        if msg_type == "photo" and file_data:
            ff.set_attribute("telegram.image_base64", file_data)
            ff.set_attribute("telegram.image_file_id", file_id)

        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning("telegramReceiver queue full, dropping message")

    def _try_download(self, file_id: str, bot_token: Optional[str] = None) -> str:
        """Try to download a file from Telegram and return base64 data."""
        if not file_id:
            return ""
        try:
            import base64
            if bot_token:
                from services.telegram_bot_service import TelegramBotPool
                data, _ = TelegramBotPool.instance().get_file_bytes(bot_token, file_id)
                return base64.b64encode(data).decode("ascii")
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if not svc:
                return ""
            data, _ = svc.get_file_bytes(file_id)
            return base64.b64encode(data).decode("ascii")
        except Exception as e:
            logger.warning(f"Failed to download Telegram file {file_id}: {e}")
            return ""

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        self._ensure_registered()
        try:
            ff = self._queue.get_nowait()
            return [ff]
        except queue.Empty:
            return []

    def cleanup(self):
        if self._registered and self._owner_id:
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.unregister_handler(self._owner_id)
            self._registered = False
        if self._pool_registered:
            try:
                from services.telegram_bot_service import TelegramBotPool
                TelegramBotPool.instance().unregister_callback(self._on_update)
            except Exception:
                logger.debug("telegramReceiver pool unregister failed", exc_info=True)
            self._pool_registered = False


TaskFactory.register(TelegramReceiverTask)
