"""PushNotification handler — pawflow replacement for the Claude Code
built-in `PushNotification`.

Claude Code's built-in sends an OS desktop/mobile push (requires its own
notification infra). In pawflow every client watches the same conversation
over SSE, so a 'notification' is simply a specially-tagged message published
on the conversation bus:

  - ConversationWriter persists it (row visible in transcript on reload)
  - SSE event `notification` fires on all connected webchat clients
  - Front-end plays a bell sound, shows a toast, flashes the tab title,
    and calls the browser Notification API when the tab is backgrounded.

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

        # Persist + publish via the conversation writer so every connected
        # client receives it and history replays it on reload. Role = user,
        # source.type = system / name = notification — the renderer branches
        # on this tag to produce the bell row.
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message

        msg_id = uuid.uuid4().hex[:12]
        stamped = stamp_message({
            "role": "user",
            "content": message,
            "msg_id": msg_id,
            "source": {
                "type": "system",
                "name": "notification",
                "agent": self._agent_name or "",
                "status": status,
            },
        }, self._conversation_id)

        # Two SSE events per notification, same post-write sequence:
        #   1. new_message — renders the bell row in the transcript (with
        #      source.name == 'notification', messages.js picks the right
        #      render branch).
        #   2. notification — triggers the *transient* attention signals
        #      (bell sound, toast, browser notification, tab flash). No
        #      DOM message payload here; pure side-channel.
        new_message_evt = {
            "type": "new_message",
            "cid": self._conversation_id,
            "data": {
                "role": "user",
                "content": message,
                "msg_id": msg_id,
                "source": stamped["source"],
            },
        }
        notification_evt = {
            "type": "notification",
            "cid": self._conversation_id,
            "data": {
                "msg_id": msg_id,
                "content": message,
                "agent": self._agent_name or "",
                "status": status,
                "ts": time.time(),
            },
        }

        writer = ConversationWriter.for_conversation(self._conversation_id)
        writer.enqueue_message(
            stamped,
            agent_name=self._agent_name or "",
            user_id=self._user_id or "",
            sse_events=[new_message_evt, notification_evt],
        )

        logger.info(
            "[push_notif] conv=%s agent=%s status=%s chars=%d",
            self._conversation_id[:8], self._agent_name or "?",
            status, len(message))
        return f"Notification delivered ({len(message)} chars)."


__all__ = ["PushNotificationHandler"]
