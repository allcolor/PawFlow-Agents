"""Project Graph — structural code graph for a conversation's workspace.

Uses the integrated graphify pipeline (core/graphify/) for AST extraction
across 17 languages via tree-sitter. No external graphify dependency.

Storage: data/graphs/{user}/{conv_id}/graph.json
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/graphs"


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

    # ── Build from graphify ────────────────────────────────────────

    def build_from_directory(self, root_path: str, use_semantic: bool = False) -> Dict:
        """Build project graph from a directory using graphify pipeline.

        Args:
            root_path: path to the project root
            use_semantic: if True, also extract semantic relations (needs LLM)
        """
        try:
            from core.graphify.detect import detect
            from core.graphify.extract import extract
            from core.graphify.build import build
        except ImportError:
            return self._build_fallback(root_path)

        try:
            # Stage 1: detect files
            detection = detect(Path(root_path))
            if not detection.get("needs_graph"):
                return {"status": "skipped", "reason": detection.get("warning", "corpus too small")}

            # Stage 2: extract (AST only — no LLM cost)
            code_files = detection.get("files", {}).get("code", [])
            if not code_files:
                return {"status": "skipped", "reason": "no code files found"}

            extractions = extract([Path(f) for f in code_files])

            # Stage 3: build graph
            G = build([extractions])

            # Convert to our format
            nodes = []
            for n, data in G.nodes(data=True):
                nodes.append({
                    "id": n,
                    "label": data.get("label", n),
                    "file_type": data.get("file_type", "code"),
                    "source_file": data.get("source_file", ""),
                    "source_location": data.get("source_location", ""),
                })
            edges = []
            for u, v, data in G.edges(data=True):
                edges.append({
                    "source": u, "target": v,
                    "relation": data.get("relation", "related"),
                    "confidence": data.get("confidence", "EXTRACTED"),
                    "source_file": data.get("source_file", ""),
                })

            self._graph = {
                "nodes": nodes, "edges": edges,
                "metadata": {
                    "root": root_path,
                    "total_files": detection.get("total_files", 0),
                    "total_words": detection.get("total_words", 0),
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                },
            }
            self._save()
            return {"status": "built", "nodes": len(nodes), "edges": len(edges)}

        except Exception as e:
            logger.error("Graphify build failed: %s", e)
            return self._build_fallback(root_path)

    def _build_fallback(self, root_path: str) -> Dict:
        """Simple fallback if graphify is not installed: just list files + imports."""
        import os
        import re
        nodes = []
        edges = []
        root = Path(root_path)
        py_files = list(root.rglob("*.py"))
        for f in py_files[:200]:  # cap
            rel = str(f.relative_to(root)).replace("\\", "/")
            fid = rel.replace("/", "_").replace(".py", "")
            nodes.append({"id": fid, "label": rel, "file_type": "code", "source_file": rel})
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for match in re.findall(r'^(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
                    target = match.replace(".", "_")
                    edges.append({
                        "source": fid, "target": target,
                        "relation": "imports", "confidence": "EXTRACTED",
                    })
            except Exception:
                continue
        self._graph = {
            "nodes": nodes, "edges": edges,
            "metadata": {"root": root_path, "node_count": len(nodes),
                         "edge_count": len(edges), "fallback": True},
        }
        self._save()
        return {"status": "built_fallback", "nodes": len(nodes), "edges": len(edges)}

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
