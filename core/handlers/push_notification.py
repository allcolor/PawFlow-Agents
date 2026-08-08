"""PushNotification handler — pawflow replacement for the Claude Code
built-in `PushNotification`.

Claude Code's built-in sends an OS desktop/mobile push (requires its own
notification infra). In pawflow every client watches the same conversation
over SSE, so a notification is a runtime event on the conversation bus:

  - SSE event `notification` fires on all connected webchat clients
  - Front-end accumulates it in the tab-local notification center, plays a
    bell, shows a toast, flashes the tab title, and calls the browser
    Notification API when the tab is backgrounded
  - Nothing is written to the transcript or agent context.

Rate-limit: one notification per (conv, agent) per 5s. A buggy agent that
loops on PushNotification cannot flood the webchat.
"""

import logging
import threading
import time
import uuid
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

_RATE_LIMIT_WINDOW_SEC = 5.0
_MAX_MESSAGE_CHARS = 200


class PushNotificationHandler(ToolHandler):
    """Send a proactive notification to every client watching this conv.

    Replaces the Claude Code built-in `PushNotification` (blocked via
    --disallowedTools). Agents invoke it via
    mcp__pawflow__use_tool(PushNotification, {message=..., status=...}).
    """

    _conversation_id: str = ""
    _agent_name: str = ""
    _user_id: str = ""

    # (conv_id, agent_name) -> last fire monotonic ts. Class-level so
    # multiple handler instances (per registry) share the same cooldown.
    _last_fire: Dict[tuple, float] = {}
    _last_fire_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "PushNotification"

    @property
    def description(self) -> str:
        return (
            "Send a proactive notification that pulls the user's attention "
            "back to this conversation — they hear a bell, see a toast, and "
            "get a browser-native notification if the tab is backgrounded. "
            "Use ONLY when the user has likely walked away and there is "
            "something worth coming back for (long task finished, build "
            "failed with a decision to make, error needs input). Routine "
            "progress updates DO NOT qualify — those arrive in chat already. "
            "A notification the user didn't need accumulates annoyance; err "
            "toward not sending one. Lead with what they'd act on. Rate-"
            "limited to one per 5s per agent."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        f"Notification body. One line, no markdown, "
                        f"<= {_MAX_MESSAGE_CHARS} chars (mobile OS truncate "
                        f"past that). Example: 'build failed: 2 auth tests' "
                        f"beats 'task done'."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": ["proactive"],
                    "description": (
                        "Always 'proactive' — reserved for future status "
                        "types. Carried to telemetry for filtering."
                    ),
                },
            },
            "required": ["message", "status"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_agent_name(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        message = (arguments.get("message") or "").strip()
        status = arguments.get("status") or "proactive"

        if not message:
            return "Error: 'message' is required and must be non-empty."
        if len(message) > _MAX_MESSAGE_CHARS:
            # Truncate rather than reject: mobile OSes cut past 200 anyway,
            # and rejecting would just make the agent retry.
            message = message[: _MAX_MESSAGE_CHARS - 1] + "…"
        if "\n" in message or "\r" in message:
            message = message.replace("\n", " ").replace("\r", " ").strip()

        if not self._conversation_id:
            return "Error: no conversation context — cannot send notification."

        # Rate-limit per (conv, agent). Cooldown is intentionally short; the
        # real protection is the description text telling the agent when to
        # send. This just caps runaway loops.
        rl_key = (self._conversation_id, self._agent_name or "")
        now = time.monotonic()
        with self._last_fire_lock:
            prev = self._last_fire.get(rl_key, 0.0)
            if prev and (now - prev) < _RATE_LIMIT_WINDOW_SEC:
                remaining = _RATE_LIMIT_WINDOW_SEC - (now - prev)
                return (
                    f"Error: notification rate-limited. "
                    f"Retry in {remaining:.1f}s. Consider batching status "
                    f"into the next chat message instead."
                )
            self._last_fire[rl_key] = now

        # Runtime-only delivery: notifications must not become fake user
        # messages or enter the LLM context. The event bus fans the event out
        # to every live client; each browser tab owns its in-memory history.
        from core.conversation_event_bus import ConversationEventBus
        msg_id = uuid.uuid4().hex[:12]
        ConversationEventBus.instance().publish_event(
            self._conversation_id,
            "notification",
            {
                "msg_id": msg_id,
                "content": message,
                "agent": self._agent_name or "",
                "status": status,
                "ts": time.time(),
            },
        )

        logger.info(
            "[push_notif] conv=%s agent=%s status=%s chars=%d",
            self._conversation_id[:8], self._agent_name or "?",
            status, len(message))
        return f"Notification delivered ({len(message)} chars)."


__all__ = ["PushNotificationHandler"]
