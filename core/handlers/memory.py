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
            "Store a fact or piece of information in persistent memory. "
            "Use this to remember user preferences, important context, "
            "or anything that should be recalled in future conversations. "
            "By default the memory is scoped to your agent. Set global=true "
            "to make it accessible to all agents."
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
                "wing": {
                    "type": "string",
                    "description": "Project or person scope (e.g. 'project:pawflow', 'person:quentin')",
                },
                "hall": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Memory type category",
                },
                "room": {
                    "type": "string",
                    "description": "Specific topic (e.g. 'auth', 'docker', 'ci-pipeline')",
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
        tags = arguments.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)]
        scope = arguments.get("scope", "agent")

        user_id = self._user_id
        # Resolve scope to agent + conversation_id
        if scope == "global":
            agent, conv_id = "", ""
        elif scope == "conversation":
            agent, conv_id = "", self._conversation_id
        elif scope == "private":
            agent, conv_id = self._agent_name or "", self._conversation_id
        else:  # "agent" (default)
            agent, conv_id = self._agent_name or "", ""
        try:
            # Auto-embed if embed function is available
            embedding = None
            if self._embed_fn:
                try:
                    embedding = self._embed_fn(text)
                except Exception as emb_err:
                    logger.debug(f"Auto-embed failed: {emb_err}")

            wing = arguments.get("wing", "")
            hall = arguments.get("hall", "")
            room = arguments.get("room", "")
            from core.memory_store import MemoryStore
            entry = MemoryStore.instance().remember(
                user_id, text, tags, source="agent",
                embedding=embedding, agent=agent,
                conversation_id=conv_id,
                wing=wing, hall=hall, room=room,
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
                pass

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
            "Search memories by meaning and similarity (semantic search). "
            "Use this when keyword search (recall) doesn't find what you need, "
            "or when the user asks about a topic using different words than stored."
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
                "wing": {
                    "type": "string",
                    "description": "Filter by project/person scope",
                },
                "hall": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Filter by memory type category",
                },
                "room": {
                    "type": "string",
                    "description": "Filter by specific topic",
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
                wing=arguments.get("wing", ""),
                hall=arguments.get("hall", ""),
                room=arguments.get("room", ""),
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
        self._memory_llm_client = None  # LLM for relevance filtering

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search persistent memory for previously stored facts, preferences, "
            "or context. Use this at the start of conversations or when the user "
            "references something you should know."
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
                "wing": {
                    "type": "string",
                    "description": "Filter by project/person scope",
                },
                "hall": {
                    "type": "string",
                    "enum": ["facts", "events", "discoveries", "preferences", "advice"],
                    "description": "Filter by memory type category",
                },
                "room": {
                    "type": "string",
                    "description": "Filter by specific topic",
                },
            },
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_memory_llm_client(self, client):
        """Set an LLM client for memory relevance filtering.

        When set, recall will use this LLM to select the top 5 most relevant
        memories from all matches, instead of returning all of them.
        """
        self._memory_llm_client = client

    def _filter_by_relevance(self, entries, query: str, top_k: int = 5):
        """Use memory_llm_client to select the most relevant memories.

        Sends memory headers (id + text preview) to the LLM with the query,
        asks it to pick the top_k most relevant ones.
        Returns the filtered list, or the original list on failure.
        """
        if not self._memory_llm_client or len(entries) <= top_k:
            return entries

        # Build memory catalog for the LLM
        catalog_lines = []
        for i, e in enumerate(entries):
            tag_str = ", ".join(e.tags) if e.tags else "none"
            preview = e.text[:200] if len(e.text) > 200 else e.text
            catalog_lines.append(f"{i}: [{e.id}] (tags: {tag_str}) {preview}")
        catalog = "\n".join(catalog_lines)

        prompt = (
            f"Given the user's query: \"{query}\"\n\n"
            f"Select the {top_k} most relevant memories from this list. "
            f"Return ONLY the indices (0-based) as a JSON array, e.g. [0, 3, 5, 7, 12].\n\n"
            f"Memories:\n{catalog}"
        )

        try:
            from core.llm_client import LLMMessage
            resp = self._memory_llm_client.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                temperature=0,
                max_tokens=200,
            )
            # Parse the response — expect a JSON array of indices
            import re as _re
            _match = _re.search(r'\[[\d,\s]+\]', resp.content or "")
            if _match:
                indices = json.loads(_match.group())
                selected = []
                for idx in indices[:top_k]:
                    if 0 <= idx < len(entries):
                        selected.append(entries[idx])
                if selected:
                    logger.info("[recall] LLM relevance filter: %d → %d memories",
                               len(entries), len(selected))
                    return selected
        except Exception as e:
            logger.warning("[recall] Memory LLM relevance filtering failed: %s", e)

        # Fallback: return all entries
        return entries

    def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        tags = arguments.get("tags")
        if isinstance(tags, str):
            tags = [tags]

        user_id = self._user_id
        try:
            from core.memory_store import MemoryStore
            entries = MemoryStore.instance().recall(
                user_id, query=query, tags=tags, limit=20,
                agent_name=self._agent_name,
                conversation_id=self._conversation_id,
                wing=arguments.get("wing", ""),
                hall=arguments.get("hall", ""),
                room=arguments.get("room", ""),
            )
            if not entries:
                return "No memories found matching your query."

            # If memory_llm_service is configured, filter by relevance
            if self._memory_llm_client and query:
                entries = self._filter_by_relevance(entries, query, top_k=5)

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
        return "Delete a specific memory by its ID. Use recall first to find the ID."

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
