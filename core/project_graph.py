"""Project Graph — structural code graph for a conversation's workspace.

Uses the integrated graphify pipeline (core/graphify/) for AST extraction
across 17 languages via tree-sitter. Build runs as a single exec on the
relay — the extraction script runs where the code is, results are sent back.

Storage: data/graphs/{user}/{relay_id}/graph.json
"""

import base64
import gzip
import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RELAY_PAYLOAD_PREFIX = "pawflow-project-graph-v1:gzip-base64:"
_GRAPH_FORMAT_VERSION = 2

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
import base64, gc, gzip, json, os, re, sys, tempfile
from pathlib import Path

# Discover code files
EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".rb", ".cs",
    ".kt", ".kts", ".scala", ".php", ".swift", ".lua", ".toc",
    ".zig", ".ps1", ".ex", ".exs"}

SKIP_DIRS = {"venv", ".venv", "env", ".env", "node_modules", "__pycache__",
    ".git", "dist", "build", "target", "out", "site-packages", ".tox",
    ".eggs", ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor"}
LOW_SIGNAL_MARKERS = (".min.", ".bundle.", ".umd.", "-vendor.")
HASHED_ASSET = re.compile(r"^index-[A-Za-z0-9_-]{8,}\\.(?:js|ts)$", re.I)

root = Path(os.environ.get("PAWFLOW_GRAPH_ROOT", ".")).resolve()
# Incremental build: caller may pass a JSON dict {rel_path: mtime_int}
# of files we already have nodes/edges for. We skip parsing for any
# file whose current mtime matches — the server will keep the cached
# nodes/edges. Files in `known` but missing from disk get reported
# as `removed`; the server uses that to garbage-collect orphans.
# The map arrives gzip+base64-encoded: it can outgrow the ~32K
# per-variable cap on Windows if sent as plain JSON.
try:
    raw_known = os.environ.get("PAWFLOW_GRAPH_KNOWN", "")
    known = json.loads(gzip.decompress(
        base64.b64decode(raw_known)).decode("utf-8")) if raw_known else {}
    if not isinstance(known, dict):
        known = {}
except (json.JSONDecodeError, ValueError, OSError):
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
        lower_name = fname.lower()
        if any(marker in lower_name for marker in LOW_SIGNAL_MARKERS):
            continue
        if Path(dirpath).name.lower() == "assets" and HASHED_ASSET.fullmatch(fname):
            continue
        try:
            rel = str(p.relative_to(root)).replace(os.sep, "/")
        except ValueError:
            rel = p.name
        try:
            info = p.stat()
            mt = f"{info.st_mtime_ns}:{info.st_size}"
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
# /opt/pawflow on the relay container at startup. The exec env carries
# no PYTHONPATH, so add the staging locations to sys.path ourselves.
# No fallback beyond that: if the import still fails the relay setup
# is broken and the agent should see the real error instead of a
# degraded import-only graph.
# Reverse order: each insert(0) prepends, so PAWFLOW_RELAY_CODE_DIR
# must go last to end up FIRST and win over the /opt/pawflow default.
for cand in ("/opt/pawflow", os.environ.get("PAWFLOW_RELAY_CODE_DIR", "")):
    if cand and cand not in sys.path and os.path.isdir(os.path.join(cand, "graphify")):
        sys.path.insert(0, cand)
from graphify.extract import extract

FULL_BATCH_MAX_FILES = 32
FULL_BATCH_MAX_BYTES = 8 * 1024 * 1024
full_batch_bytes = 0
for path in files:
    try:
        full_batch_bytes += path.stat().st_size
    except OSError:
        full_batch_bytes = FULL_BATCH_MAX_BYTES + 1
        break
if len(files) <= FULL_BATCH_MAX_FILES and full_batch_bytes <= FULL_BATCH_MAX_BYTES:
    extraction_batches = [files]
else:
    extraction_batches = [[path] for path in files]

def relative_source(value):
    sf = str(value or "")
    if sf:
        try:
            sf = str(Path(sf).relative_to(root))
        except ValueError:
            pass
    return sf.replace(os.sep, "/")

def scoped_id(source_file, node_id):
    rel = relative_source(source_file)
    return ("source:" + rel + ":" + node_id) if rel else ("external:" + node_id)

def node_payload(node_id, data):
    return {
        "id": node_id, "label": data.get("label", node_id),
        "file_type": data.get("file_type", "code"),
        "source_file": relative_source(data.get("source_file", "")),
        "source_location": data.get("source_location", ""),
    }

def edge_payload(edge):
    return {
        "source": str(edge.get("source", "")),
        "target": str(edge.get("target", "")),
        "relation": edge.get("relation", "related"),
        "confidence": edge.get("confidence", "EXTRACTED"),
        "source_file": relative_source(edge.get("source_file", "")),
    }

# parsed_files = rel-paths the server should drop+replace.
# all_files = rel-paths the server should keep tracking.
# removed = files known previously but missing now (orphans).
parsed_files = sorted({
    rel
    for rel, _ in mtimes.items()
    if known.get(rel) != mtimes[rel]
})

# stdout is capped at 10 MiB by the relay. Extract one file at a time so
# Graphify never retains the whole corpus plus its resolution indexes in RAM.
# Nodes stream into the compressed payload immediately; edges use an anonymous
# spool because the JSON object writes its node array first. Neither temporary
# file has a project-tree path and both are unlinked automatically.
with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as edge_spool, \
        tempfile.TemporaryFile() as payload:
    with gzip.open(payload, "wt", encoding="utf-8", compresslevel=6) as out:
        out.write('{"status":"built","nodes":[')
        first_node = True
        with contextlib.redirect_stdout(sys.stderr):
            for batch in extraction_batches:
                extraction = extract(batch)
                raw_nodes = [node for node in extraction.get("nodes", [])
                             if isinstance(node, dict) and node.get("id")]
                local_ids = {}
                node_by_id = {}
                for node in raw_nodes:
                    old_id = str(node["id"])
                    rel = relative_source(node.get("source_file", ""))
                    new_id = scoped_id(node.get("source_file", ""), old_id)
                    node_by_id[new_id] = node
                    if rel:
                        local_ids[(rel, old_id)] = new_id
                for node_id, data in node_by_id.items():
                    if not first_node:
                        out.write(",")
                    json.dump(node_payload(node_id, data), out, separators=(",", ":"))
                    first_node = False
                edge_by_pair = {}
                for edge in extraction.get("edges", []):
                    if not isinstance(edge, dict):
                        continue
                    old_source = str(edge.get("source", ""))
                    old_target = str(edge.get("target", ""))
                    if not old_source or not old_target:
                        continue
                    owner = relative_source(edge.get("source_file", ""))
                    def resolve_endpoint(old_id):
                        local = local_ids.get((owner, old_id))
                        if local:
                            return local
                        # Cross-file references must wait for the complete
                        # server-side graph, not this extraction batch.
                        return "external:" + old_id
                    source = resolve_endpoint(old_source)
                    target = resolve_endpoint(old_target)
                    for old_id, endpoint in ((old_source, source), (old_target, target)):
                        if not endpoint.startswith("external:") or endpoint in node_by_id:
                            continue
                        external = {"label": old_id, "file_type": "code",
                                    "source_file": "", "source_location": ""}
                        if not first_node:
                            out.write(",")
                        json.dump(node_payload(endpoint, external), out,
                                  separators=(",", ":"))
                        first_node = False
                        node_by_id[endpoint] = external
                    edge = dict(edge, source=source, target=target)
                    edge_by_pair[tuple(sorted((source, target)))] = edge
                for edge in edge_by_pair.values():
                    json.dump(edge_payload(edge), edge_spool,
                              separators=(",", ":"))
                    edge_spool.write("\\n")
                del extraction, raw_nodes, node_by_id, local_ids, edge_by_pair
                gc.collect()
        out.write('],"edges":[')
        edge_spool.seek(0)
        first_edge = True
        for line in edge_spool:
            if not first_edge:
                out.write(",")
            out.write(line.rstrip("\\n"))
            first_edge = False
        out.write('],"total_files":')
        json.dump(len(all_files), out)
        out.write(',"all_files":')
        json.dump(all_files, out, separators=(",", ":"))
        out.write(',"parsed_files":')
        json.dump(parsed_files, out, separators=(",", ":"))
        out.write(',"removed":')
        json.dump(removed, out, separators=(",", ":"))
        out.write(',"mtimes":')
        json.dump(mtimes, out, separators=(",", ":"))
        out.write("}")
    payload.seek(0)
    sys.stdout.write("pawflow-project-graph-v1:gzip-base64:")
    while True:
        chunk = payload.read(57 * 1024)
        if not chunk:
            break
        sys.stdout.write(base64.b64encode(chunk).decode("ascii"))
    sys.stdout.write("\\n")
'''


def _decode_relay_payload(stdout: str) -> Dict[str, Any]:
    """Decode either the compact graph transport or a small legacy JSON reply."""
    if not stdout.startswith(_RELAY_PAYLOAD_PREFIX):
        return json.loads(stdout)
    encoded = stdout[len(_RELAY_PAYLOAD_PREFIX):].strip()
    try:
        compressed = base64.b64decode(encoded, validate=True)
        raw = gzip.decompress(compressed).decode("utf-8")
        return json.loads(raw)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid compressed project graph payload: {exc}") from exc


class ProjectGraph:
    """Persistent code structure graph for one relay project."""

    _instances: Dict[str, "ProjectGraph"] = {}
    _lock = threading.Lock()

    def __init__(self, graph_path: str, user_id: str = "", relay_id: str = ""):
        self._path = Path(graph_path)
        self.user_id = user_id
        self.relay_id = relay_id
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
    def for_relay(cls, user_id: str, relay_id: str) -> "ProjectGraph":
        if not user_id:
            raise ValueError("user_id is required for project graph storage")
        if not relay_id:
            raise ValueError("relay_id is required for project graph storage")
        key = f"{user_id}::{relay_id}"
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    def _safe(value: str) -> str:
                        clean = re.sub(
                            r"[^A-Za-z0-9._-]+", "-", value).strip(".-")[:48]
                        digest = hashlib.sha256(
                            value.encode("utf-8")).hexdigest()[:12]
                        return f"{clean or 'scope'}-{digest}"
                    path = (
                        Path(str(_paths.GRAPHS_DIR))
                        / _safe(user_id)
                        / _safe(relay_id)
                        / "graph.json"
                    )
                    cls._instances[key] = cls(
                        str(path), user_id=user_id, relay_id=relay_id)
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

    def build_from_relay(self, fs_service, root_path: str = ".",
                         local: bool = False) -> Dict:
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
            old_meta = self._graph.get("metadata", {}) or {}
            format_changed = old_meta.get("format_version") != _GRAPH_FORMAT_VERSION
            root_changed = bool(
                old_meta.get("root") and old_meta.get("root") != root_path)
            reset_graph = root_changed or format_changed
            known_files: Dict[str, int] = (
                {} if reset_graph else old_meta.get("files", {}) or {})
            # The script and the known-files map travel via env vars,
            # never the command line: cmd.exe caps a command at 8191
            # chars while an env var holds up to 32767. KNOWN is
            # gzip+base64 so large projects stay under that too.
            encoded_script = base64.b64encode(
                _RELAY_EXTRACT_SCRIPT.encode("utf-8")).decode("ascii")
            encoded_known = base64.b64encode(gzip.compress(json.dumps(
                known_files, separators=(',', ':')).encode("utf-8"))).decode("ascii")
            command = (
                "python3 -c \"import base64,os;"
                "exec(base64.b64decode(os.environ['PAWFLOW_GRAPH_SCRIPT']))\"")
            env = {
                "PAWFLOW_GRAPH_ROOT": root_path,
                "PAWFLOW_GRAPH_SCRIPT": encoded_script,
                "PAWFLOW_GRAPH_KNOWN": encoded_known,
            }
            result = fs_service.exec(".", command, env=env, local=local)

            stdout = result.get("stdout", "") if isinstance(result, dict) else str(result)
            stderr = result.get("stderr", "") if isinstance(result, dict) else ""
            returncode = result.get("returncode", 0) if isinstance(result, dict) else 0

            if returncode != 0:
                logger.error("[project_graph] Relay script failed: %s", stderr[:500])
                return {"status": "error", "reason": f"Script failed (exit {returncode}): {stderr[:200]}"}

            try:
                data = _decode_relay_payload(stdout)
            except (json.JSONDecodeError, ValueError) as e:
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

            # The relay streams each file independently to keep its peak memory
            # bounded. Recreate the old exact-id / undirected-endpoint collapse
            # here, after the complete delta has reached the server.
            node_by_id = {
                str(node.get("id")): node
                for node in (data.get("nodes", []) or [])
                if isinstance(node, dict) and node.get("id")
            }
            new_nodes = list(node_by_id.values())
            raw_new_edges = data.get("edges", []) or []
            parsed_files = set(data.get("parsed_files", []) or [])
            removed = set(data.get("removed", []) or [])
            # `gone` = files whose nodes/edges must be dropped from the
            # cache. Two reasons to drop: file was re-parsed (will be
            # replaced by new_nodes/new_edges) or file vanished from
            # disk (orphan GC).
            gone = parsed_files | removed

            kept_nodes = [] if reset_graph else [
                n for n in self.nodes if n.get("source_file", "") not in gone]
            kept_edges = [] if reset_graph else [
                e for e in self.edges if e.get("source_file", "") not in gone]
            merged_by_id = {node["id"]: node for node in kept_nodes + new_nodes}
            symbols = {}
            for node_id, node in merged_by_id.items():
                prefix = "source:" + node.get("source_file", "") + ":"
                if node.get("source_file") and node_id.startswith(prefix):
                    symbols.setdefault(node_id[len(prefix):], []).append(node_id)

            def resolve_reference(reference):
                if reference.startswith("external:"):
                    symbol = reference[len("external:"):]
                    matches = symbols.get(symbol, [])
                    if len(matches) == 1:
                        return matches[0]
                    merged_by_id.setdefault(reference, {
                        "id": reference, "label": symbol, "file_type": "code",
                        "source_file": "", "source_location": "",
                    })
                    return reference
                return reference if reference in merged_by_id else ""

            edge_by_pair = {}
            for edge in kept_edges + raw_new_edges:
                if not isinstance(edge, dict):
                    continue
                source_ref = str(edge.get("source_ref", edge.get("source", "")))
                target_ref = str(edge.get("target_ref", edge.get("target", "")))
                source = resolve_reference(source_ref)
                target = resolve_reference(target_ref)
                if not source or not target:
                    continue
                edge = dict(edge, source=source, target=target)
                # Retain unresolved identities so deletion, restoration or a
                # newly ambiguous homonym re-resolves unchanged callers too.
                if source_ref.startswith("external:"):
                    edge["source_ref"] = source_ref
                if target_ref.startswith("external:"):
                    edge["target_ref"] = target_ref
                key = (*sorted((source, target)), edge.get("relation", ""),
                       edge.get("source_file", ""))
                edge_by_pair[key] = edge
            merged_edges = list(edge_by_pair.values())
            referenced = {edge[key] for edge in merged_edges
                          for key in ("source", "target")}
            merged_nodes = [node for node_id, node in merged_by_id.items()
                            if not node_id.startswith("external:") or node_id in referenced]

            self._graph = {
                "nodes": merged_nodes, "edges": merged_edges,
                "metadata": {
                    "root": root_path,
                    "format_version": _GRAPH_FORMAT_VERSION,
                    "relay_id": self.relay_id,
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
        seeds = [n["id"] for n in self.nodes
                 if q in n.get("label", "").lower()
                 or q in n.get("source_file", "").lower()]
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
            if (q in n.get("label", "").lower()
                    or q in n.get("id", "").lower()
                    or q in n.get("source_file", "").lower()):
                neighbors = [
                    e for e in self.edges
                    if e["source"] == n["id"] or e["target"] == n["id"]
                ]
                return {**n, "neighbors": len(neighbors), "neighbor_edges": neighbors[:20]}
        return None

    def ego_subgraph(self, label: str = "", depth: int = 1,
                     max_nodes: int = 150) -> Dict:
        """Induced subgraph for the interactive graph view.

        With a label: BFS neighborhood around the first matching node,
        capped at max_nodes. Without a label: the most-connected nodes
        as an overview entry point. Edges are those whose both ends are
        in the selection.
        """
        by_id = {n["id"]: n for n in self.nodes}
        adj: Dict[str, List[Dict]] = {}
        degree: Dict[str, int] = {}
        for e in self.edges:
            adj.setdefault(e["source"], []).append(e)
            adj.setdefault(e["target"], []).append(e)
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1

        center = None
        truncated = False
        if label:
            q = label.lower()
            center = next(
                (n["id"] for n in self.nodes
                 if q in n.get("label", "").lower()
                 or q in n.get("id", "").lower()),
                None)
            if center is None:
                return {"error": f"Node '{label}' not found"}
            seen = {center}
            order = [center]
            frontier = [center]
            for _ in range(max(1, depth)):
                nxt = []
                for node in frontier:
                    # Most-connected neighbors first so hubs survive the cap.
                    others = sorted(
                        {e["target"] if e["source"] == node else e["source"]
                         for e in adj.get(node, [])},
                        key=lambda o: -degree.get(o, 0))
                    for other in others:
                        if other in seen or other not in by_id:
                            continue
                        if len(order) >= max_nodes:
                            truncated = True
                            break
                        seen.add(other)
                        order.append(other)
                        nxt.append(other)
                frontier = nxt
            selected = order
        else:
            ranked = sorted(degree, key=lambda n: -degree[n])
            selected = [n for n in ranked if n in by_id][:max_nodes]
            truncated = len(ranked) > len(selected)

        keep = set(selected)
        edges = [e for e in self.edges
                 if e["source"] in keep and e["target"] in keep]
        nodes = [{**by_id[i], "degree": degree.get(i, 0)} for i in selected]
        return {"center": center, "nodes": nodes, "edges": edges,
                "truncated": truncated,
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges)}

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
        from core.project_graph_digest import _is_low_signal_source, _is_noise
        by_id = {n["id"]: n for n in nodes}
        top = []
        for node_id, node_degree in sorted(degree.items(), key=lambda x: -x[1]):
            if node_id not in by_id:
                continue
            node = by_id.get(node_id, {})
            if (_is_noise(node_id, node.get("label", node_id))
                    or _is_low_signal_source(node.get("source_file", ""))):
                continue
            top.append((node_id, node_degree))
            if len(top) == 10:
                break

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
