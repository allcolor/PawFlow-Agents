"""AgentLoopTask actions — cancel interrupt"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_cancel_interrupt(self, action, body, store, user_id, flowfile):
    """Handle cancel interrupt actions. Returns [flowfile] or None."""


    if action == "cancel":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        self.cancel_agent(conv_id, agent_name=agent_name)
        # Force mode: kill the thread and force UI cleanup
        if body.get("force"):
            # Kill agent threads for this conversation
            _killed = 0
            for t in threading.enumerate():
                if t.name == f"agent-stream-{conv_id}" and t.is_alive():
                    # Python can't kill threads, but we can set status + publish done
                    # to force the UI to stop showing "thinking..."
                    _killed += 1
            store.set_status(conv_id, "idle")
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conv_id, "done", {
                    "response": "[Force stopped by user]",
                    "agent_name": agent_name or "",
                    "force_stopped": True,
                })
            # Clear active tracking
            with self._active_lock:
                self._active_conversations.pop(conv_id, None)
                self._user_active_conversations.discard(conv_id)
            logger.info(f"[agent:{conv_id[:8]}] FORCE STOPPED ({_killed} thread(s))")
        flowfile.set_content(json.dumps({
            "cancelled": True, "conversation_id": conv_id,
            "agent_name": agent_name or "all",
            "force": bool(body.get("force")),
        }).encode())
        return [flowfile]

    if action == "interrupt":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        self.interrupt_agent(conv_id, agent_name)
        flowfile.set_content(json.dumps({
            "interrupted": True, "conversation_id": conv_id,
            "agent_name": agent_name or "",
        }).encode())
        return [flowfile]

    if action == "btw":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        if agent_name and agent_name.upper() != "ALL":
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        question = body.get("message", "")
        if not conv_id or not question:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or message"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        user_id = flowfile.get_attribute("http.auth.principal") or ""
        # Handle ALL â€” spawn btw for each agent + default
        if agent_name.upper() == "ALL":
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            all_agents = rs.list_all("agent", user_id)
            targets = [a["name"] for a in all_agents]
            for t in targets:
                thread = threading.Thread(
                    target=self._btw_query,
                    args=(conv_id, t, question, user_id),
                    daemon=True,
                    name=f"btw-{t}-{conv_id[:8]}",
                )
                thread.start()
        else:
            thread = threading.Thread(
                target=self._btw_query,
                args=(conv_id, agent_name, question, user_id),
                daemon=True,
                name=f"btw-{agent_name or 'agent'}-{conv_id[:8]}",
            )
            thread.start()
        flowfile.set_content(json.dumps({
            "ok": True, "conversation_id": conv_id,
        }).encode())
        return [flowfile]

    if action == "broadcast_agents":
        # Send the same message to ALL defined agents in parallel
        conv_id = body.get("conversation_id", "")
        message = body.get("message", "")
        if not conv_id or not message:
            flowfile.set_content(json.dumps({
                "error": "conversation_id and message are required",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Launch broadcast in background thread
        thread = threading.Thread(
            target=self._broadcast_agents,
            args=(conv_id, message, user_id),
            daemon=True,
            name=f"broadcast-{conv_id[:8]}",
        )
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "broadcasting",
            "conversation_id": conv_id,
        }).encode())
        return [flowfile]

    return None
