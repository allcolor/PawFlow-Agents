"""Read conversation history tool — allows the LLM to search/browse old messages."""
import json
import logging
import re
from typing import Any, Dict

from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)


class ReadHistoryHandler(ToolHandler):
    """Read conversation history on demand.

    The LLM's context is compacted after each response. This tool lets it
    access the full uncompacted history when needed — like reading a file
    instead of keeping everything in memory.
    """

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "read_history"

    @property
    def description(self) -> str:
        return (
            "Read conversation history (messages outside your current context). "
            "Use to recall earlier messages, search for past decisions, or "
            "review what was discussed. The context is compacted after each "
            "response — use this tool to access older messages."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["recent", "search", "read", "count"],
                    "description": (
                        "recent: last N messages (default). "
                        "search: find messages matching a query. "
                        "read: read a specific message by index. "
                        "count: total message count."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip N most recent messages (for recent action, default 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return (default 10)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action). Case-insensitive text match.",
                },
                "index": {
                    "type": "integer",
                    "description": "Message index to read (for read action, 0-based from start)",
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._conversation_id:
            return "Error: no conversation context"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        action = arguments.get("action", "recent")

        if action == "count":
            count = store.message_count(self._conversation_id)
            return f"Total messages in history: {count}"

        if action == "read":
            idx = int(arguments.get("index", 0))
            all_msgs = store.load(self._conversation_id, user_id=self._user_id)
            if not all_msgs:
                return "No history found"
            if idx < 0 or idx >= len(all_msgs):
                return f"Error: index {idx} out of range (0-{len(all_msgs) - 1})"
            msg = all_msgs[idx]
            return self._format_message(msg, idx)

        if action == "search":
            query = arguments.get("query", "")
            if not query:
                return "Error: search requires a query"
            all_msgs = store.load(self._conversation_id, user_id=self._user_id)
            if not all_msgs:
                return "No history found"
            results = []
            query_lower = query.lower()
            for i, msg in enumerate(all_msgs):
                content = self._get_content(msg)
                if query_lower in content.lower():
                    results.append(self._format_message(msg, i, preview=True))
                    if len(results) >= 20:
                        break
            if not results:
                return f"No messages matching '{query}'"
            return f"Found {len(results)} match(es):\n\n" + "\n\n".join(results)

        # Default: recent
        limit = int(arguments.get("limit", 10))
        offset = int(arguments.get("offset", 0))
        page = store.load_page(
            self._conversation_id, limit=limit, offset=offset,
            user_id=self._user_id)
        if not page:
            return "No history found"
        msgs = page.get("messages", [])
        total = page.get("total_count", 0)
        lines = []
        start_idx = total - offset - len(msgs)
        for i, msg in enumerate(msgs):
            lines.append(self._format_message(msg, start_idx + i))
        header = f"Messages {start_idx}-{start_idx + len(msgs) - 1} of {total}"
        if page.get("has_more"):
            header += f" (use offset={offset + limit} for older)"
        return header + "\n\n" + "\n\n".join(lines)

    @staticmethod
    def _get_content(msg) -> str:
        if isinstance(msg, dict):
            c = msg.get("content", "")
        else:
            c = getattr(msg, "content", "")
        return c if isinstance(c, str) else str(c)

    @staticmethod
    def _format_message(msg, index: int, preview: bool = False) -> str:
        role = msg.get("role", "?") if isinstance(msg, dict) else getattr(msg, "role", "?")
        content = ReadHistoryHandler._get_content(msg)
        source = msg.get("source", {}) if isinstance(msg, dict) else getattr(msg, "source", {})
        agent = ""
        if isinstance(source, dict):
            agent = source.get("name", "")

        header = f"[#{index}] {role}"
        if agent:
            header += f" ({agent})"

        if preview:
            # Search result: first 200 chars
            return f"{header}: {content[:200]}{'...' if len(content) > 200 else ''}"
        else:
            # Full message (cap at 2000 to avoid huge results)
            if len(content) > 2000:
                return f"{header}:\n{content[:2000]}\n... ({len(content)} chars total)"
            return f"{header}:\n{content}"
