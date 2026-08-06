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
import mimetypes
import queue
import threading
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_MEDIA_GROUP_DEBOUNCE_SECONDS = 0.5


def _rich_inline_text(value: Any) -> str:
    """Flatten inline rich-message fragments without losing their spacing."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_inline_text(item) for item in value)
    if isinstance(value, dict):
        if "text" in value:
            return _rich_inline_text(value["text"])
        if "content" in value:
            return _rich_inline_text(value["content"])
    return ""


def _rich_blocks_text(blocks: Any) -> str:
    """Render Telegram rich-message blocks as readable plain text."""
    if not isinstance(blocks, list):
        return ""
    rendered: List[str] = []
    for block in blocks:
        if isinstance(block, str):
            text = block
        elif not isinstance(block, dict):
            continue
        elif block.get("type") == "list":
            lines = []
            for item in block.get("items") or []:
                if not isinstance(item, dict):
                    continue
                label = _rich_inline_text(item.get("label")).strip()
                body = _rich_blocks_text(item.get("blocks")).strip()
                line = " ".join(part for part in (label, body) if part)
                if line:
                    lines.append(line)
            text = "\n".join(lines)
        else:
            text = _rich_inline_text(block.get("text"))
            if not text:
                text = _rich_blocks_text(block.get("blocks"))
        text = text.strip()
        if text:
            rendered.append(text)
    return "\n\n".join(rendered)


def _rich_message_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return _rich_blocks_text(value.get("blocks"))


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
        "allowed_updates": {
            "type": "string",
            "description": (
                "Comma-separated Telegram update types to subscribe to "
                "(e.g. message,callback_query,my_chat_member,chat_member). "
                "Unioned into the bot service filter. Empty = service default."
            ),
            "required": False,
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._registered = False
        self._owner_id: Optional[str] = None
        self._pool_registered = False
        self._media_group_lock = threading.Lock()
        self._media_groups: Dict[tuple, Dict[str, Any]] = {}

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
        allowed = self.config.get("allowed_updates", "")
        if allowed and hasattr(svc, "add_allowed_updates"):
            svc.add_allowed_updates(allowed)
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
            allowed = self.config.get("allowed_updates", "")
            if allowed:
                pool.add_allowed_updates(allowed)
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
        # Chat-membership updates (bot added/removed/promoted, or a member's
        # status change) carry no `message`; surface them as their own type.
        for member_kind in ("my_chat_member", "chat_member"):
            member = update.get(member_kind)
            if member:
                self._emit_member_update(update, member_kind, member)
                return

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
        rich_text = _rich_message_text(msg.get("rich_message"))
        if callback:
            content = str(callback.get("data") or "").encode("utf-8")
            msg_type = "callback_query"
        elif "text" in msg:
            content = msg["text"].encode("utf-8")
            msg_type = "text"
        elif rich_text:
            content = rich_text.encode("utf-8")
            msg_type = "text"
        elif "document" in msg:
            caption = msg.get("caption", "")
            file_id = msg["document"].get("file_id", "")
            file_name = msg["document"].get("file_name", "unknown")
            mime_type = (msg["document"].get("mime_type", "")
                         or mimetypes.guess_type(file_name)[0]
                         or "application/octet-stream")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content = json.dumps({
                "type": "document",
                "file_id": file_id,
                "file_name": file_name,
                "mime_type": mime_type,
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
            # Album downloads happen only after the debounce window closes.
            # Keeping this callback fast lets every update in the group reach
            # the buffer before its timer can fire.
            file_data = "" if msg.get("media_group_id") else self._try_download(
                file_id, bot_token=bot_token)
            content_text = caption or "(photo)"
            content = content_text.encode("utf-8")
            msg_type = "photo"
        elif "voice" in msg:
            file_id = msg["voice"].get("file_id", "")
            file_data = self._try_download(file_id, bot_token=bot_token)
            content = json.dumps({
                "type": "voice",
                "file_id": file_id,
                "file_name": "telegram_voice.ogg",
                "duration": msg["voice"].get("duration", 0),
                "mime_type": msg["voice"].get("mime_type", "audio/ogg"),
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
        ff.set_attribute("telegram.update_type",
                         "callback_query" if callback else "message")
        if callback:
            ff.set_attribute("telegram.callback_query_id", str(callback.get("id", "")))
            ff.set_attribute("telegram.callback_data", str(callback.get("data", "")))

        # For photos, store base64 data for LLM vision
        if msg_type == "photo" and file_data:
            ff.set_attribute("telegram.image_base64", file_data)
            ff.set_attribute("telegram.image_file_id", file_id)

        self._enrich_message_attributes(ff, update, msg)

        media_group_id = str(msg.get("media_group_id") or "")
        if msg_type == "photo" and media_group_id:
            ff.set_attribute("telegram.media_group_id", media_group_id)
            self._buffer_photo_group(
                ff, media_group_id, file_id, file_data, str(caption or ""))
            return

        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning("telegramReceiver queue full, dropping message")

    def _buffer_photo_group(self, ff: FlowFile, media_group_id: str,
                            file_id: str, data_base64: str,
                            caption: str) -> None:
        """Debounce Telegram album updates into one multi-photo FlowFile."""
        source_id = ff.get_attribute("telegram.bot_token") or str(
            self.config.get("service_id") or "")
        key = (source_id, ff.get_attribute("telegram.chat_id") or "",
               media_group_id)
        with self._media_group_lock:
            group = self._media_groups.get(key)
            if group is None:
                group = {
                    "flowfile": ff,
                    "photos": [],
                    "caption": "",
                    "bot_token": ff.get_attribute("telegram.bot_token") or "",
                    "generation": 0,
                    "timer": None,
                }
                self._media_groups[key] = group
            group["photos"].append({
                "file_id": file_id,
                "data_base64": data_base64,
                "mime_type": "image/jpeg",
            })
            if caption and not group["caption"]:
                group["caption"] = caption
            previous = group.get("timer")
            if previous is not None:
                previous.cancel()
            group["generation"] += 1
            generation = group["generation"]
            timer = threading.Timer(
                _MEDIA_GROUP_DEBOUNCE_SECONDS,
                self._flush_media_group,
                args=(key, generation),
            )
            timer.daemon = True
            group["timer"] = timer
            timer.start()

    def _flush_media_group(self, key: tuple,
                           generation: Optional[int] = None) -> None:
        with self._media_group_lock:
            group = self._media_groups.get(key)
            if group is None:
                return
            if generation is not None and group["generation"] != generation:
                return
            self._media_groups.pop(key, None)
            timer = group.get("timer")
            if timer is not None:
                timer.cancel()

        ff = group["flowfile"]
        photos = group["photos"]
        caption = group["caption"]
        for index, photo in enumerate(photos, 1):
            photo["filename"] = f"telegram_photo_{index}.jpg"
            if not photo["data_base64"]:
                photo["data_base64"] = self._try_download(
                    photo["file_id"], bot_token=group["bot_token"])
        ff.set_content(json.dumps({
            "type": "photo_album",
            "caption": caption,
            "photos": photos,
        }, ensure_ascii=False).encode("utf-8"))
        ff.set_attribute("telegram.message_type", "photo_album")
        ff.delete_attribute("telegram.image_base64")
        ff.delete_attribute("telegram.image_file_id")
        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning("telegramReceiver queue full, dropping photo album")

    def _enrich_message_attributes(self, ff: FlowFile, update: dict,
                                   msg: dict) -> None:
        """Surface group/reply/membership context needed by moderation flows."""
        chat = msg.get("chat", {})
        ff.set_attribute("telegram.chat_type", str(chat.get("type", "")))
        ff.set_attribute("telegram.chat_title", str(chat.get("title", "")))

        reply = msg.get("reply_to_message")
        if reply:
            ff.set_attribute("telegram.reply_to_message_id",
                             str(reply.get("message_id", "")))
            ff.set_attribute("telegram.reply_to_user_id",
                             str(reply.get("from", {}).get("id", "")))
            ff.set_attribute("telegram.reply_to_username",
                             str(reply.get("from", {}).get("username", "")))
            ff.set_attribute("telegram.reply_to_text",
                             str(reply.get("text", reply.get("caption", ""))))

        new_members = msg.get("new_chat_members")
        if new_members:
            ff.set_attribute("telegram.new_chat_members",
                             json.dumps(new_members, ensure_ascii=False))
            ff.set_attribute(
                "telegram.new_chat_member_ids",
                ",".join(str(m.get("id", "")) for m in new_members))
        left = msg.get("left_chat_member")
        if left:
            ff.set_attribute("telegram.left_chat_member",
                             json.dumps(left, ensure_ascii=False))
            ff.set_attribute("telegram.left_chat_member_id",
                             str(left.get("id", "")))

        if "migrate_to_chat_id" in msg:
            ff.set_attribute("telegram.migrate_to_chat_id",
                             str(msg.get("migrate_to_chat_id", "")))
        if "migrate_from_chat_id" in msg:
            ff.set_attribute("telegram.migrate_from_chat_id",
                             str(msg.get("migrate_from_chat_id", "")))

        entities = msg.get("entities") or msg.get("caption_entities")
        if entities:
            ff.set_attribute("telegram.entities",
                             json.dumps(entities, ensure_ascii=False))

        # Never persist the live bot token/owner inside telegram.raw: the
        # full dump lands in logs/attribute dumps (log_task dumps all
        # attributes), leaking the token with read/write/kick capability
        # for every chat the bot is in. Keep the functional attribute.
        ff.set_attribute("telegram.raw", json.dumps(
            {k: v for k, v in update.items()
             if k not in ("_bot_token", "_bot_owner")},
            ensure_ascii=False))
        bot_token = str(update.get("_bot_token") or "")
        if bot_token:
            ff.set_attribute("telegram.bot_token", bot_token)

    def _emit_member_update(self, update: dict, kind: str, member: dict) -> None:
        """Convert a my_chat_member/chat_member update into a FlowFile."""
        chat = member.get("chat", {})
        actor = member.get("from", {})
        old = member.get("old_chat_member", {})
        new = member.get("new_chat_member", {})
        target = new.get("user", {})

        ff = FlowFile(content=json.dumps(member, ensure_ascii=False).encode("utf-8"))
        ff.set_attribute("telegram.chat_id", str(chat.get("id", "")))
        ff.set_attribute("telegram.chat_type", str(chat.get("type", "")))
        ff.set_attribute("telegram.chat_title", str(chat.get("title", "")))
        ff.set_attribute("telegram.message_type", kind)
        ff.set_attribute("telegram.update_type", kind)
        # `from` is who performed the action; the affected member is the target.
        ff.set_attribute("telegram.user_id", str(actor.get("id", "")))
        ff.set_attribute("telegram.username", str(actor.get("username", "")))
        ff.set_attribute("telegram.first_name", str(actor.get("first_name", "")))
        ff.set_attribute("telegram.target_user_id", str(target.get("id", "")))
        ff.set_attribute("telegram.target_username", str(target.get("username", "")))
        ff.set_attribute("telegram.target_is_bot", str(target.get("is_bot", False)))
        ff.set_attribute("telegram.old_status", str(old.get("status", "")))
        ff.set_attribute("telegram.new_status", str(new.get("status", "")))
        # Never persist the live bot token/owner inside telegram.raw: the
        # full dump lands in logs/attribute dumps (log_task dumps all
        # attributes), leaking the token with read/write/kick capability
        # for every chat the bot is in. Keep the functional attribute.
        ff.set_attribute("telegram.raw", json.dumps(
            {k: v for k, v in update.items()
             if k not in ("_bot_token", "_bot_owner")},
            ensure_ascii=False))
        bot_token = str(update.get("_bot_token") or "")
        if bot_token:
            ff.set_attribute("telegram.bot_token", bot_token)

        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning("telegramReceiver queue full, dropping member update")

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
        with self._media_group_lock:
            groups = list(self._media_groups.values())
            self._media_groups.clear()
        for group in groups:
            timer = group.get("timer")
            if timer is not None:
                timer.cancel()
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
