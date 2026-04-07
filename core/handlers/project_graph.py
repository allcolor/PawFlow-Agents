"""Project graph handlers — build and query code structure graphs."""

import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class ProjectGraphHandler(ToolHandler):
    """Build and query the structural graph of a project codebase."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    def set_agent_name(self, name: str):
        self._agent_name = name

    @property
    def name(self) -> str:
        return "project_graph"

    @property
    def description(self) -> str:
        return (
            "Build or query the structural code graph of the current project. "
            "Actions: build (index codebase), query (traverse graph), "
            "report (summary), node (entity details)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["build", "query", "report", "node"],
                    "description": (
                        "build: index the project codebase into a graph; "
                        "query: traverse graph with a question; "
                        "report: get graph summary (god nodes, stats); "
                        "node: get details about a specific entity"
                    ),
                },
                "path": {"type": "string", "description": "Project root path (for build)"},
                "question": {"type": "string", "description": "Query text (for query/node)"},
                "depth": {"type": "integer", "description": "Traversal depth (default: 3)"},
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not self._user_id or not self._conversation_id:
            return "Error: user_id and conversation_id required"

        from core.project_graph import ProjectGraph
        pg = ProjectGraph.for_conversation(self._user_id, self._conversation_id)

        if action == "build":
            path = arguments.get("path", ".")
            result = pg.build_from_directory(path)
            status = result.get("status", "?")
            if status in ("built", "built_fallback"):
                return (f"Project graph built: {result.get('nodes', 0)} nodes, "
                        f"{result.get('edges', 0)} edges"
                        + (" (fallback mode)" if status == "built_fallback" else ""))
            return f"Graph build: {status} — {result.get('reason', '')}"

        if action == "query":
            question = arguments.get("question", "")
            if not question:
                return "Error: question required for query"
            if not pg.has_graph():
                return "No project graph built yet. Use action='build' first."
            results = pg.query(question, depth=int(arguments.get("depth", 3) or 3))
            if not results:
                return f"No connections found for: {question}"
            lines = [f"Project graph query '{question}' ({len(results)} edges):"]
            for e in results:
                lines.append(f"  [{e.get('confidence', '?')}] {e['source']} → {e['relation']} → {e['target']}")
            return "\n".join(lines)

        if action == "report":
            if not pg.has_graph():
                return "No project graph built yet."
            return pg.get_report()

        if action == "node":
            label = arguments.get("question", "") or arguments.get("path", "")
            if not label:
                return "Error: provide node label in 'question' param"
            node = pg.get_node(label)
            if not node:
                return f"Node '{label}' not found."
            lines = [f"Node: {node['label']}"]
            lines.append(f"  File: {node.get('source_file', '?')} @ {node.get('source_location', '?')}")
            lines.append(f"  Type: {node.get('file_type', '?')}")
            lines.append(f"  Neighbors: {node.get('neighbors', 0)}")
            for e in node.get("neighbor_edges", [])[:10]:
                lines.append(f"    → {e['relation']} → {e['target'] if e['source'] == node['id'] else e['source']}")
            return "\n".join(lines)

        return f"Unknown action: {action}"
