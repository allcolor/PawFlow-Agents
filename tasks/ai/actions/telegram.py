"""AgentLoopTask actions — telegram"""

import json
import logging
import time
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_telegram(self, action, body, store, user_id, flowfile):
    """Handle telegram actions. Returns [flowfile] or None."""

    if action == "link_telegram":
        tg_user_id = body.get("telegram_user_id", "").strip()
        bot_token = body.get("bot_token", "").strip()
        if not user_id:
            flowfile.set_content(json.dumps({
                "error": "Authentication required",
            }).encode())
            flowfile.set_attribute("http.response.status", "401")
            return [flowfile]
        if not tg_user_id:
            flowfile.set_content(json.dumps({
                "error": "Missing telegram_user_id",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.identity_service import IdentityService
        linked = IdentityService.instance().link(
            user_id, "telegram", tg_user_id, bot_token=bot_token,
        )
        if not linked:
            flowfile.set_content(json.dumps({
                "error": "This Telegram ID is already linked to another user",
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        result = {"linked": True, "telegram_user_id": tg_user_id}
        # Register personal bot in the pool
        if bot_token:
            try:
                from services.telegram_bot_service import TelegramBotPool
                username = TelegramBotPool.instance().register_bot(
                    bot_token, user_id,
                )
                result["bot_username"] = username
            except Exception as e:
                result["bot_warning"] = f"Bot token invalid: {e}"
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    if action == "unlink_telegram":
        if not user_id:
            flowfile.set_content(json.dumps({
                "error": "Authentication required",
            }).encode())
            flowfile.set_attribute("http.response.status", "401")
            return [flowfile]
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        # Unregister personal bot from pool before unlinking
        bot_token = ids.get_bot_token(user_id, "telegram")
        if bot_token:
            try:
                from services.telegram_bot_service import TelegramBotPool
                TelegramBotPool.instance().unregister_bot(bot_token)
            except Exception:
                pass
        unlinked = ids.unlink(user_id, "telegram")
        flowfile.set_content(json.dumps({
            "unlinked": unlinked,
        }).encode())
        return [flowfile]

    if action == "get_links":
        if not user_id:
            flowfile.set_content(json.dumps({"links": {}}).encode())
            return [flowfile]
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        links = ids.get_links(user_id)
        active_conv = ids.get_active_conv(user_id, "telegram")
        flowfile.set_content(json.dumps({
            "links": links, "active_telegram_conv": active_conv,
        }, ensure_ascii=False).encode())
        return [flowfile]

    return None
