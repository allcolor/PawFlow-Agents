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

import logging
import threading
import time
from typing import Any, Dict

from core.agent_inbox import DEFAULT_READ_CHARS as _DEFAULT_CHARS
from core.agent_inbox import MAX_READ_CHARS as _MAX_CHARS
from core.agent_inbox import MIN_READ_CHARS as _MIN_CHARS
from core.handlers.spawn_agents import SpawnAgentsHandler

logger = logging.getLogger(__name__)

# A turn that already found its inbox empty and keeps calling read_inbox is
# spinning: the ACTION REQUIRED notice stays in the turn's context, so the
# model re-obeying it every iteration is exactly the loop. The first empty
# answer explains and asks the turn to end; reads that keep coming back
# fast (~seconds apart, the LLM iteration cadence) are hard errors, and two
# rapid rejections in a row force-stop the offending run — kill, not error,
# per the platform rule that a force stop never affects the next run.
_EMPTY_CALL_COOLDOWN_SECONDS = 180.0
_RAPID_RECALL_SECONDS = 30.0
_RAPID_RECALL_LIMIT = 2

_EMPTY_READ_STATE: Dict[tuple, Dict[str, float]] = {}
_EMPTY_READ_STATS = {"blocked": 0, "force_stopped": 0}
_EMPTY_READ_LOCK = threading.Lock()


def _register_empty_read(conversation_id: str,
                         agent_name: str) -> tuple:
    """Record an empty read; return (blocked, retry_after, rapid_count).

    The window anchor is set once and never refreshed while reads keep
    arriving, so the printed countdown actually counts down. Rapid recall
    (less than _RAPID_RECALL_SECONDS since the previous empty read)
    accumulates toward the force-stop limit; slow re-reads do not.
    """
    key = (conversation_id, agent_name)
    now = time.monotonic()
    with _EMPTY_READ_LOCK:
        state = _EMPTY_READ_STATE.setdefault(key, {})
        anchor = state.get("anchor", 0.0)
        if anchor and now - anchor < _EMPTY_CALL_COOLDOWN_SECONDS:
            gap = now - state.get("last", 0.0)
            rapid = state.get("rapid", 0) + 1 if gap < _RAPID_RECALL_SECONDS \
                else 0
            state["rapid"] = rapid
            state["last"] = now
            _EMPTY_READ_STATS["blocked"] += 1
            remaining = max(1, int(_EMPTY_CALL_COOLDOWN_SECONDS
                                   - (now - anchor)))
            return (True, remaining, rapid)
        state.clear()
        state["anchor"] = now
        state["last"] = now
        state["rapid"] = 0
        return (False, 0, 0)


def _reset_empty_read_window(conversation_id: str, agent_name: str) -> None:
    """Forget the empty-read window (test helper / item arrival clears it)."""
    with _EMPTY_READ_LOCK:
        _EMPTY_READ_STATE.pop((conversation_id, agent_name), None)


def _blocked_message(retry_after: int, stopped: bool = False) -> str:
    text = (
        f"Error: your inbox is empty and you already read it moments ago "
        f"(retry in ~{retry_after}s). Do not call read_inbox again in this "
        f'turn: end it instead. A new "[PawFlow inbox: ACTION REQUIRED]" '
        f"notice will wake you — no notice means nothing to read."
    )
    if stopped:
        text += " This turn is being force-stopped: no further calls."
    return text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _force_stop_read_loop(conversation_id: str, agent_name: str) -> None:
    """Kill the run whose turn keeps polling an empty inbox.

    Called from a tool handler thread: the cancel propagates in the
    background (cancel_provider_client + TaskGroup cancel), so the read
    returns its error immediately. Bumping the fence first guarantees the
    stopped run cannot act again; the next run clears it at start, so this
    never affects the successor.
    """
    def _stop():
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.force_stop_agent(conversation_id, agent_name)
        except Exception:
            logger.debug(
                "read_inbox force-stop failed", exc_info=True)
    threading.Thread(target=_stop, daemon=True).start()


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
            # A paged read (item=...) must keep its specific answer: it is a
            # continuation of a large item, not an incremental poll.
            if not item:
                return self._empty_answer(conversation_id, src_agent)
            return "No unread inbox item %s." % item
        # Items arrived since the last empty read: clear the loop window.
        _reset_empty_read_window(conversation_id, src_agent)
        return agent_inbox.read_inbox(
            conversation_id, src_agent,
            max_chars=max_chars, item=item, offset=offset)

    def _empty_answer(self, conversation_id: str, agent_name: str) -> str:
        """The empty-inbox answer, escalating to a force stop.

        The first empty read explains and asks the turn to end; reads that
        keep coming back inside the cooldown window are hard errors with a
        real countdown, and enough rapid rejections force-stop the offending
        run — a kill, which never affects the agent's next run.
        """
        blocked, retry_after, rapid = _register_empty_read(
            conversation_id, agent_name)
        if not blocked:
            return (
                "No unread items. Your inbox is empty: do NOT call "
                "read_inbox again until a new [PawFlow inbox: ACTION "
                "REQUIRED] notice appears. If you have nothing else to act "
                "on, end your turn without scheduling another wake-up."
            )
        if rapid >= _RAPID_RECALL_LIMIT:
            _EMPTY_READ_STATS["force_stopped"] += 1
            _force_stop_read_loop(conversation_id, agent_name)
            logger.warning(
                "[read_inbox] force-stopped empty-read spin %s/%s after "
                "%d rapid rejected reads", conversation_id[:8], agent_name,
                rapid)
            return _blocked_message(retry_after, stopped=True)
        return _blocked_message(retry_after)
