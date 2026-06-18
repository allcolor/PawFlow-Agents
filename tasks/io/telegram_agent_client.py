"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
import base64
import json
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI

logger = logging.getLogger(__name__)

# Mixin/helper modules — split for the <=800-line rule.
# These imports also re-export the public + test-referenced surface so that
# `from tasks.io.telegram_agent_client import X` keeps working (invariant 1).
from tasks.io._telegram_client_helpers import (  # noqa: F401,E402
    _WIZARD_TTL_SECONDS, _WIZARDS, _WIZARD_LOCK, _parse_new_conversation_args, _guess_llm_service, _validate_relays, _wizard_key, _get_wizard, _save_wizard, _clear_wizard, _telegram_response, _telegram_command_name, _normalize_telegram_command_text, _apply_telegram_response, _format_telegram_command_result, _telegram_markdown_help, _inline_keyboard, _button, _start_new_conversation_wizard, _handle_resume_callback, _conversation_keyboard, _handle_new_conversation_callback, _available_agents, _agent_definition, _available_llm_services, _available_relays, _choose_agent_definition, _choose_llm_service, _choose_relays, _new_wizard_summary, _create_from_wizard, _next_agent_instance_name, _clean_instance_name)
from tasks.io._telegram_bridge import (  # noqa: F401,E402
    _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS, _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS, _TELEGRAM_SENT_ASSISTANT_CONTENT, _TELEGRAM_SENT_CONTENT_LOCK, _TELEGRAM_ASSISTANT_CONTENT_TTL, TelegramConversationBridgeTask, _format_attachment_summary, _collect_attachment_refs, _filestore_user_id_for_event, _telegram_agent_badge, _extract_thinking_text, _merge_thinking, _telegram_thinking_message, _telegram_blockquote, _telegram_render_message_text, _telegram_badge_color, _telegram_tool_display_name, _extract_filestore_refs, _load_filestore_media, _remember_forwarded_telegram_live_assistant, _telegram_assistant_msg_key, _telegram_assistant_content_text, _telegram_assistant_content_already_sent, _remember_sent_telegram_assistant_msg_id, _telegram_assistant_msg_id_was_sent, _telegram_live_assistant_was_forwarded, _is_telegram_origin_event, _compact_live_text)
from tasks.io._telegram_voice import (  # noqa: F401,E402
    _telegram_tts_enabled, _attach_telegram_tts_audio, _transcribe_telegram_voice, _transcribe_telegram_voice_result, _configured_tts_service_id)


