"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class RememberHandler(ToolHandler):
    """Store a fact in persistent long-term memory.

    The agent uses this to remember user preferences, important facts,
    or anything that should survive across conversations.
    """

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""
        self._embed_fn = None

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact or piece of information in persistent long-term memory.\n"
            "Use this to remember user preferences, important facts, project context, "
            "or anything that should survive across conversations.\n\n"
            "Key parameters:\n"
            "- text (required): The fact to store. Keep it concise and self-contained.\n"
            "- category: Organizes memories by type. Use 'facts' for objective info (names, "
            "dates, technical details), 'events' for things that happened, 'discoveries' for "
            "new findings, 'preferences' for user likes/dislikes/settings, 'advice' for "
            "guidance or rules the user wants enforced.\n"
            "- scope: Controls visibility across agents and conversations.\n"
            "  'agent' (default) — visible in all conversations but only to this agent.\n"
            "  'global' — visible to ALL agents in ALL conversations.\n"
            "  'conversation' — visible to all agents but only in THIS conversation.\n"
            "  'private' — visible only to this agent in this conversation.\n"
            "- tags: Free-form labels for retrieval. Use consistent tags like 'preference', "
            "'project:name', 'person:name'. Recalled memories can be filtered by tags.\n"
            "- valid_from: Epoch timestamp for temporal facts (e.g., 'user started job on X'). "
            "Enables as_of queries in recall to see what was true at a given time.\n\n"
            "Before storing, consider using check_duplicate to avoid redundant entries. "
            "For structured relationships between entities, prefer kg_add instead. "
            "Memories are auto-embedded for semantic_recall if an embedding provider is configured."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The fact or information to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization and retrieval (e.g. 'preference', 'name', 'project')",
                },
                "scope": {
                    "type": "string",
                    "enum": ["conversation", "agent", "global", "private"],
                    "description": "Where to store: conversation (this conv, all agents), agent (all convs, this agent), global (everywhere), private (this agent + this conv only). Default: agent.",
                },
                "category": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Memory type category",
                },
                "valid_from": {
                    "type": "number",
                    "description": "Epoch timestamp when this fact became valid (0 = since creation)",
                },
            },
            "required": ["text"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_embed_fn(self, fn):
        """Set embedding function for auto-embedding memories."""
        self._embed_fn = fn

    @staticmethod
    def _sanitize_memory(text: str) -> str:
        """Flag injection attempts in memory content.

        Memories are recalled in future conversations and injected into
        the system prompt. A poisoned memory could hijack future sessions.
        """
        _INJ = re.compile(
            r'(?i)'
            r'(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions'
            r'|you\s+are\s+now\s+(?:a|an|the)\s+'
            r'|(?:^|\n)\s*system\s*:\s+'
            r'|new\s+instructions?\s*:'
            r'|override\s+(?:all\s+)?(?:previous|system)\s+'
        )
        return _INJ.sub(lambda m: f"[⚠ FLAGGED: {m.group()[:40]}]", text)

    def execute(self, arguments: Dict[str, Any]) -> str:
        text = arguments.get("text", "")
        if not text:
            return "Error: text is required"
        text = self._sanitize_memory(text)
        from core.handlers._arg_normalize import normalize_string_list
        tags = normalize_string_list(arguments.get("tags"))
        scope = arguments.get("scope", "agent")

        user_id = self._user_id
        # Resolve scope to (agent, conv_id) via the canonical helper
        # so all callers that accept a `scope` string land on the
        # same fields.
        from core.memory_store import resolve_scope, VALID_SCOPES
        if scope not in VALID_SCOPES:
            return f"Error: invalid scope {scope!r} (valid: {', '.join(VALID_SCOPES)})"
        agent, conv_id = resolve_scope(
            scope, self._agent_name or "", self._conversation_id or "")
        try:
            # Auto-embed if embed function is available
            embedding = None
            if self._embed_fn:
                try:
                    embedding = self._embed_fn(text)
                except Exception as emb_err:
                    logger.debug(f"Auto-embed failed: {emb_err}")

            category = arguments.get("category", "")
            valid_from = float(arguments.get("valid_from", 0) or 0)
            from core.memory_store import MemoryStore
            entry = MemoryStore.instance().remember(
                user_id, text, tags, source="agent",
                embedding=embedding, agent=agent,
                conversation_id=conv_id,
                category=category,
                valid_from=valid_from,
            )
            scope_label = scope
            if scope == "private":
                scope_label = f"private:{agent}@{conv_id[:8]}"
            elif scope == "agent" and agent:
                scope_label = f"agent:{agent}"
            elif scope == "conversation":
                scope_label = f"conv:{conv_id[:8]}"
            msg = f"Remembered (id: {entry.id}, tags: {entry.tags}, scope: {scope_label})"

            # Light KG cross-check: warn if contradictions exist
            try:
                from core.knowledge_graph import KnowledgeGraph
                kg = KnowledgeGraph.for_user(user_id)
                if kg.stats()["triples"] > 0:
                    # Check if any entity mentioned in the text has contradictions
                    for ent_data in kg._entities:
                        if ent_data.lower() in text.lower():
                            facts = kg.query_entity(ent_data)
                            active = [f for f in facts if f["current"]]
                            # Group by predicate, check for multi-valued
                            by_pred = {}
                            for f in active:
                                by_pred.setdefault(f["predicate"], []).append(f["object"])
                            conflicts = [
                                f"{ent_data}->{p}: {', '.join(vs)}"
                                for p, vs in by_pred.items() if len(vs) > 1
                            ]
                            if conflicts:
                                msg += f"\n\u26a0 KG conflicts: {'; '.join(conflicts[:3])}"
                            break  # only check first matching entity
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            return msg
        except Exception as e:
            return f"Error storing memory: {e}"


class SemanticRecallHandler(ToolHandler):
    """Search memories by meaning/similarity using vector embeddings."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""
        self._embed_fn = None

    @property
    def name(self) -> str:
        return "semantic_recall"

    @property
    def description(self) -> str:
        return (
            "Search memories by meaning and similarity using vector embeddings (semantic search).\n"
            "This finds memories whose MEANING is close to the query, even if the exact words "
            "differ. For example, querying 'favorite color' would match a memory stored as "
            "'the user prefers blue for UI themes'.\n\n"
            "When to use semantic_recall vs recall:\n"
            "- Use semantic_recall when the user asks about a topic and you don't know the "
            "exact words used when the memory was stored, or when keyword search returned "
            "no results. Best for conceptual/topical queries.\n"
            "- Use recall when you know specific keywords or tags to filter by, or when you "
            "need exact phrase matching. Best for precise lookups.\n\n"
            "Key parameters:\n"
            "- query (required): Natural language description of what you're looking for. "
            "Longer, more descriptive queries produce better results than single words.\n"
            "- limit: Max results (default 5). Increase for broader searches.\n"
            "- category: Filter to a specific memory type (facts/events/discoveries/"
            "preferences/advice) before ranking by similarity.\n\n"
            "Results include a similarity score (0-1, higher = more similar). "
            "Requires an embedding provider to be configured — returns an error if unavailable. "
            "Each result includes the memory ID, which can be used with forget to delete."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query to search by meaning",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 5)",
                },
                "category": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Filter by memory type category",
                },
            },
            "required": ["query"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_embed_fn(self, fn):
        """Set embedding function for query embedding."""
        self._embed_fn = fn

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: query is required"
        limit = int(arguments.get("limit", 5))

        if not self._embed_fn:
            return "Error: semantic search not available (no embedding provider configured)"

        user_id = self._user_id
        try:
            query_embedding = self._embed_fn(query)
            from core.memory_store import MemoryStore
            results = MemoryStore.instance().semantic_recall(
                user_id, query_embedding, limit=limit,
                agent_name=self._agent_name,
                conversation_id=self._conversation_id,
                category=arguments.get("category", ""),
            )
            if not results:
                return "No semantically similar memories found."

            lines = []
            for entry, score in results:
                tag_str = ", ".join(entry.tags) if entry.tags else "none"
                lines.append(f"- [{entry.id}] (score: {score:.3f}, tags: {tag_str}) {entry.text}")
            return f"Found {len(results)} similar memories:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error in semantic recall: {e}"


class RecallHandler(ToolHandler):
    """Retrieve facts from persistent long-term memory."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search persistent memory for previously stored facts, preferences, or context "
            "using keyword matching and tag filtering.\n\n"
            "Use this at the start of conversations to load relevant context, or whenever "
            "the user references something you should already know. Also use it to find "
            "memory IDs before calling forget.\n\n"
            "Key parameters:\n"
            "- query: Text to search for in memory content. Matches by substring/keyword. "
            "Can be omitted if filtering by tags alone.\n"
            "- tags: Filter by one or more tags (e.g. ['preference', 'project:pawflow']). "
            "Only memories that have ALL specified tags are returned.\n"
            "- category: Filter by memory type — 'facts', 'events', 'discoveries', "
            "'preferences', or 'advice'. Narrows results to a specific kind.\n"
            "- as_of: Epoch timestamp for temporal queries. Returns only memories that were "
            "valid at that point in time (using valid_from/valid_to). Use 0 or omit for "
            "'current' memories only.\n\n"
            "Scope visibility rules: You see memories scoped to 'global', your own agent "
            "scope, the current conversation scope, and your private scope. You do NOT see "
            "other agents' agent-scoped or private memories.\n\n"
            "Returns up to 20 results with scope icons: global, agent, conversation, or "
            "private. Each result includes the memory ID for use with forget. "
            "For meaning-based search when keywords don't match, use semantic_recall instead."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in memories",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (e.g. 'preference', 'name')",
                },
                "category": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Filter by memory type category",
                },
                "as_of": {
                    "type": "number",
                    "description": "Epoch timestamp — only return memories valid at this time (0 = now, excludes ended)",
                },
            },
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        from core.handlers._arg_normalize import normalize_string_list
        tags = normalize_string_list(arguments.get("tags")) or None

        user_id = self._user_id
        try:
            from core.memory_store import MemoryStore
            entries = MemoryStore.instance().recall(
                user_id, query=query, tags=tags, limit=20,
                agent_name=self._agent_name,
                conversation_id=self._conversation_id,
                category=arguments.get("category", ""),
                as_of=float(arguments.get("as_of", 0) or 0),
            )
            if not entries:
                return "No memories found matching your query."

            lines = []
            for e in entries:
                tag_str = ", ".join(e.tags) if e.tags else "none"
                scope = "🌐" if not e.agent and not e.conversation_id else (
                    "🔒" if e.agent and e.conversation_id else (
                        "💬" if e.conversation_id else "🤖"))
                lines.append(f"- [{e.id}] {scope} ({tag_str}) {e.text}")
            return f"Found {len(entries)} memories:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error recalling memories: {e}"


