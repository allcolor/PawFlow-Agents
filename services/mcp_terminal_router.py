"""Route persisted prompts to active published MCP terminals."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def route_published_terminal_prompt(
        conversation_id: str, agent_name: str, content: str,
        message_id: str, *, channel: str = "web",
        attachments: Any = None) -> Optional[bool]:
    """Inject a canonical prompt.

    Return ``None`` when no published external target owns the request, ``True``
    after injection, and ``False`` when the external target exists but its
    terminal is unavailable. External agents must never fall back to an
    internal PawFlow LLM runtime.

    Persistence is deliberately owned by the caller. This function only routes
    the already-stamped message and never writes conversation state itself.
    """
    if (channel not in {
            "web", "a2a", "cross_conversation_delegate",
            "delegate", "delegate_reply"}
            or not conversation_id
            or not agent_name or not message_id or not str(content).strip()):
        return None
    try:
        from core.mcp_server_store import MCPServerStore
        store = MCPServerStore.instance()
        server = store.get_for_conversation(conversation_id, agent_name)
        if not server or not server.get("enabled"):
            return None
        if not server.get("terminal_ready"):
            return False
        terminal = store.get_terminal_registration(server["server_id"])
        relay_id = str(terminal.get("active_relay_id") or "")
        if not relay_id or not terminal.get("terminal_session_id"):
            return False
        from core.service_registry import ServiceRegistry
        relay = ServiceRegistry.get_instance().resolve(
            relay_id, user_id=server["owner_user_id"],
            conv_id=conversation_id)
        if not relay or not relay.is_connected():
            return False
        result = relay._request(  # pylint: disable=protected-access
            "mcp_terminal_inject", ".",
            terminal_kind=str(terminal.get("terminal_kind") or ""),
            terminal_target=str(terminal.get("terminal_target") or ""),
            terminal_secret=str(terminal.get("terminal_secret") or ""),
            terminal_state_path=str(terminal.get("terminal_state_path") or ""),
            message_id=message_id,
            content=str(content),
            _request_timeout=15,
            _retry_on_disconnect=False,
        )
        return bool(isinstance(result, dict) and result.get("ok"))
    except Exception as exc:
        logger.warning(
            "Published MCP terminal injection failed for %s/%s: %s",
            conversation_id[:8], agent_name, exc)
        return False


def complete_published_terminal_turn(
        server: dict, reply_to_message_id: str, content: str,
        *, error: str = "") -> bool:
    """Complete the PawFlow request whose prompt was injected into a TUI.

    The durable request message is the correlation record. AgentRuntimeAPI
    waiters receive a normal done event. Same-conversation delegates also
    deliver their private result to the caller unless a synchronous published
    MCP tool call already owns that task.
    """
    reply_to_message_id = str(reply_to_message_id or "").strip()
    if not reply_to_message_id:
        return False
    conversation_id = str(server.get("conversation_id") or "")
    owner_user_id = str(server.get("owner_user_id") or "")
    agent_name = str(server.get("agent_name") or "")
    try:
        from core.conversation_store import ConversationStore
        rows = ConversationStore.instance().load(
            conversation_id, user_id=owner_user_id) or []
        request = next((
            row for row in reversed(rows)
            if str(row.get("msg_id") or "") == reply_to_message_id
        ), None)
        if not isinstance(request, dict):
            logger.warning(
                "External MCP response correlation message was not found: %s",
                reply_to_message_id)
            return False
        source = request.get("source") or {}
        if not isinstance(source, dict):
            source = {}

        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conversation_id, "done", {
                "turn_id": reply_to_message_id,
                "request_msg_id": reply_to_message_id,
                "response": "" if error else str(content),
                "error": str(error or ""),
                "agent_name": agent_name,
            })

        if source.get("type") != "agent_delegate":
            return True
        task_id = str(source.get("task_id") or "")
        caller_agent = str(source.get("from") or "")
        payload = {
            "task_id": task_id,
            "agent": agent_name,
            "status": "failed" if error else "completed",
            "response": "" if error else str(content),
            "error": str(error or ""),
        }
        if task_id:
            from core.external_call_router import complete_task
            if complete_task(task_id, payload):
                return True
        if not caller_agent:
            return True
        if error:
            reply = (
                f"[Delegate result for task_id={task_id}] Agent "
                f"'{agent_name}' FAILED: {error}")
        else:
            reply = (
                f"[Delegate result for task_id={task_id}] Agent "
                f"'{agent_name}' replied:\n\n{content}")
        from core.handlers.resource_agent import SpawnAgentsHandler
        SpawnAgentsHandler()._deliver_to_caller(  # pylint: disable=protected-access
            conversation_id, caller_agent, owner_user_id, reply,
            "external_delegate_result:" + (task_id or reply_to_message_id),
            task_id, agent_name, "")
        return True
    except Exception:
        logger.exception(
            "Could not complete external MCP turn %s", reply_to_message_id)
        return False


def complete_published_terminal_target(
        conversation_id: str, agent_name: str, reply_to_message_id: str,
        content: str, *, error: str = "") -> bool:
    """Resolve one published target and complete its correlated turn."""
    try:
        from core.mcp_server_store import MCPServerStore
        server = MCPServerStore.instance().get_for_conversation(
            conversation_id, agent_name)
        if not server:
            return False
        return complete_published_terminal_turn(
            server, reply_to_message_id, content, error=error)
    except Exception:
        logger.exception(
            "Could not resolve external MCP target %s/%s",
            conversation_id[:8], agent_name)
        return False


__all__ = [
    "route_published_terminal_prompt", "complete_published_terminal_turn",
    "complete_published_terminal_target"]
