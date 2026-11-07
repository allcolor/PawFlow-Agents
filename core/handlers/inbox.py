"""read_inbox -- the agent's pull of its non-user messages.

Every message an agent receives that is not from the user (delegate
messages and results, background tool results, scheduled wake-ups,
catch-up of other participants, skill events) is stored as an inbox item
(core/agent_inbox.py). The agent only gets an imperative notice and reads
the content through this tool; the read marks the items read and is the
server-side acknowledgement. See docs/inbox_delivery.md.

The handler derives from SpawnAgentsHandler, like delegate_status, so the
relay's source-agent injection (services/_tool_relay_execute.py) and the
registry wiring identify the calling agent the same way.
"""

from typing import Any, Dict

from core.agent_inbox import DEFAULT_READ_CHARS as _DEFAULT_CHARS
from core.agent_inbox import MAX_READ_CHARS as _MAX_CHARS
from core.agent_inbox import MIN_READ_CHARS as _MIN_CHARS
from core.handlers.spawn_agents import SpawnAgentsHandler


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class ReadInboxHandler(SpawnAgentsHandler):
    """Return the caller's unread inbox items and mark them read."""

    @property
    def name(self) -> str:
        return "read_inbox"

    @property
    def description(self) -> str:
        return (
            "Read your PawFlow inbox: every message for you that is not from "
            "the user (delegate messages and results, background tool "
            "results, scheduled wake-ups, catch-up of other participants, "
            "skill events). Call it IMMEDIATELY whenever a [PawFlow inbox: "
            "ACTION REQUIRED] notice appears, before any other tool call or "
            "answer. Returns unread items oldest first and marks them read. "
            "A large item is paged: call read_inbox(item=..., offset=...) as "
            "its page says."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_chars": {
                    "type": "integer",
                    "minimum": _MIN_CHARS,
                    "maximum": _MAX_CHARS,
                    "default": _DEFAULT_CHARS,
                    "description": "Character budget of this read "
                                   "(2000..100000, default 40000).",
                },
                "item": {
                    "type": "string",
                    "description": "msg_id of one item to continue, as "
                                   "named by its page.",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Character offset to continue `item` "
                                   "from (only with item).",
                },
            },
            "required": [],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core import agent_inbox

        if not isinstance(arguments, dict):
            return "Error: read_inbox arguments must be an object."
        max_chars = arguments.get("max_chars", _DEFAULT_CHARS)
        if not _is_int(max_chars) or not _MIN_CHARS <= max_chars <= _MAX_CHARS:
            return (f"Error: read_inbox max_chars must be an integer between "
                    f"{_MIN_CHARS} and {_MAX_CHARS}.")
        item = arguments.get("item", "")
        if not isinstance(item, str):
            return "Error: read_inbox item must be a string msg_id."
        item = item.strip()
        offset = arguments.get("offset", 0)
        if not _is_int(offset) or offset < 0:
            return "Error: read_inbox offset must be an integer >= 0."
        if offset and not item:
            return "Error: read_inbox offset requires item."

        conversation_id = str(self._conversation_id or "").strip()
        src_agent, _src_svc = self._resolve_source_context()
        if not conversation_id or not src_agent:
            return (
                "Error: read_inbox could not determine the calling agent:"
                " no conversation id or no agent identity (thread-local"
                " source context or configured agent instance name)."
            )
        return agent_inbox.read_inbox(
            conversation_id, src_agent,
            max_chars=max_chars, item=item, offset=offset)