class TelegramAgentClientTask(BaseTask):
    """Submit a Telegram message to the shared agent runtime and wait for done."""

    _max_instances = 20
    TYPE = "telegramAgentClient"
    VERSION = "1.0.0"
    NAME = "Telegram Agent Client"
    DESCRIPTION = "Submit Telegram messages through the shared agent API"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "client"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "agent_runtime_port": {
                "type": "string", "required": False,
                "default": "pawflow_agent.agent_runtime_in",
                "description": "Visible target runtime port for the shared AgentLoop runtime.",
            },
            "service_id": {
                "type": "string", "required": False,
                "default": "telegram_bot",
                "description": "Fallback TelegramBotService used for live progress messages.",
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
        callback_data = flowfile.get_attribute("telegram.callback_data") or ""
        wizard_response = self._handle_wizard_input(
            text, callback_data, user_id, chat_id)
        if wizard_response is not None:
            _apply_telegram_response(flowfile, wizard_response)
            return [flowfile]

        try:
            command_response = self._handle_command(text, user_id, chat_id)
        except Exception as exc:
            logger.warning("Telegram command handling failed: %s", exc, exc_info=True)
            flowfile.set_content(f"Command failed: {exc}".encode("utf-8"))
            return [flowfile]
        if command_response is not None:
            _apply_telegram_response(flowfile, command_response)
            return [flowfile]

        conversation_id = ids.get_active_conv(user_id, "telegram") or ""
        if not conversation_id:
            flowfile.set_content(
                b"No resumed conversation. Use /conv list then /conv select <id>."
            )
            return [flowfile]

        target_agent = self._selected_agent_for_conversation(conversation_id)
        if not target_agent:
            flowfile.set_content(
                b"No selected agent for this conversation. Select an agent before sending a message."
            )
            return [flowfile]

        if flowfile.get_attribute("telegram.message_type") in {"voice", "audio"}:
            transcribed, stt_error = _transcribe_telegram_voice_result(
                text, user_id, conversation_id, target_agent)
            if stt_error:
                flowfile.set_content(
                    f"Speech transcription failed: {stt_error}".encode("utf-8"))
                return [flowfile]
            if not transcribed:
                logger.info(
                    "Telegram voice message for %s produced an empty transcription",
                    conversation_id,
                )
                flowfile.set_content(
                    b"Voice message received but the transcription came back "
                    b"empty. Try again, or check the STT service with "
                    b"/sttservice.")
                return [flowfile]
            text = transcribed

        attachments = []
        image_base64 = flowfile.get_attribute("telegram.image_base64") or ""
        if image_base64:
            attachment = {
                "filename": "telegram_photo.jpg",
                "mime_type": "image/jpeg",
                "data": image_base64,
            }
            try:
                raw = base64.b64decode(image_base64)
                from core.file_store import FileStore
                from core.file_ttl import resolve_ttl_seconds
                _photo_ttl = resolve_ttl_seconds(
                    conversation_id=conversation_id or "",
                    conv_keys=("attachment_ttl_seconds", "webchat_upload_ttl_seconds"),
                    env_key="PAWFLOW_ATTACHMENT_TTL_SECONDS",
                    default=86400,
                )
                file_id = FileStore.instance().store(
                    "telegram_photo.jpg", raw, "image/jpeg",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    agent_name=target_agent,
                    ttl=_photo_ttl,
                    category="attachment",
                )
                attachment["file_id"] = file_id
                attachment["url"] = f"/files/{file_id}/telegram_photo.jpg"
            except Exception as exc:
                logger.warning("Telegram image FileStore materialization failed: %s", exc, exc_info=True)
            attachments.append(attachment)

        msg_id = f"telegram:{chat_id}:{flowfile.get_attribute('telegram.message_id') or ''}"
        request = AgentRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            target_agent=target_agent,
            message=text,
            attachments=attachments,
            msg_id=msg_id,
            channel="telegram",
            runtime_port=str(self.config.get("agent_runtime_port") or "").strip(),
            source_attributes={
                "telegram.chat_id": chat_id,
                "telegram.user_id": tg_user_id,
                "telegram.message_id": flowfile.get_attribute("telegram.message_id") or "",
            },
        )
        try:
            submission = AgentRuntimeAPI.submit_message(request)
            if not submission.wait_for_done:
                if submission.status == "queued":
                    flowfile.set_content(b"Message queued for the selected agent.")
                    flowfile.set_attribute("agent.conversation_id", submission.conversation_id)
                    flowfile.set_attribute("agent.turn_id", submission.turn_id)
                    return [flowfile]
                return []
            # NO implicit timeout — project rule. Block until the turn's
            # final answer arrives, however long it takes; the live bridge
            # streams progress meanwhile.
            result = AgentRuntimeAPI.wait_for_done(
                submission.conversation_id, submission.turn_id)
        except Exception as exc:
            logger.warning("Telegram agent submit failed: %s", exc, exc_info=True)
            runtime_port = str(self.config.get("agent_runtime_port") or "").strip()
            suffix = f" (runtime port: {runtime_port})" if runtime_port else ""
            flowfile.set_content(f"Agent request failed{suffix}: {exc}".encode("utf-8"))
            return [flowfile]

        if result is None:
            # No correlated waiter was registered (e.g. submission carried no
            # conversation/turn id); the final reply arrives through the live
            # bridge. This is not a timeout — the wait above is unbounded.
            logger.info(
                "Telegram agent request has no correlated waiter; final reply "
                "will arrive through the live bridge")
            return []
        if result.error:
            flowfile.set_content(f"Agent error: {result.error}".encode("utf-8"))
            return [flowfile]
        if _telegram_live_assistant_was_forwarded(
                submission.conversation_id, result.data):
            flowfile.set_content(b"")
        else:
            flowfile.set_content(str(result.response or "").encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", submission.conversation_id)
        flowfile.set_attribute("agent.turn_id", submission.turn_id)
        return [flowfile]

    def _handle_command(self, text: str, user_id: str, chat_id: str) -> Optional[str]:
        command = _telegram_command_name(text)
        if command == "/tts":
            return self._handle_tts_command(text, user_id)
        if command != "/conv":
            if text.startswith("/"):
                return self._handle_dispatch_command(text, user_id)
            return None
        from core.identity_service import IdentityService
        from core.conversation_store import ConversationStore
        ids = IdentityService.instance()
        store = ConversationStore.instance()
        parts = text.split(maxsplit=2)
        sub = parts[1] if len(parts) > 1 else "current"
        if sub == "current":
            active = ids.get_active_conv(user_id, "telegram") or ""
            return f"Active conversation: {active or '(none)'}"
        if sub == "new":
            if len(parts) <= 2:
                return _start_new_conversation_wizard(user_id, chat_id)
            try:
                cid, agent_name = self._create_conversation_from_command(
                    parts[2] if len(parts) > 2 else "", user_id)
            except ValueError as exc:
                return str(exc)
            ids.set_active_conv(user_id, "telegram", cid)
            return f"Created and selected conversation: {cid}\nAgent: {agent_name}"
        if sub == "list":
            convs = store.list_conversations(user_id=user_id)[:10]
            if not convs:
                return _telegram_response(
                    "No conversations yet.",
                    _inline_keyboard([[{"text": "New conversation", "callback_data": "conv:new:start"}]])
                )
            lines = ["Conversations:"]
            for idx, conv in enumerate(convs, 1):
                title = conv.get("title") or conv.get("preview") or conv["conversation_id"]
                lines.append(f"{idx}. {title[:60]} — {conv['conversation_id']}")
            return _telegram_response("\n".join(lines), _conversation_keyboard(convs))
        if sub == "select" and len(parts) <= 2:
            convs = store.list_conversations(user_id=user_id)[:10]
            if not convs:
                return _telegram_response(
                    "No conversations yet.",
                    _inline_keyboard([[{"text": "New conversation", "callback_data": "conv:new:start"}]])
                )
            return _telegram_response("Select a conversation to resume:", _conversation_keyboard(convs))
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
        return "Usage: /conv current | /conv list | /conv select <n|id> | /conv new <agent> --title <title> --relay <relay_id> [--llm <service>]"

    def _handle_dispatch_command(self, text: str, user_id: str) -> str:
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        conversation_id = ids.get_active_conv(user_id, "telegram") or ""
        command = _telegram_command_name(text)
        if not conversation_id and command != "/help":
            return "No resumed conversation. Use /conv list then /conv select <id>."
        agent_name = (self._selected_agent_for_conversation(
            conversation_id, persist_default=False) if conversation_id else "")
        body = {
            "action": "command",
            "text": _normalize_telegram_command_text(text),
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "_inline_response": True,
        }
        ff = FlowFile(content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        ff.set_attribute("http.auth.principal", user_id)
        ff.set_attribute("agent.client_channel", "telegram")
        ff.set_attribute("conversation_id", conversation_id)
        # Resolve the dispatcher the same way AgentRuntimeAPI.submit_message
        # does for regular messages: an explicit runtime port when configured,
        # otherwise the live AgentLoopTask singleton.
        runtime_port = str(self.config.get("agent_runtime_port") or "").strip()
        if runtime_port:
            from core.agent_runtime_ports import resolve_agent_runtime_task
            inst = resolve_agent_runtime_task(runtime_port)
        else:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
        if inst is None:
            raise RuntimeError(
                f"No live AgentLoopTask is available for runtime port: {runtime_port or '(default)'}")
        outputs = inst.execute(ff)
        out = outputs[0] if outputs else ff
        return _format_telegram_command_result(
            out.get_content().decode("utf-8", errors="replace"))

    def _handle_tts_command(self, text: str, user_id: str) -> str:
        from core.identity_service import IdentityService
        from core.conversation_store import ConversationStore
        ids = IdentityService.instance()
        store = ConversationStore.instance()
        conversation_id = ids.get_active_conv(user_id, "telegram") or ""
        if not conversation_id:
            return "No resumed conversation. Use /conv list then /conv select <id>."
        agent_name = self._selected_agent_for_conversation(conversation_id)
        parts = text.split(maxsplit=1)
        mode = (parts[1] if len(parts) > 1 else "status").strip().lower()
        if mode in ("status", ""):
            enabled = bool(store.get_extra(conversation_id, "telegram_tts_enabled"))
            service_id = _configured_tts_service_id(conversation_id, agent_name)
            return (
                f"Telegram TTS is {'on' if enabled else 'off'}."
                + (f" Service: {service_id}." if service_id else " No TTS service selected.")
            )
        if mode == "on":
            service_id = _configured_tts_service_id(conversation_id, agent_name)
            if not service_id:
                return "No TTS service selected for this conversation. Select one in PawFlow, then retry /tts on."
            store.set_extra(conversation_id, "telegram_tts_enabled", True)
            return f"Telegram TTS enabled. Text replies will also include audio via {service_id}."
        if mode == "off":
            store.set_extra(conversation_id, "telegram_tts_enabled", False)
            return "Telegram TTS disabled."
        return "Usage: /tts on | /tts off | /tts status"

    def _handle_wizard_input(
        self, text: str, callback_data: str, user_id: str, chat_id: str,
    ) -> Optional[Any]:
        key = _wizard_key(user_id, chat_id)
        if callback_data.startswith("conv:resume:"):
            return _handle_resume_callback(callback_data, user_id)
        if callback_data == "conv:new:start":
            return _start_new_conversation_wizard(user_id, chat_id)
        if callback_data.startswith("conv:new:"):
            return _handle_new_conversation_callback(callback_data, user_id, chat_id)
        state = _get_wizard(key)
        if not state or state.get("mode") != "new":
            return None
        if text.startswith("/"):
            return None
        step = state.get("step")
        if step == "title":
            if not text:
                return "Send a title for the conversation."
            state["title"] = text[:120]
            state["step"] = "agent_def"
            _save_wizard(key, state)
            return _choose_agent_definition(user_id)
        if step == "agent_name":
            name = _clean_instance_name(text)
            if not name:
                return "Send a valid agent instance name."
            used = {str(a.get("instance_name") or "") for a in state.get("agents") or []}
            if name in used:
                return "This agent instance name is already used. Send another name."
            state["pending_instance_name"] = name
            state["step"] = "llm"
            _save_wizard(key, state)
            return _choose_llm_service(user_id)
        return None

    @staticmethod
    def _selected_agent_for_conversation(conversation_id: str,
                                         persist_default: bool = True) -> str:
        from core.conversation_store import ConversationStore
        from core.conv_agent_config import get_all_agent_configs
        store = ConversationStore.instance()
        active = store.get_extra(conversation_id, "active_resources") or {}
        selected = str(active.get("agent") or "").strip()
        if selected:
            return selected
        members = get_all_agent_configs(conversation_id)
        if not members:
            return ""
        selected = next(iter(members.keys()), "")
        if selected and persist_default:
            active["agent"] = selected
            store.set_extra(conversation_id, "active_resources", active)
        return selected

    @staticmethod
    def _create_conversation_from_command(args: str, user_id: str):
        opts = _parse_new_conversation_args(args)
        agent_name = opts["agent"]
        if not agent_name:
            raise ValueError(
                "Usage: /conv new <agent> --title <title> --relay <relay_id> [--llm <service>]")
        if not opts["title"]:
            raise ValueError("Conversation title is required: add --title <title>.")
        if not opts["relays"]:
            raise ValueError("At least one relay is required: add --relay <relay_id>.")

        from core.conversation_creation import create_conversation
        from core.resource_store import ResourceStore
        from core.service_registry import ServiceRegistry

        rs = ResourceStore.instance()
        agent_def = rs.get_any("agent", agent_name, user_id)
        if not agent_def:
            raise ValueError(f"Agent definition not found: {agent_name}")

        reg = ServiceRegistry.get_instance()
        services = [
            s for s in reg.resolve_by_type("llmConnection", user_id=user_id)
            if getattr(s, "enabled", True)
        ]
        service_ids = {s.service_id for s in services}
        llm_service = opts["llm"] or _guess_llm_service(agent_name, services)
        if not llm_service:
            raise ValueError("No enabled LLM service available. Configure one first.")
        if llm_service not in service_ids:
            raise ValueError(f"LLM service not found or disabled: {llm_service}")

        relay_ids = _validate_relays(opts["relays"], user_id=user_id)

        result = create_conversation(user_id, {
            "agents": [{
                "instance_name": agent_name,
                "definition": agent_name,
                "llm_service": llm_service,
                "params": {"name": agent_name},
                "model": str(agent_def.get("model") or ""),
                "tools": agent_def.get("tools") or [],
                "max_depth": int(agent_def.get("max_depth", 1000) or 1000),
                "skills": agent_def.get("skills") or [],
            }],
            "title": opts["title"],
            "relays": relay_ids,
            "default_relay": relay_ids[0],
        })
        cid = result["conversation_id"]
        return cid, agent_name


TaskFactory.register(TelegramAgentClientTask)
TaskFactory.register(TelegramConversationBridgeTask)
