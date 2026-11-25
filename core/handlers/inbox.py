"""read_inbox -- the agent's pull of its non-user messages.

Every message an agent receives that is not from the user (delegate
messages and results, background tool results, scheduled wake-ups,
skill events) is stored as an inbox item
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

# A turn that already found its inbox empty and keeps calling read_inbox is
# spinning (Monitor schedules feeding it a wake every few minutes for an
# hour). The first empty answer explains and asks the turn to end; if the
# same agent keeps polling within this window, the reads fail instead of
# feeding the loop.
_EMPTY_CALL_COOLDOWN_SECONDS = 180.0

_LAST_EMPTY_READ: Dict[tuple, float] = {}
_LAST_EMPTY_READ_STATS = {"blocked": 0}


def _register_empty_read(conversation_id: str, agent_name: str) -> float:
    """Record an empty read; return (now - last empty read) for that agent."""
    import time

    key = (conversation_id, agent_name)
    now = time.monotonic()
    previous = _LAST_EMPTY_READ.get(key, 0.0)
    _LAST_EMPTY_READ[key] = now
    return now - previous if previous else float("inf")


def _reset_empty_read_window(conversation_id: str, agent_name: str) -> None:
    """Forget the empty-read window (test helper / item arrival clears it)."""
    _LAST_EMPTY_READ.pop((conversation_id, agent_name), None)


def _blocked_message(retry_after: int) -> str:
    return (
        f"Error: your inbox is empty and you already read it moments ago "
        f"(retry in ~{retry_after}s). Do not call read_inbox again in this "
        f'turn: end it instead. A new "[PawFlow inbox: ACTION REQUIRED]" '
        f"notice will wake you — no notice means nothing to read."
    )


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
            "results, scheduled wake-ups, "
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
        # A published-MCP external owner ids the CLIENT
        # (published_mcp_<hash>), not the conversation agent this inbox
        # belongs to. read_inbox must always open the agent's box — that
        # agent is pinned on the publication itself.
        if src_agent.startswith("published_mcp_"):
            src_agent = self._agent_name or src_agent
        if not conversation_id or not src_agent:
            return (
                "Error: read_inbox could not determine the calling agent:"
                " no conversation id or no agent identity (thread-local"
                " source context or configured agent instance name)."
            )
        from core import agent_inbox as _inbox_mod

        if _inbox_mod.unread_count(conversation_id, src_agent) == 0:
            return self._empty_answer(conversation_id, src_agent)
        # Items arrived since the last empty read: clear the loop window.
        _reset_empty_read_window(conversation_id, src_agent)
        return agent_inbox.read_inbox(
            conversation_id, src_agent,
            max_chars=max_chars, item=item, offset=offset)

    def _empty_answer(self, conversation_id: str, agent_name: str) -> str:
        """The empty-inbox answer, or a blocking error inside the cooldown.

        The first empty read explains and asks the turn to end; repeats
        within the cooldown window are hard errors so a spinning turn
        cannot keep burning its context on an immutable answer.
        """
        gap = _register_empty_read(conversation_id, agent_name)
        if gap < _EMPTY_CALL_COOLDOWN_SECONDS:
            _LAST_EMPTY_READ_STATS["blocked"] += 1
            return _blocked_message(
                int(_EMPTY_CALL_COOLDOWN_SECONDS - gap))
        return (
            "No unread items. Your inbox is empty: do NOT call read_inbox "
            "again until a new [PawFlow inbox: ACTION REQUIRED] notice "
            "appears. If you have nothing else to act on, end your turn "
            "without scheduling another wake-up."
        )
