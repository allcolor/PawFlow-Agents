"""Project Graph — structural code graph for a conversation's workspace.

Uses the integrated graphify pipeline (core/graphify/) for AST extraction
across 17 languages via tree-sitter. Build runs as a single exec on the
relay — the extraction script runs where the code is, results are sent back.

Storage: data/graphs/{user}/{conv_id}/graph.json
"""

import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

import core.paths as _paths

# Extensions supported by graphify AST extraction
_CODE_EXTENSIONS = (
    "*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs",
    "*.java", "*.c", "*.h", "*.cpp", "*.cc", "*.cxx", "*.hpp",
    "*.rb", "*.cs", "*.kt", "*.kts", "*.scala", "*.php", "*.swift",
    "*.lua", "*.toc", "*.zig", "*.ps1", "*.ex", "*.exs",
)

# Python script that runs ON THE RELAY to extract AST and return JSON
_RELAY_EXTRACT_SCRIPT = '''
import json, sys, os
from pathlib import Path

# Discover code files
EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".rb", ".cs",
    ".kt", ".kts", ".scala", ".php", ".swift", ".lua", ".toc",
    ".zig", ".ps1", ".ex", ".exs"}

SKIP_DIRS = {"venv", ".venv", "env", ".env", "node_modules", "__pycache__",
    ".git", "dist", "build", "target", "out", "site-packages", ".tox",
    ".eggs", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

root = Path(os.environ.get("PAWFLOW_GRAPH_ROOT", ".")).resolve()
files = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
    for fname in filenames:
        p = Path(dirpath) / fname
        if p.suffix in EXTENSIONS:
            files.append(p)
            if len(files) >= 500:
                break
    if len(files) >= 500:
        break

if not files:
    print(json.dumps({"status": "skipped", "reason": "no code files found"}))
    sys.exit(0)

# Try tree-sitter extraction (graphify must be installed on the relay)
try:
    from graphify.extract import extract
    from graphify.build import build
    extraction = extract(files)
    G = build([extraction])

    nodes = []
    for n, data in G.nodes(data=True):
        sf = data.get("source_file", "")
        if sf:
            try:
                sf = str(Path(sf).relative_to(root))
            except ValueError:
                pass
        nodes.append({
            "id": n, "label": data.get("label", n),
            "file_type": data.get("file_type", "code"),
            "source_file": sf.replace(os.sep, "/"),
            "source_location": data.get("source_location", ""),
        })
    edges = []
    for u, v, data in G.edges(data=True):
        sf = data.get("source_file", "")
        if sf:
            try:
                sf = str(Path(sf).relative_to(root))
            except ValueError:
                pass
        edges.append({
            "source": u, "target": v,
            "relation": data.get("relation", "related"),
            "confidence": data.get("confidence", "EXTRACTED"),
            "source_file": sf.replace(os.sep, "/"),
        })

    print(json.dumps({
        "status": "built", "nodes": nodes, "edges": edges,
        "total_files": len(files),
    }))

except Exception as e:
    # Fallback: simple import-based graph (no tree-sitter needed)
    import re
    nodes, edges = [], []
    for f in files:
        if f.suffix != ".py":
            continue
        try:
            rel = str(f.relative_to(root)).replace(os.sep, "/")
        except ValueError:
            rel = f.name
        fid = rel.replace("/", "_").replace(".py", "")
        nodes.append({"id": fid, "label": rel, "file_type": "code", "source_file": rel})
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for match in re.findall("^(?:from|import)\\s+([\\w.]+)", content, re.MULTILINE):
                edges.append({
                    "source": fid, "target": match.replace(".", "_"),
                    "relation": "imports", "confidence": "EXTRACTED",
                })
        except Exception:
            pass

    print(json.dumps({
        "status": "built_fallback", "nodes": nodes, "edges": edges,
        "total_files": len(files), "error": str(e),
    }))
'''


