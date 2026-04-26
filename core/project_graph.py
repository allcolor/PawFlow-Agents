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
# Incremental build: caller may pass a JSON dict {rel_path: mtime_int}
# of files we already have nodes/edges for. We skip parsing for any
# file whose current mtime matches — the server will keep the cached
# nodes/edges. Files in `known` but missing from disk get reported
# as `removed`; the server uses that to garbage-collect orphans.
try:
    known = json.loads(os.environ.get("PAWFLOW_GRAPH_KNOWN", "") or "{}")
    if not isinstance(known, dict):
        known = {}
except (json.JSONDecodeError, ValueError):
    known = {}

all_files = []   # rel paths discovered now (for orphan detection)
to_parse = []    # absolute paths we need to re-parse
mtimes = {}      # rel path -> int mtime for the new metadata
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
    for fname in filenames:
        p = Path(dirpath) / fname
        if p.suffix not in EXTENSIONS:
            continue
        try:
            rel = str(p.relative_to(root)).replace(os.sep, "/")
        except ValueError:
            rel = p.name
        try:
            mt = int(p.stat().st_mtime)
        except OSError:
            continue
        all_files.append(rel)
        mtimes[rel] = mt
        if known.get(rel) != mt:
            to_parse.append(p)

removed = sorted(set(known) - set(all_files))

if not all_files:
    print(json.dumps({"status": "skipped", "reason": "no code files found"}))
    sys.exit(0)

# Pure-cache hit: nothing changed, no removal. Server keeps the
# entire previous graph; no parsing happens.
if not to_parse and not removed:
    print(json.dumps({
        "status": "unchanged", "all_files": all_files,
        "mtimes": mtimes, "total_files": len(all_files),
    }))
    sys.exit(0)

files = to_parse  # only re-parse the changed ones

# graphify.extract prints progress lines ("AST extraction: N/M
# files (X%)") to stdout. We reserve stdout for the final JSON
# result, so redirect graphify's output to stderr while it runs.
import contextlib

# graphify is bundled in core/graphify/ on the server, vendored in
# /opt/pawflow on the relay container at startup. No fallback path:
# if the import fails the relay setup is broken and the agent should
# see the real error instead of a degraded import-only graph.
from graphify.extract import extract
from graphify.build import build

with contextlib.redirect_stdout(sys.stderr):
    extraction = extract(files) if files else []
    G = build([extraction]) if extraction else None

nodes = []
edges = []
if G is not None:
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

# parsed_files = rel-paths the server should drop+replace.
# all_files = rel-paths the server should keep tracking.
# removed = files known previously but missing now (orphans).
parsed_files = sorted({
    rel
    for rel, _ in mtimes.items()
    if known.get(rel) != mtimes[rel]
})
print(json.dumps({
    "status": "built", "nodes": nodes, "edges": edges,
    "total_files": len(all_files),
    "all_files": all_files,
    "parsed_files": parsed_files,
    "removed": removed,
    "mtimes": mtimes,
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
        """Build (or refresh) the project graph from the relay.

        Incremental: passes the relay the {file: mtime} map we already
        have so it only re-parses files whose mtime changed. The server
        merges the partial result with the cached graph — nodes/edges
        from unchanged files are kept verbatim, files reported as
        removed have their nodes/edges garbage-collected, files that
        were re-parsed have their nodes/edges replaced. First build
        for a conv (no cache) is naturally a full build.

        Single exec call regardless of incremental vs full.
        """
        try:
            # What we know from the cached graph: per-file mtimes from
            # metadata.files. The relay script reads this via env var.
            known_files: Dict[str, int] = (
                self._graph.get("metadata", {}).get("files", {}) or {})
            script_name = ".pawflow_graph_extract.py"
            fs_service.write_file(script_name, _RELAY_EXTRACT_SCRIPT.encode("utf-8"))
            try:
                env = {
                    "PAWFLOW_GRAPH_ROOT": root_path,
                    "PAWFLOW_GRAPH_KNOWN": json.dumps(known_files),
                }
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

            try:
                data = json.loads(stdout)
            except json.JSONDecodeError as e:
                logger.error(
                    "[project_graph] Invalid JSON from relay: %s\n"
                    "  stdout[:500]=%r\n  stderr[:500]=%r",
                    str(e), (stdout or "")[:500], (stderr or "")[:500])
                return {"status": "error",
                        "reason": (f"Invalid JSON output: {str(e)[:100]}; "
                                    f"stdout starts with {(stdout or '')[:60]!r}")}

            status = data.get("status", "?")
            if status == "skipped":
                return data

            new_mtimes: Dict[str, int] = data.get("mtimes", {}) or {}

            # Cache hit: nothing parsed, nothing removed. Refresh the
            # mtimes (a cheap touch can shift them without changing
            # content) and return early.
            if status == "unchanged":
                meta = self._graph.setdefault("metadata", {})
                meta["files"] = new_mtimes
                meta["total_files"] = data.get("total_files", len(new_mtimes))
                self._save()
                logger.info(
                    "[project_graph] unchanged — %d files, %d nodes, %d edges",
                    len(new_mtimes), len(self.nodes), len(self.edges))
                return {"status": "unchanged",
                        "nodes": len(self.nodes), "edges": len(self.edges),
                        "files": len(new_mtimes)}

            new_nodes = data.get("nodes", [])
            new_edges = data.get("edges", [])
            parsed_files = set(data.get("parsed_files", []) or [])
            removed = set(data.get("removed", []) or [])
            # `gone` = files whose nodes/edges must be dropped from the
            # cache. Two reasons to drop: file was re-parsed (will be
            # replaced by new_nodes/new_edges) or file vanished from
            # disk (orphan GC).
            gone = parsed_files | removed

            kept_nodes = [n for n in self.nodes
                          if n.get("source_file", "") not in gone]
            kept_edges = [e for e in self.edges
                          if e.get("source_file", "") not in gone]
            merged_nodes = kept_nodes + new_nodes
            merged_edges = kept_edges + new_edges

            self._graph = {
                "nodes": merged_nodes, "edges": merged_edges,
                "metadata": {
                    "root": root_path,
                    "total_files": data.get("total_files", len(new_mtimes)),
                    "node_count": len(merged_nodes),
                    "edge_count": len(merged_edges),
                    "files": new_mtimes,
                },
            }
            self._save()
            logger.info(
                "[project_graph] %s — reparsed %d, removed %d, total %d "
                "(nodes=%d, edges=%d)",
                status, len(parsed_files), len(removed),
                len(new_mtimes), len(merged_nodes), len(merged_edges))
            return {
                "status": status,
                "nodes": len(merged_nodes), "edges": len(merged_edges),
                "files": len(new_mtimes),
                "reparsed": len(parsed_files), "removed": len(removed),
            }

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
