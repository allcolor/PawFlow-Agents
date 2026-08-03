"""Google Chat webhook and shared-agent client tasks.

The webhook task verifies Google authentication and acknowledges the HTTP
request before downstream work starts.  The agent task enforces owner/space
policy, preserves the real external actor as provenance, and streams each live
assistant message back into the originating Chat thread.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from typing import Any, Dict, List, Optional, Tuple

from core import FlowFile, TaskFactory
from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
from core.base_task import BaseTask
from core.google_chat_store import GoogleChatSpaceStore


logger = logging.getLogger(__name__)


def _event_space(event: Dict[str, Any]) -> Tuple[str, str, str]:
    space = event.get("space") if isinstance(event.get("space"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if not space and isinstance(message.get("space"), dict):
        space = message["space"]
    return (
        str(space.get("name") or ""),
        str(space.get("displayName") or ""),
        str(space.get("type") or ""),
    )


def _event_actor(event: Dict[str, Any]) -> Tuple[str, str, str]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    actor = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    if not actor and isinstance(event.get("user"), dict):
        actor = event["user"]
    return (
        str(actor.get("name") or ""),
        str(actor.get("displayName") or ""),
        str(actor.get("type") or ""),
    )


def _event_id(event: Dict[str, Any]) -> str:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    name = str(message.get("name") or "")
    if name:
        return name
    space_id, _, _ = _event_space(event)
    return ":".join((str(event.get("type") or ""), space_id,
                     str(event.get("eventTime") or event.get("event_time") or "")))


class GoogleChatWebhookTask(BaseTask):
    """Verify and immediately acknowledge a Google Chat interaction webhook."""

    TYPE = "googleChatWebhook"
    VERSION = "1.0.0"
    NAME = "Google Chat Webhook"
    DESCRIPTION = "Verify and acknowledge Google Chat HTTP events"
    ICON = "google"
    TAGS = ["google", "chat", "http", "source"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {"type": "string", "required": True},
            "http_service_id": {"type": "string", "required": True},
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        chat = self.get_service(str(self.config.get("service_id") or ""))
        listener = self.get_service(str(self.config.get("http_service_id") or ""))
        if chat is None:
            raise RuntimeError("GoogleChatService not found")
        if listener is None:
            raise RuntimeError("HTTPListenerService not found")
        request_id = flowfile.get_attribute("http.request.id") or ""
        if not request_id:
            raise RuntimeError("Missing http.request.id")
        status = 200
        body = b"{}"
        try:
            chat.verify_request(flowfile.get_attribute("http.header.authorization") or "")
        except Exception as exc:
            logger.warning("Google Chat webhook rejected: %s", exc)
            status = 401
            body = json.dumps({"error": "unauthorized"}).encode("utf-8")
            flowfile.set_attribute("google_chat.skip", "true")
        else:
            try:
                event = json.loads(flowfile.get_content().decode("utf-8"))
                if not isinstance(event, dict):
                    raise ValueError("Google Chat event must be an object")
                flowfile.set_attribute(
                    "google_chat.event_json", json.dumps(event, ensure_ascii=False))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                logger.warning("Malformed Google Chat event: %s", exc)
                status = 400
                body = json.dumps({"error": "invalid event"}).encode("utf-8")
                flowfile.set_attribute("google_chat.skip", "true")
        listener.submit_response(
            request_id, status, {"Content-Type": "application/json"}, body)
        flowfile.set_attribute("http.response.sent", "true")
        flowfile.set_attribute("http.response.status.sent", str(status))
        flowfile.set_content(body)
        return [flowfile]


class GoogleChatAgentClientTask(BaseTask):
    """Apply Google Chat policy and submit allowed messages to the agent runtime."""

    TYPE = "googleChatAgentClient"
    VERSION = "1.0.0"
    NAME = "Google Chat Agent Client"
    DESCRIPTION = "Route authorized Google Chat messages to a PawFlow conversation"
    ICON = "google"
    TAGS = ["google", "chat", "agent", "client"]
    _max_instances = 20

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._owner_user_id = ""

    def set_runtime_context(self, *, user_id: str = "", **_: Any) -> None:
        self._owner_user_id = str(user_id or "")

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {"type": "string", "required": True},
            "owner_google_user_id": {
                "type": "string", "required": True,
                "description": "Immutable users/... identity allowed to administer the app.",
            },
            "agent_runtime_port": {
                "type": "string", "required": False,
                "default": "pawflow_agent.agent_runtime_in",
            },
            "direct_conversation_id": {
                "type": "string", "required": False, "default": "",
                "description": "Conversation used for owner-only direct messages.",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if flowfile.get_attribute("google_chat.skip") == "true":
            return []
        if not self._owner_user_id:
            raise ValueError("googleChatAgentClient requires a user-scoped deployment")
        service_id = str(self.config.get("service_id") or "")
        service = self.get_service(service_id)
        if service is None:
            raise RuntimeError(f"GoogleChatService '{service_id}' not found")
        owner_google_id = str(self.config.get("owner_google_user_id") or "").strip()
        if not owner_google_id:
            raise ValueError("owner_google_user_id is required")
        event = json.loads(flowfile.get_attribute("google_chat.event_json") or "{}")
        event_type = str(event.get("type") or "").upper()
        space_id, display_name, space_type = _event_space(event)
        actor_id, actor_name, actor_type = _event_actor(event)
        if not space_id:
            return []
        store = GoogleChatSpaceStore(self._owner_user_id, service_id)
        event_id = _event_id(event)
        if not event_id or not store.claim_event(event_id):
            return []
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        thread = message.get("thread") if isinstance(message.get("thread"), dict) else {}
        thread_name = str(thread.get("name") or "")

        if event_type == "ADDED_TO_SPACE":
            store.observe_space(space_id, display_name)
            self._send(service, space_id,
                       "This space is pending approval by the PawFlow bot owner.",
                       thread_name)
            return []
        if event_type == "REMOVED_FROM_SPACE":
            store.set_status(space_id, "removed")
            return []
        if event_type != "MESSAGE" or actor_type.upper() == "BOT":
            return []

        text = str(message.get("argumentText") or message.get("text") or "").strip()
        if text.lower().startswith("/gchat"):
            reply = self._handle_admin(
                text, store, space_id, actor_id, owner_google_id)
            self._send(service, space_id, reply, thread_name)
            return []

        is_dm = space_type.upper() in {"DIRECT_MESSAGE", "DM"}
        if is_dm:
            if actor_id != owner_google_id:
                self._send(service, space_id, "Access denied: direct messages are owner-only.", thread_name)
                return []
            conversation_id = str(self.config.get("direct_conversation_id") or "").strip()
            permission_mode = "default"
            if not conversation_id:
                self._send(service, space_id, "No direct-message conversation is configured.", thread_name)
                return []
            try:
                self._require_owned_conversation(conversation_id)
            except ValueError as exc:
                self._send(service, space_id, str(exc), thread_name)
                return []
        else:
            row = store.observe_space(space_id, display_name)
            if row.get("status") != "allowed":
                self._send(service, space_id,
                           "This space is not authorized by the PawFlow bot owner.",
                           thread_name)
                return []
            conversation_id = str(row.get("conversation_id") or "")
            permission_mode = "read_only"

        target_agent = self._selected_agent(conversation_id)
        if not target_agent:
            self._send(service, space_id,
                       "The linked PawFlow conversation has no selected agent.", thread_name)
            return []
        attachments = self._attachments(
            service, message.get("attachment") or [], conversation_id, target_agent)
        if not text and not attachments:
            return []

        sent_live = set()
        sent_lock = threading.Lock()

        def live_callback(_cid: str, event_name: str, data: Any) -> None:
            if event_name != "new_message" or not isinstance(data, dict):
                return
            if str(data.get("role") or "") != "assistant":
                return
            content = str(data.get("content") or "").strip()
            msg_id = str(data.get("msg_id") or "")
            if not content:
                return
            key = msg_id or content
            with sent_lock:
                if key in sent_live:
                    return
                sent_live.add(key)
            self._send(service, space_id, content, thread_name)

        request = AgentRequest(
            user_id=self._owner_user_id,
            conversation_id=conversation_id,
            target_agent=target_agent,
            message=text,
            attachments=attachments,
            msg_id=f"google_chat:{event_id}",
            channel="google_chat",
            runtime_port=str(self.config.get("agent_runtime_port") or "").strip(),
            permission_mode=permission_mode,
            source_attributes={
                "google_chat.space_id": space_id,
                "google_chat.thread_id": thread_name,
                "google_chat.message_id": str(message.get("name") or ""),
                "google_chat.actor_id": actor_id,
                "google_chat.actor_name": actor_name,
                "google_chat.owner_user_id": self._owner_user_id,
            },
            live_callback=live_callback,
        )
        try:
            submission = AgentRuntimeAPI.submit_message(request)
            if not submission.wait_for_done:
                return []
            result = AgentRuntimeAPI.wait_for_done(
                submission.conversation_id, submission.turn_id)
            if result and result.error:
                self._send(service, space_id, f"Agent error: {result.error}", thread_name)
            elif result and result.response and not sent_live:
                self._send(service, space_id, result.response, thread_name)
        except Exception as exc:
            logger.warning("Google Chat agent submission failed: %s", exc, exc_info=True)
            self._send(service, space_id, f"Agent request failed: {exc}", thread_name)
        return []

    def _handle_admin(self, text: str, store: GoogleChatSpaceStore,
                      space_id: str, actor_id: str, owner_google_id: str) -> str:
        if actor_id != owner_google_id:
            return "Access denied: only the configured bot owner can administer spaces."
        parts = text.split()
        action = parts[1].lower() if len(parts) > 1 else "status"
        if action == "status":
            row = store.get_space(space_id)
            return (
                f"Space status: {row.get('status', 'pending')}\n"
                f"Conversation: {row.get('conversation_id') or '(none)'}\n"
                f"Permission mode: {row.get('permission_mode', 'read_only')}"
            )
        if action == "deny":
            store.set_status(space_id, "denied")
            return "This space is now denied."
        if action == "allow":
            if len(parts) < 3:
                return "Usage: /gchat allow <conversation_id>"
            conversation_id = parts[2]
            permission_mode = parts[3].lower() if len(parts) > 3 else "read_only"
            try:
                self._require_owned_conversation(conversation_id)
                store.allow_space(space_id, conversation_id, permission_mode)
            except ValueError as exc:
                return str(exc)
            return (
                f"This space is authorized for conversation {conversation_id} "
                f"with {permission_mode} permissions."
            )
        return "Usage: /gchat status | allow <conversation_id> | deny"

    def _require_owned_conversation(self, conversation_id: str) -> None:
        from core.conversation_store import ConversationStore
        owner = ConversationStore.instance().resolve_owner(conversation_id)
        if owner != self._owner_user_id:
            raise ValueError("Conversation not found for the configured PawFlow owner.")
        if not self._selected_agent(conversation_id):
            raise ValueError("The conversation must have a selected agent before authorization.")

    @staticmethod
    def _selected_agent(conversation_id: str) -> str:
        if not conversation_id:
            return ""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        resources = store.get_extra(conversation_id, "active_resources") or {}
        return str(resources.get("agent") or store.get_extra(
            conversation_id, "selectedAgent") or "").strip()

    def _attachments(self, service: Any, rows: List[Any], conversation_id: str,
                     target_agent: str) -> List[Dict[str, Any]]:
        attachments: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ref = row.get("attachmentDataRef")
            ref = ref if isinstance(ref, dict) else {}
            resource_name = str(ref.get("resourceName") or "")
            if not resource_name:
                continue
            filename = str(row.get("contentName") or "google_chat_attachment")
            mime_type = str(row.get("contentType") or mimetypes.guess_type(filename)[0]
                            or "application/octet-stream")
            try:
                raw = service.download_attachment(resource_name)
                from core.file_store import FileStore
                file_id = FileStore.instance().store(
                    filename, raw, mime_type, conversation_id=conversation_id,
                    user_id=self._owner_user_id, agent_name=target_agent,
                    category="attachment")
                attachments.append({
                    "filename": filename, "mime_type": mime_type,
                    "file_id": file_id, "url": f"/files/{file_id}/{filename}",
                })
            except Exception as exc:
                logger.warning("Google Chat attachment download failed: %s", exc)
        return attachments

    @staticmethod
    def _send(service: Any, space_id: str, text: str, thread_name: str) -> None:
        for start in range(0, len(str(text or "")), 4000):
            chunk = str(text)[start:start + 4000]
            if chunk.strip():
                service.send_message(space_id, chunk, thread_name=thread_name)


TaskFactory.register(GoogleChatWebhookTask)
TaskFactory.register(GoogleChatAgentClientTask)