class ProjectGraph:
    """Persistent code structure graph for a conversation workspace."""

    _instances: Dict[str, "ProjectGraph"] = {}
    _lock = threading.Lock()

    def __init__(self, graph_path: str):
        self._path = Path(graph_path)
        self._graph: Dict[str, Any] = {"nodes": [], "edges": [], "metadata": {}}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._graph = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Project graph load failed: %s", e)

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._graph, separators=(',', ':')), encoding="utf-8")
        tmp.replace(self._path)

    @classmethod
    def for_conversation(cls, user_id: str, conv_id: str) -> "ProjectGraph":
        key = f"{user_id}::{conv_id}"
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    safe_user = user_id.replace("/", "_").replace("\\", "_")
                    safe_conv = conv_id.replace(":", "_")
                    path = Path(str(_paths.GRAPHS_DIR)) / safe_user / safe_conv / "graph.json"
                    cls._instances[key] = cls(str(path))
        return cls._instances[key]

    @property
    def nodes(self) -> List[Dict]:
        return self._graph.get("nodes", [])

    @property
    def edges(self) -> List[Dict]:
        return self._graph.get("edges", [])

    def has_graph(self) -> bool:
        return bool(self.nodes)

    # ── Build via single relay exec ───────────────────────────────

    def build_from_relay(self, fs_service, root_path: str = ".") -> Dict:
        """Build project graph by running extraction script on the relay.

        Single exec call — the script discovers files, parses AST, and returns
        the full graph as JSON. No N×read_file roundtrips.
        """
        try:
            # Write extraction script to relay, exec it, read result
            script_name = ".pawflow_graph_extract.py"
            fs_service.write_file(script_name, _RELAY_EXTRACT_SCRIPT.encode("utf-8"))
            try:
                env = {"PAWFLOW_GRAPH_ROOT": root_path}
                result = fs_service.exec(".", f"python3 {script_name}", env=env)
            finally:
                try:
                    fs_service.delete_file(script_name)
                except Exception:
                    pass

            stdout = result.get("stdout", "") if isinstance(result, dict) else str(result)
            stderr = result.get("stderr", "") if isinstance(result, dict) else ""
            returncode = result.get("returncode", 0) if isinstance(result, dict) else 0

            if returncode != 0:
                logger.error("[project_graph] Relay script failed: %s", stderr[:500])
                return {"status": "error", "reason": f"Script failed (exit {returncode}): {stderr[:200]}"}

            # Parse JSON from stdout
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError as e:
                logger.error("[project_graph] Invalid JSON from relay: %s", str(e))
                return {"status": "error", "reason": f"Invalid JSON output: {str(e)[:100]}"}

            status = data.get("status", "?")
            if status == "skipped":
                return data

            nodes = data.get("nodes", [])
            edges = data.get("edges", [])

            self._graph = {
                "nodes": nodes, "edges": edges,
                "metadata": {
                    "root": root_path,
                    "total_files": data.get("total_files", 0),
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "fallback": status == "built_fallback",
                },
            }
            self._save()
            logger.info("[project_graph] Built: %d nodes, %d edges (%s)",
                        len(nodes), len(edges), status)
            return {"status": status, "nodes": len(nodes), "edges": len(edges),
                    "files": data.get("total_files", 0)}

        except Exception as e:
            logger.error("Project graph build failed: %s", e, exc_info=True)
            return {"status": "error", "reason": str(e)}

    # ── Query ──────────────────────────────────────────────────────

    def query(self, question: str, mode: str = "bfs",
              depth: int = 3, max_results: int = 50) -> List[Dict]:
        """BFS/DFS traversal on the project graph."""
        q = question.lower()
        seeds = [n["id"] for n in self.nodes if q in n.get("label", "").lower()]
        if not seeds:
            return []

        adj: Dict[str, List[Dict]] = {}
        for e in self.edges:
            adj.setdefault(e["source"], []).append(e)
            adj.setdefault(e["target"], []).append(e)

        visited = set()
        results = []
        queue = [(s, 0) for s in seeds]
        while queue and len(results) < max_results:
            node, d = queue.pop(0)
            if node in visited or d > depth:
                continue
            visited.add(node)
            for e in adj.get(node, []):
                if e not in results:
                    results.append(e)
                other = e["target"] if e["source"] == node else e["source"]
                if other not in visited:
                    queue.append((other, d + 1))
        return results

    def get_node(self, label: str) -> Optional[Dict]:
        """Get a node by label (fuzzy match)."""
        q = label.lower()
        for n in self.nodes:
            if q in n.get("label", "").lower() or q in n.get("id", "").lower():
                neighbors = [
                    e for e in self.edges
                    if e["source"] == n["id"] or e["target"] == n["id"]
                ]
                return {**n, "neighbors": len(neighbors), "neighbor_edges": neighbors[:20]}
        return None

    def get_report(self) -> str:
        """Generate a concise graph report."""
        meta = self._graph.get("metadata", {})
        nodes = self.nodes
        edges = self.edges

        # God nodes
        degree: Dict[str, int] = {}
        for e in edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        top = sorted(degree.items(), key=lambda x: -x[1])[:10]

        # Confidence breakdown
        conf_counts = {}
        for e in edges:
            c = e.get("confidence", "EXTRACTED")
            conf_counts[c] = conf_counts.get(c, 0) + 1

        lines = [
            f"Project Graph: {meta.get('root', '?')}",
            f"Nodes: {len(nodes)}, Edges: {len(edges)}, Files: {meta.get('total_files', '?')}",
            f"Confidence: {', '.join(f'{k}={v}' for k, v in conf_counts.items())}",
            "God nodes (most connected):",
        ]
        for name, deg in top:
            label = next((n["label"] for n in nodes if n["id"] == name), name)
            lines.append(f"  {label} ({deg} connections)")
        return "\n".join(lines)
