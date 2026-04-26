"""Project graph handlers — build and query code structure graphs.

Build action fetches files via the relay (user's machine), runs AST
extraction server-side. Query/report/node actions work on the stored graph.
"""

import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)


class ProjectGraphHandler(BaseFsHandler):
    """Build and query the structural graph of a project codebase.

    Extends BaseFsHandler to get relay/filesystem service access.
    Build fetches code via relay, AST parsing runs server-side.
    """

    def __init__(self):
        super().__init__()
        self._agent_name = ""

    def set_agent_name(self, name: str):
        self._agent_name = name

    @property
    def name(self) -> str:
        return "project_graph"

    @property
    def description(self) -> str:
        return (
            "Structural code graph of the current project codebase.\n"
            "Nodes = code entities (functions, classes, modules, imports), "
            "edges = relationships (calls, imports, contains, inherits) extracted "
            "via tree-sitter AST. 17 languages: Python, JS/TS/TSX, Go, Rust, Java, "
            "C/C++, Ruby, C#, Kotlin, Scala, PHP, Swift, Lua, Zig, PowerShell, Elixir.\n\n"
            "WHEN TO USE:\n"
            " - Onboarding to a new repo: `report` shows god nodes (most-connected "
            "entities) so you find the architectural backbone in one call instead "
            "of grep'ing across the tree.\n"
            " - Refactor impact analysis: `query` on a function/class returns its "
            "call sites and neighbours via AST resolution — more reliable than "
            "grep, which matches comments/strings/typos.\n"
            " - 'Where does concept X live': `query` on a keyword returns nodes "
            "touching that concept across the repo.\n"
            " - Cross-cutting concerns: `kg_god_nodes`-style overview of which "
            "entities are highly coupled.\n\n"
            "WHEN NOT TO USE:\n"
            " - Editing a single file you already know — just `read` it.\n"
            " - Searching content/comments/strings — graph is purely structural, "
            "`grep` is better for free text.\n"
            " - Markdown / JSON / configs — not indexed.\n"
            " - <5 file question — the report overhead isn't worth it.\n"
            " - When you've just edited code and the graph hasn't been rebuilt — "
            "the cache is stale; read the file directly.\n\n"
            "Actions:\n"
            "- build: AST-parses the workspace via the relay. INCREMENTAL: only "
            "files whose mtime changed are re-parsed; deleted files have their "
            "nodes/edges garbage-collected. First build is full; subsequent builds "
            "are cheap. Returns {nodes, edges, files, reparsed, removed} counts.\n"
            "- query: BFS from entities matching the keyword question. depth "
            "controls how far to expand (default 3). Requires a prior build.\n"
            "- report: God nodes (top by degree) + total node/edge counts + "
            "language distribution. Quick overview after build.\n"
            "- node: Detail on one entity — file, location, type, neighbour edges. "
            "Pass the label in `question`.\n\n"
            "PASSIVE DIGEST: when a graph exists for the conv, a one-line summary "
            "(entity count, language mix, top god nodes) is auto-injected into "
            "the system prompt under '## Project structure' — so you have basic "
            "awareness without any tool call.\n\n"
            "Parameters:\n"
            "- action (required): build / query / report / node.\n"
            "- path: project root for build (default '.').\n"
            "- question: search text for query/node.\n"
            "- depth: BFS depth for query (default 3).\n"
            "- source: relay service name for build (omit for the conv's default "
            "relay)."
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
                        "build: fetch code via relay and index into a graph; "
                        "query: traverse graph with a question; "
                        "report: get graph summary (god nodes, stats); "
                        "node: get details about a specific entity"
                    ),
                },
                "path": {"type": "string", "description": "Project root path (for build, default: '.')"},
                "question": {"type": "string", "description": "Query text (for query/node)"},
                "depth": {"type": "integer", "description": "Traversal depth (default: 3)"},
                "source": {
                    "type": "string",
                    "description": "Relay/filesystem service name (for build). Omit for default.",
                },
            },
            "required": ["action"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not self._user_id or not self._conversation_id:
            return "Error: user_id and conversation_id required"

        from core.project_graph import ProjectGraph
        pg = ProjectGraph.for_conversation(self._user_id, self._conversation_id)

        if action == "build":
            path = arguments.get("path", ".")
            # Resolve filesystem service (relay) via BaseFsHandler
            source = arguments.get("source", "")
            svc, workdir = self._resolve(source)
            if svc == "filestore":
                return "Error: project_graph build requires a relay, not filestore"
            if svc is None and workdir is None:
                return "Error: no relay connected. Connect a relay to index the codebase."
            if workdir:
                return "Error: project_graph build requires a relay filesystem service"
            # Build via relay
            result = pg.build_from_relay(svc, path)
            status = result.get("status", "?")
            if status == "built":
                return (f"Project graph built: {result.get('nodes', 0)} nodes, "
                        f"{result.get('edges', 0)} edges "
                        f"({result.get('files', 0)} files indexed)")
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
