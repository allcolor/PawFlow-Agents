"""AgentLoopTask actions — account linking (generic)"""

import json
import logging
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_account_linking(self, action, body, store, user_id, flowfile):
    """Handle account linking actions. Returns [flowfile] or None."""

    if action == "begin_oauth_account_link":
        if not user_id:
            flowfile.set_content(json.dumps({"error": "Authentication required"}).encode())
            flowfile.set_attribute("http.response.status", "401")
            return [flowfile]
        ttl_seconds = 600
        from core import oauth_invite_tokens
        invite = oauth_invite_tokens.create_token(
            role="viewer",
            link_username=user_id,
            ttl_seconds=ttl_seconds,
            created_by=user_id,
        )
        cookie_name = "pawflow_token"
        session_token = flowfile.get_attribute("http.cookie." + cookie_name) or ""
        if not session_token:
            cookie_header = flowfile.get_attribute("http.header.cookie") or ""
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(cookie_name + "="):
                    session_token = part[len(cookie_name) + 1:]
                    break
        if session_token:
            try:
                from core.security import SecurityManager
                SecurityManager.get_instance()._sessions.pop(session_token, None)
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
        link_cookie = (
            f"pawflow_oauth_link_token={invite['token']}; Path=/; "
            f"Max-Age={ttl_seconds}; HttpOnly; SameSite=Lax"
        )
        clear_session = f"{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        flowfile.set_attribute("http.response.header.Set-Cookie",
                               link_cookie + "\n" + clear_session)
        flowfile.set_content(json.dumps({
            "ok": True,
            "login_url": "/auth/login",
            "ttl_seconds": ttl_seconds,
        }).encode())
        return [flowfile]

    if action == "link_account":
        provider = body.get("provider", "").strip()
        provider_id = body.get("provider_id", "").strip()
        bot_token = body.get("bot_token", "").strip()  # Telegram-specific
        if not user_id:
            flowfile.set_content(json.dumps({"error": "Authentication required"}).encode())
            flowfile.set_attribute("http.response.status", "401")
            return [flowfile]
        if not provider or not provider_id:
            flowfile.set_content(json.dumps({"error": "Missing provider or provider_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.identity_service import IdentityService
        linked = IdentityService.instance().link(
            user_id, provider, provider_id, bot_token=bot_token)
        if not linked:
            flowfile.set_content(json.dumps({
                "error": f"This {provider} ID is already linked to another user",
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        result = {"linked": True, "provider": provider, "provider_id": provider_id}
        # Telegram-specific: register personal bot
        if provider == "telegram" and bot_token:
            try:
                from services.telegram_bot_service import TelegramBotPool
                username = TelegramBotPool.instance().register_bot(bot_token, user_id)
                result["bot_username"] = username
            except Exception as e:
                result["bot_warning"] = f"Bot token invalid: {e}"
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    if action == "unlink_account":
        provider = body.get("provider", "").strip()
        if not user_id:
            flowfile.set_content(json.dumps({"error": "Authentication required"}).encode())
            flowfile.set_attribute("http.response.status", "401")
            return [flowfile]
        if not provider:
            flowfile.set_content(json.dumps({"error": "Missing provider"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        # Telegram-specific: unregister bot before unlinking
        if provider == "telegram":
            bot_token = ids.get_bot_token(user_id, "telegram")
            if bot_token:
                try:
                    from services.telegram_bot_service import TelegramBotPool
                    TelegramBotPool.instance().unregister_bot(bot_token)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unlinked = ids.unlink(user_id, provider)
        flowfile.set_content(json.dumps({
            "unlinked": unlinked, "provider": provider,
        }).encode())
        return [flowfile]

    if action == "list_linked_accounts":
        if not user_id:
            flowfile.set_content(json.dumps({"links": {}}).encode())
            return [flowfile]
        from core.identity_service import IdentityService
        links = IdentityService.instance().get_links(user_id)
        flowfile.set_content(json.dumps({"links": links}, ensure_ascii=False).encode())
        return [flowfile]

    return None