class ForgetHandler(ToolHandler):
    """Delete a specific memory entry."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return (
            "Permanently delete a specific memory entry by its ID.\n\n"
            "Use recall or semantic_recall first to find the memory and its ID, "
            "then pass that ID here. This is a hard delete — the memory is removed "
            "from the store and cannot be recovered.\n\n"
            "Key parameters:\n"
            "- memory_id (required): The ID of the memory to delete, as shown in "
            "recall results (e.g., the value in square brackets like [abc123]).\n\n"
            "Use this when the user asks to forget something, when a memory is outdated "
            "and should be replaced (forget old + remember new), or when check_duplicate "
            "reveals redundant entries that should be cleaned up."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to delete (from recall results)",
                },
            },
            "required": ["memory_id"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        memory_id = arguments.get("memory_id", "")
        if not memory_id:
            return "Error: memory_id is required"

        user_id = self._user_id
        try:
            from core.memory_store import MemoryStore
            deleted = MemoryStore.instance().forget(user_id, memory_id)
            return f"Memory {memory_id} deleted." if deleted else f"Memory {memory_id} not found."
        except Exception as e:
            return f"Error deleting memory: {e}"


class CheckDuplicateHandler(ToolHandler):
    """Check for duplicate or similar memories before storing."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "check_duplicate"

    @property
    def description(self) -> str:
        return (
            "Check if a similar memory already exists before storing a new one.\n\n"
            "Call this BEFORE remember to avoid creating duplicate or near-duplicate "
            "entries. Returns up to 5 existing memories that match the given text, "
            "with exact matches clearly flagged.\n\n"
            "Key parameters:\n"
            "- text (required): The text you intend to store. Will be compared against "
            "existing memories using keyword matching.\n"
            "- category: Optionally narrow the duplicate check to a specific category "
            "(facts/events/discoveries/preferences/advice).\n\n"
            "If the result says 'No similar memories found. Safe to store.' — proceed "
            "with remember. If exact or near matches are found, decide whether to skip "
            "storing, update the existing memory (forget + remember), or store anyway "
            "if the new fact adds distinct information."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to check for duplicates"},
                "category": {"type": "string", "description": "Filter by category"},
            },
            "required": ["text"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        text = arguments.get("text", "").strip()
        if not text:
            return "Error: text is required"
        user_id = self._user_id
        if not user_id:
            return "Error: user_id not set"
        try:
            from core.memory_store import MemoryStore
            ms = MemoryStore.instance()
            entries = ms.recall(
                user_id, query=text, limit=5,
                category=arguments.get("category", ""),
            )
            if not entries:
                return "No similar memories found. Safe to store."
            lines = [f"Found {len(entries)} similar memor{'y' if len(entries) == 1 else 'ies'}:"]
            for e in entries:
                _exact = e.text.strip().lower() == text.strip().lower()
                _marker = " [EXACT MATCH]" if _exact else ""
                lines.append(f"  - [{e.id}] {e.text[:100]}{_marker}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
