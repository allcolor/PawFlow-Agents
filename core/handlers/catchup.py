"""Optional public-agent catchup, separate from mandatory inbox delivery."""

from core.agent_catchup import DEFAULT_READ_CHARS, MIN_READ_CHARS, MAX_READ_CHARS
from core.handlers.spawn_agents import SpawnAgentsHandler


class CatchUpHandler(SpawnAgentsHandler):
    """Read one public author on demand with a cursor owned by the caller."""

    @property
    def name(self):
        return "catchUp"

    @property
    def description(self):
        return (
            "Optionally read one agent's public replies in this conversation. "
            "No automatic catchup or notification is sent. Each caller/source "
            "pair has its own durable cursor, initially at the beginning. "
            "Returns JSON with messages and has_more; call catchUp(agent=...) "
            "again whenever you want the next page. Oversized replies resume "
            "automatically. Excludes private delegates, tasks, thinking and tools. "
            "Use read_history for general history searches."
        )

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {
            "agent": {"type": "string", "minLength": 1,
                      "description": "Author whose public replies you choose to read."},
            "max_chars": {"type": "integer", "minimum": MIN_READ_CHARS,
                          "maximum": MAX_READ_CHARS, "default": DEFAULT_READ_CHARS,
                          "description": "Total character budget including JSON metadata."},
        }, "required": ["agent"]}

    def execute(self, arguments):
        from core.agent_catchup import read_catchup

        if not isinstance(arguments, dict):
            return "Error: catchUp arguments must be an object."
        agent = arguments.get("agent")
        if not isinstance(agent, str) or not agent.strip():
            return "Error: catchUp agent is required and must be a nonempty string."
        reader, _service = self._resolve_source_context()
        if not self._conversation_id or not reader:
            return "Error: catchUp requires a conversation and calling agent identity."
        try:
            return read_catchup(self._conversation_id, reader, agent.strip(),
                                max_chars=arguments.get("max_chars", DEFAULT_READ_CHARS))
        except ValueError as exc:
            return f"Error: {exc}"
