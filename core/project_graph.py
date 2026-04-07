"""Project Graph — structural code graph for a conversation's workspace.

Uses the integrated graphify pipeline (core/graphify/) for AST extraction
across 17 languages via tree-sitter. Files are fetched via the relay
filesystem service (user's machine), AST parsing runs server-side.

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

_DEFAULT_DIR = "data/graphs"

# Extensions supported by graphify AST extraction
_CODE_EXTENSIONS = (
    "*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs",
    "*.java", "*.c", "*.h", "*.cpp", "*.cc", "*.cxx", "*.hpp",
    "*.rb", "*.cs", "*.kt", "*.kts", "*.scala", "*.php", "*.swift",
    "*.lua", "*.toc", "*.zig", "*.ps1", "*.ex", "*.exs",
)


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
                    path = Path(_DEFAULT_DIR) / safe_user / safe_conv / "graph.json"
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

    # ── Build via relay FS service ────────────────────────────────

    def build_from_relay(self, fs_service, root_path: str = ".") -> Dict:
        """Build project graph by fetching files via relay, parsing AST server-side.

        1. svc.search() to discover code files on the user's machine
        2. svc.read_file() to fetch each file's bytes
        3. Write to server temp dir
        4. Graphify AST extract + build on temp dir
        5. Clean up temp dir
        """
        try:
            from core.graphify.extract import extract
            from core.graphify.build import build
        except ImportError as e:
            return {"status": "error", "reason": f"graphify not available: {e}"}

        tmpdir = None
        try:
            # Stage 1: discover code files via relay
            all_files = []
            for pattern in _CODE_EXTENSIONS:
                try:
                    matches = fs_service.search(root_path, pattern, recursive=True)
                    if isinstance(matches, list):
                        all_files.extend(matches)
                except Exception as e:
                    logger.debug("search(%s, %s) failed: %s", root_path, pattern, e)

            if not all_files:
                return {"status": "skipped", "reason": "no code files found"}

            # Cap at 500 files to avoid overwhelming the relay
            if len(all_files) > 500:
                logger.info("[project_graph] Capping %d files to 500", len(all_files))
                all_files = all_files[:500]

            logger.info("[project_graph] Fetching %d files via relay...", len(all_files))

            # Stage 2: fetch files via relay, write to temp dir
            tmpdir = tempfile.mkdtemp(prefix="pawflow_pg_")
            local_paths = []
            fetched = 0
            for rel_path in all_files:
                try:
                    content = fs_service.read_file(rel_path)
                    if not isinstance(content, bytes):
                        content = content.encode("utf-8") if isinstance(content, str) else b""
                    # Recreate directory structure in temp
                    local_path = Path(tmpdir) / rel_path.replace("\\", "/")
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(content)
                    local_paths.append(local_path)
                    fetched += 1
                except Exception as e:
                    logger.debug("read_file(%s) failed: %s", rel_path, e)

            if not local_paths:
                return {"status": "error", "reason": "could not read any code files"}

            logger.info("[project_graph] Fetched %d/%d files, extracting AST...",
                        fetched, len(all_files))

            # Stage 3: AST extraction on temp dir (server-side, tree-sitter)
            extractions = extract(local_paths)

            # Stage 4: build graph
            G = build([extractions])

            # Convert to our format
            nodes = []
            for n, data in G.nodes(data=True):
                # Strip temp dir prefix from source_file paths
                sf = data.get("source_file", "")
                if sf and tmpdir:
                    sf = sf.replace(tmpdir, "").replace("\\", "/").lstrip("/")
                nodes.append({
                    "id": n,
                    "label": data.get("label", n),
                    "file_type": data.get("file_type", "code"),
                    "source_file": sf,
                    "source_location": data.get("source_location", ""),
                })
            edges = []
            for u, v, data in G.edges(data=True):
                sf = data.get("source_file", "")
                if sf and tmpdir:
                    sf = sf.replace(tmpdir, "").replace("\\", "/").lstrip("/")
                edges.append({
                    "source": u, "target": v,
                    "relation": data.get("relation", "related"),
                    "confidence": data.get("confidence", "EXTRACTED"),
                    "source_file": sf,
                })

            self._graph = {
                "nodes": nodes, "edges": edges,
                "metadata": {
                    "root": root_path,
                    "total_files": len(all_files),
                    "fetched_files": fetched,
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                },
            }
            self._save()
            return {"status": "built", "nodes": len(nodes), "edges": len(edges),
                    "files": fetched}

        except Exception as e:
            logger.error("Project graph build failed: %s", e, exc_info=True)
            return {"status": "error", "reason": str(e)}
        finally:
            if tmpdir and os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)

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
