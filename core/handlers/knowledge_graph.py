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
            "Add a fact as a (subject, predicate, object) triple to the knowledge graph.\n"
            "The KG stores structured relationships between entities — use it for facts that "
            "have a clear subject-relationship-object structure (e.g., 'Quentin -> works_on -> PawFlow').\n\n"
            "Key parameters:\n"
            "- subject (required): The entity the fact is about (e.g., 'Quentin', 'PawFlow', 'Python').\n"
            "- predicate (required): The relationship verb (e.g., 'uses', 'works_on', 'prefers', "
            "'is_a', 'has_feature'). Use consistent predicates for the same relationship type.\n"
            "- object (required): The target entity or value (e.g., 'PostgreSQL', 'dark_mode').\n"
            "- confidence: How certain the fact is.\n"
            "  'EXTRACTED' (default) — directly stated by the user or found in a source.\n"
            "  'INFERRED' — deduced from context but not explicitly stated.\n"
            "  'AMBIGUOUS' — uncertain or potentially conflicting information.\n"
            "- valid_from: ISO date string (e.g., '2026-01') marking when the fact became true. "
            "Enables temporal queries with as_of in kg_query.\n"
            "- source: Where the fact came from (e.g., 'conversation', 'web search', 'code analysis').\n\n"
            "Contradiction detection: If you add a triple that conflicts with an existing active triple "
            "(same subject+predicate, different object), the system warns you. Use kg_invalidate to "
            "expire the old fact first if the new one supersedes it.\n\n"
            "For unstructured text that doesn't fit a triple pattern, use remember instead."
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
            "Query the knowledge graph for all facts about a specific entity.\n"
            "Returns the entity's relationships — both where it appears as subject (outgoing) "
            "and where it appears as object (incoming).\n\n"
            "Key parameters:\n"
            "- entity (required): The entity name to look up (e.g., 'Quentin', 'PawFlow'). "
            "Case-sensitive — use the exact name as stored.\n"
            "- direction: Filter which relationships to return.\n"
            "  'both' (default) — all relationships involving this entity.\n"
            "  'outgoing' — only facts where this entity is the subject (entity -> pred -> ?).\n"
            "  'incoming' — only facts where this entity is the object (? -> pred -> entity).\n"
            "- as_of: ISO date string for temporal queries. Returns only facts that were valid "
            "at the given date. Omit to see all facts (current and expired).\n\n"
            "Results show each triple with its current/expired status and validity dates. "
            "For broader graph exploration across multiple entities, use query_graph instead. "
            "For free-text memory search, use recall or semantic_recall."
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
        return (
            "Mark a knowledge graph triple as expired (no longer true).\n\n"
            "This is a soft delete — the triple remains in the graph with an end date "
            "but is no longer considered 'current'. This preserves history so temporal "
            "queries (as_of) can still find it. Use this when a fact changes over time "
            "(e.g., someone changes jobs, a project switches technologies).\n\n"
            "Key parameters:\n"
            "- subject (required): The subject entity of the triple to invalidate.\n"
            "- predicate (required): The relationship of the triple.\n"
            "- object (required): The object entity of the triple.\n"
            "- ended: ISO date string for when the fact stopped being true. "
            "Defaults to today if omitted.\n\n"
            "Typical workflow: kg_invalidate the old fact, then kg_add the new one. "
            "For example, to update someone's employer: invalidate 'Alice -> works_at -> OldCo' "
            "then add 'Alice -> works_at -> NewCo'."
        )

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
        return (
            "View a chronological timeline of knowledge graph facts, ordered by valid_from date.\n\n"
            "Shows when facts were added and whether they are still current or have expired. "
            "Useful for understanding the history of an entity or seeing how the knowledge "
            "graph evolved over time.\n\n"
            "Key parameters:\n"
            "- entity: Filter the timeline to facts involving this specific entity. "
            "Omit to see the full timeline across all entities.\n"
            "- limit: Max entries to return (default 20). Increase for a fuller history.\n\n"
            "Each entry shows: [date] subject -> predicate -> object (current/expired status). "
            "For a summary overview of the graph, use kg_stats instead. "
            "For exploring connections from a specific entity, use kg_query."
        )

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
        return (
            "Get summary statistics about the knowledge graph.\n\n"
            "Returns a quick overview including: total number of entities, total triples "
            "(broken down into current vs expired), and the list of relationship types "
            "(predicates) used in the graph.\n\n"
            "Takes no parameters. Use this to get a sense of the graph's size and "
            "structure before diving into specific queries. Useful for answering questions "
            "like 'how much do you know about me?' or before deciding whether to use "
            "kg_query or query_graph for exploration."
        )

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
            "Traverse the knowledge graph to find connections related to a natural language question.\n"
            "Unlike kg_query (which looks up a single entity), this tool starts from keyword-matched "
            "seed entities and explores outward through the graph.\n\n"
            "Key parameters:\n"
            "- question (required): Keywords or a question. Entities whose names match any word "
            "in the question become seed nodes for traversal.\n"
            "- mode: Traversal strategy.\n"
            "  'bfs' (default) — Breadth-first search. Explores all neighbors at each depth level "
            "before going deeper. Best for getting broad context around an entity — 'tell me "
            "everything related to X'.\n"
            "  'dfs' — Depth-first search. Follows one path as deep as possible before backtracking. "
            "Best for tracing specific chains of relationships — 'how is X connected to Y?'.\n"
            "- depth: Max traversal hops from seed entities (default 3). Higher values explore "
            "more of the graph but return more results. Keep low (2-3) for focused queries.\n"
            "- max_results: Max triples to return (default 50). Acts as a safety limit.\n\n"
            "Results include confidence levels for each triple. For single-entity lookups, "
            "use kg_query. For the most connected entities, use kg_god_nodes."
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
        return (
            "Return the most connected entities (god nodes) in the knowledge graph, "
            "ranked by total degree (number of incoming + outgoing relationships).\n\n"
            "God nodes are the central hubs of the knowledge graph — entities that appear "
            "in the most triples. These are good starting points for exploration and help "
            "identify the most important topics/people/concepts stored.\n\n"
            "Key parameters:\n"
            "- limit: Max entities to return (default 10).\n\n"
            "Each result shows the entity name and its connection count. "
            "Use this to answer questions like 'what are the main topics you know about?' "
            "or to find seed entities for deeper query_graph traversals."
        )

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


