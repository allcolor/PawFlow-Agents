"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI

logger = logging.getLogger(__name__)


class TelegramAgentClientTask(BaseTask):
    """Submit a Telegram message to the shared agent runtime and wait for done."""

    TYPE = "telegramAgentClient"
    VERSION = "1.0.0"
    NAME = "Telegram Agent Client"
    DESCRIPTION = "Submit Telegram messages through the shared agent API"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "client"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "target_agent": {
                "type": "string", "required": False, "default": "",
                "description": "Agent instance to target. Empty uses the active conversation agent.",
            },
            "timeout": {
                "type": "number", "required": False, "default": 600,
                "description": "Seconds to wait for the final agent response.",
            },
            "create_conversation": {
                "type": "boolean", "required": False, "default": True,
                "description": "Create a conversation when Telegram has no active one.",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        chat_id = flowfile.get_attribute("telegram.chat_id") or ""
        if not tg_user_id or not chat_id:
            flowfile.set_content(b"Telegram user and chat attributes are required.")
            return [flowfile]

        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        user_id = ids.resolve_user("telegram", tg_user_id)
        if not user_id:
            flowfile.set_content(
                b"Access denied. Link your Telegram account from PawFlow first."
            )
            return [flowfile]

        text = flowfile.get_content().decode("utf-8", errors="replace").strip()
        command_response = self._handle_command(text, user_id, chat_id)
        if command_response is not None:
            flowfile.set_content(command_response.encode("utf-8"))
            return [flowfile]

        conversation_id = ids.get_active_conv(user_id, "telegram") or ""
        if not conversation_id:
            if not bool(self.config.get("create_conversation", True)):
                flowfile.set_content(
                    b"No active conversation. Use /conv list or /conv new."
                )
                return [flowfile]
            conversation_id = self._create_conversation(user_id)
            ids.set_active_conv(user_id, "telegram", conversation_id)

        msg_id = f"telegram:{chat_id}:{flowfile.get_attribute('telegram.message_id') or ''}"
        request = AgentRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            target_agent=str(self.config.get("target_agent") or ""),
            message=text,
            msg_id=msg_id,
            channel="telegram",
            source_attributes={
                "telegram.chat_id": chat_id,
                "telegram.user_id": tg_user_id,
                "telegram.message_id": flowfile.get_attribute("telegram.message_id") or "",
            },
        )
        try:
            submission = AgentRuntimeAPI.submit_message(request)
            timeout = float(self.config.get("timeout", 600) or 600)
            result = AgentRuntimeAPI.wait_for_done(
                submission.conversation_id, submission.turn_id, timeout=timeout)
        except Exception as exc:
            logger.warning("Telegram agent submit failed: %s", exc, exc_info=True)
            flowfile.set_content(f"Agent request failed: {exc}".encode("utf-8"))
            return [flowfile]

        if result is None:
            flowfile.set_content(b"Agent response timed out.")
            return [flowfile]
        if result.error:
            flowfile.set_content(f"Agent error: {result.error}".encode("utf-8"))
            return [flowfile]
        flowfile.set_content((result.response or "").encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", submission.conversation_id)
        flowfile.set_attribute("agent.turn_id", submission.turn_id)
        return [flowfile]

    def _handle_command(self, text: str, user_id: str, chat_id: str) -> Optional[str]:
        if not text.startswith("/conv"):
            return None
        from core.identity_service import IdentityService
        from core.conversation_store import ConversationStore
        ids = IdentityService.instance()
        store = ConversationStore.instance()
        parts = text.split()
        sub = parts[1] if len(parts) > 1 else "current"
        if sub == "current":
            active = ids.get_active_conv(user_id, "telegram") or ""
            return f"Active conversation: {active or '(none)'}"
        if sub == "new":
            cid = self._create_conversation(user_id)
            ids.set_active_conv(user_id, "telegram", cid)
            return f"Created and selected conversation: {cid}"
        if sub == "list":
            convs = store.list_conversations(user_id=user_id)[:10]
            if not convs:
                return "No conversations. Use /conv new."
            lines = ["Conversations:"]
            for idx, conv in enumerate(convs, 1):
                title = conv.get("title") or conv.get("preview") or conv["conversation_id"]
                lines.append(f"{idx}. {title[:60]} — {conv['conversation_id']}")
            return "\n".join(lines)
        if sub == "select" and len(parts) > 2:
            wanted = parts[2]
            convs = store.list_conversations(user_id=user_id)
            selected = ""
            if wanted.isdigit():
                idx = int(wanted) - 1
                if 0 <= idx < min(len(convs), 10):
                    selected = convs[idx]["conversation_id"]
            else:
                for conv in convs:
                    cid = conv["conversation_id"]
                    if cid == wanted or cid.startswith(wanted):
                        selected = cid
                        break
            if not selected:
                return "Conversation not found. Use /conv list."
            ids.set_active_conv(user_id, "telegram", selected)
            return f"Selected conversation: {selected}"
        return "Usage: /conv current | /conv list | /conv select <n|id> | /conv new"

    @staticmethod
    def _create_conversation(user_id: str) -> str:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        cid = store.generate_id()
        store.save(cid, [], user_id=user_id)
        return cid


class TelegramConversationBridgeTask(BaseTask):
    """Relay shared conversation events to Telegram chats in compact mode."""

    TYPE = "telegramConversationBridge"
    VERSION = "1.0.0"
    NAME = "Telegram Conversation Bridge"
    DESCRIPTION = "Forward conversation events to active Telegram subscribers"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "events"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._registered = False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string", "required": True,
                "description": "Fallback TelegramBotService for users without a personal bot token.",
            },
        }

    def initialize(self):
        if self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().add_listener(self._on_event)
        self._registered = True

    def cleanup(self):
        if not self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().remove_listener(self._on_event)
        self._registered = False

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        return [flowfile]

    def _on_event(self, conversation_id: str, event_type: str, data: Any) -> None:
        if event_type not in {"new_message", "done", "error_event"}:
            return
        if not isinstance(data, dict):
            return
        if data.get("channel") == "telegram":
            return
        text = self._format_event(event_type, data)
        if not text:
            return
        for user_id, chat_id in self._telegram_subscribers(conversation_id):
            self._send(user_id, chat_id, text)

    def _format_event(self, event_type: str, data: Dict[str, Any]) -> str:
        if event_type == "new_message":
            if data.get("role") != "user":
                return ""
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            name = source.get("name") or data.get("channel") or "user"
            content = str(data.get("content") or "").strip()
            return f"[{name}] {content}" if content else ""
        if event_type == "done":
            agent = data.get("agent_name") or "assistant"
            response = str(data.get("response") or "").strip()
            return f"[{agent}] {response}" if response else ""
        if event_type == "error_event":
            message = str(data.get("message") or "").strip()
            return f"[error] {message}" if message else ""
        return ""

    @staticmethod
    def _telegram_subscribers(conversation_id: str):
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        for user_id, links in ids.list_all().items():
            chat_id = links.get("telegram") if isinstance(links, dict) else ""
            if not chat_id:
                continue
            if ids.get_active_conv(user_id, "telegram") == conversation_id:
                yield user_id, chat_id

    def _send(self, user_id: str, chat_id: str, text: str) -> None:
        try:
            from core.identity_service import IdentityService
            bot_token = IdentityService.instance().get_bot_token(user_id, "telegram")
            if bot_token:
                from services.telegram_bot_service import TelegramBotPool
                TelegramBotPool.instance().send_message(bot_token, chat_id, text)
                return
            svc = self.get_service(self.config.get("service_id", ""))
            if svc:
                svc.ensure_connected()
                svc.send_message(chat_id, text)
        except Exception as exc:
            logger.warning("Telegram bridge send failed for %s: %s", chat_id, exc)


TaskFactory.register(TelegramAgentClientTask)
TaskFactory.register(TelegramConversationBridgeTask)

