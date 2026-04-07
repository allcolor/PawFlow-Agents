"""Knowledge Graph tool handlers.

5 handlers for managing temporal entity-relationship triples.
All require set_user_id() before execution.
"""

import json
import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class _KgBaseHandler(ToolHandler):
    """Base for KG handlers — provides set_user_id/set_conversation_id/set_agent_name."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def _get_kg(self):
        from core.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph.for_user(self._user_id)


class KgAddHandler(_KgBaseHandler):
    """Add a fact triple to the knowledge graph."""

    @property
    def name(self) -> str:
        return "kg_add"

    @property
    def description(self) -> str:
        return (
            "Add a fact as a (subject, predicate, object) triple to the knowledge graph. "
            "Returns the triple ID and warns if a contradiction is detected."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "The entity the fact is about (e.g. 'PawFlow', 'Quentin')",
                },
                "predicate": {
                    "type": "string",
                    "description": "The relationship (e.g. 'uses', 'works_on', 'prefers')",
                },
                "object": {
                    "type": "string",
                    "description": "The value or target entity (e.g. 'PostgreSQL', 'dark_mode')",
                },
                "valid_from": {
                    "type": "string",
                    "description": "When this fact became true (ISO date, e.g. '2026-01'). Optional.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["EXTRACTED", "INFERRED", "AMBIGUOUS"],
                    "description": "How confident: EXTRACTED (stated), INFERRED (deduced), AMBIGUOUS (uncertain)",
                },
                "source": {
                    "type": "string",
                    "description": "Where this fact came from (conversation, observation, etc.)",
                },
            },
            "required": ["subject", "predicate", "object"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        try:
            kg = self._get_kg()
            result = kg.add_triple(
                subject=arguments["subject"],
                predicate=arguments["predicate"],
                obj=arguments["object"],
                valid_from=arguments.get("valid_from", ""),
                confidence=arguments.get("confidence", "EXTRACTED"),
                source=arguments.get("source", ""),
            )
            status = result.get("status", "?")
            tid = result.get("triple_id", "?")
            msg = f"{status}: {arguments['subject']} -> {arguments['predicate']} -> {arguments['object']} (id: {tid})"
            contradictions = result.get("contradictions", [])
            if contradictions:
                msg += f"\n\u26a0 Contradicts active values: {', '.join(contradictions)}"
            return msg
        except Exception as e:
            return f"Error adding triple: {e}"


class KgQueryHandler(_KgBaseHandler):
    """Query facts about an entity."""

    @property
    def name(self) -> str:
        return "kg_query"

    @property
    def description(self) -> str:
        return (
            "Query the knowledge graph for all facts about an entity. "
            "Returns outgoing and/or incoming relationships."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity name to query",
                },
                "as_of": {
                    "type": "string",
                    "description": "Only return facts valid at this date (ISO format). Optional.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming", "both"],
                    "description": "Which relationships to return (default: both)",
                },
            },
            "required": ["entity"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        try:
            kg = self._get_kg()
            facts = kg.query_entity(
                entity=arguments["entity"],
                as_of=arguments.get("as_of", ""),
                direction=arguments.get("direction", "both"),
            )
            if not facts:
                return f"No facts found about '{arguments['entity']}'."
            lines = []
            for f in facts:
                status = "\u2713" if f["current"] else f"\u2717 (ended {f['valid_to']})"
                vf = f" [from {f['valid_from']}]" if f["valid_from"] else ""
                lines.append(
                    f"- {f['subject']} -> {f['predicate']} -> {f['object']} "
                    f"{status}{vf}"
                )
            return f"Facts about '{arguments['entity']}' ({len(facts)}):\n" + "\n".join(lines)
        except Exception as e:
            return f"Error querying entity: {e}"


class KgInvalidateHandler(_KgBaseHandler):
    """Mark a fact as no longer valid."""

    @property
    def name(self) -> str:
        return "kg_invalidate"

    @property
    def description(self) -> str:
        return "Mark a knowledge graph fact as expired/no longer true."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Subject entity"},
                "predicate": {"type": "string", "description": "Relationship"},
                "object": {"type": "string", "description": "Object entity"},
                "ended": {
                    "type": "string",
                    "description": "When the fact stopped being true (ISO date). Defaults to today.",
                },
            },
            "required": ["subject", "predicate", "object"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        try:
            kg = self._get_kg()
            count = kg.invalidate(
                subject=arguments["subject"],
                predicate=arguments["predicate"],
                obj=arguments["object"],
                ended=arguments.get("ended", ""),
            )
            if count:
                return f"Invalidated {count} triple(s): {arguments['subject']} -> {arguments['predicate']} -> {arguments['object']}"
            return "No matching active triple found to invalidate."
        except Exception as e:
            return f"Error invalidating triple: {e}"


class KgTimelineHandler(_KgBaseHandler):
    """View chronological history of facts."""

    @property
    def name(self) -> str:
        return "kg_timeline"

    @property
    def description(self) -> str:
        return "View a chronological timeline of knowledge graph facts, optionally filtered by entity."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Filter timeline to this entity. Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default: 20)",
                },
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        try:
            kg = self._get_kg()
            entries = kg.timeline(
                entity=arguments.get("entity", ""),
                limit=int(arguments.get("limit", 20)),
            )
            if not entries:
                return "No facts in the knowledge graph yet."
            lines = []
            for t in entries:
                status = "\u2713" if t["current"] else f"\u2717 ended {t['valid_to']}"
                vf = t["valid_from"] or "?"
                lines.append(
                    f"- [{vf}] {t['subject']} -> {t['predicate']} -> {t['object']} ({status})"
                )
            return f"Timeline ({len(entries)} entries):\n" + "\n".join(lines)
        except Exception as e:
            return f"Error getting timeline: {e}"


class KgStatsHandler(_KgBaseHandler):
    """Get knowledge graph statistics."""

    @property
    def name(self) -> str:
        return "kg_stats"

    @property
    def description(self) -> str:
        return "Get summary statistics about the knowledge graph (entities, triples, relationship types)."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"
        try:
            kg = self._get_kg()
            s = kg.stats()
            return (
                f"Knowledge Graph Stats:\n"
                f"- Entities: {s['entities']}\n"
                f"- Triples: {s['triples']} ({s['current_facts']} current, {s['expired_facts']} expired)\n"
                f"- Relationship types: {', '.join(s['relationship_types']) or 'none'}"
            )
        except Exception as e:
            return f"Error getting stats: {e}"


class QueryGraphHandler(_KgBaseHandler):
    """Traverse the knowledge graph with a question."""

    @property
    def name(self) -> str:
        return "query_graph"

    @property
    def description(self) -> str:
        return (
            "Traverse the knowledge graph to find connections related to a question. "
            "BFS = broad context around matching entities. "
            "DFS = trace a specific path through the graph."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question or keywords to search"},
                "mode": {
                    "type": "string", "enum": ["bfs", "dfs"],
                    "description": "bfs (broad) or dfs (deep path). Default: bfs",
                },
                "depth": {"type": "integer", "description": "Max traversal depth (default: 3)"},
                "max_results": {"type": "integer", "description": "Max triples to return (default: 50)"},
            },
            "required": ["question"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        question = arguments.get("question", "")
        if not question:
            return "Error: question is required"
        kg = self._get_kg()
        results = kg.query_graph(
            question,
            mode=arguments.get("mode", "bfs"),
            depth=int(arguments.get("depth", 3) or 3),
            max_results=int(arguments.get("max_results", 50) or 50),
        )
        if not results:
            return f"No connections found for: {question}"
        lines = [f"Graph traversal for '{question}' ({len(results)} connections):"]
        for r in results:
            conf = r.get("confidence", "EXTRACTED")
            lines.append(f"  [{conf}] {r['subject']} → {r['predicate']} → {r['object']}")
        return "\n".join(lines)


class KgGodNodesHandler(_KgBaseHandler):
    """Return the most connected entities in the knowledge graph."""

    @property
    def name(self) -> str:
        return "kg_god_nodes"

    @property
    def description(self) -> str:
        return "Return the most connected entities in the knowledge graph."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entities (default: 10)"},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        kg = self._get_kg()
        nodes = kg.god_nodes(limit=int(arguments.get("limit", 10) or 10))
        if not nodes:
            return "No entities in the knowledge graph."
        lines = ["Most connected entities:"]
        for n in nodes:
            lines.append(f"  {n['entity']} ({n['connections']} connections)")
        return "\n".join(lines)


