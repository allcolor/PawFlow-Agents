"""Tests for core.project_graph.ProjectGraph."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Realistic Python file contents for tree-sitter parsing ────────────────────

FILE_MODELS_PY = b"""\
import json
from typing import Optional, List

class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def to_dict(self) -> dict:
        return {"name": self.name, "email": self.email}

class Session:
    def __init__(self, user: User, token: str):
        self.user = user
        self.token = token

    def is_valid(self) -> bool:
        return bool(self.token)
"""

FILE_SERVICE_PY = b"""\
from models import User, Session

class AuthService:
    def __init__(self):
        self._sessions = {}

    def login(self, user: User, password: str) -> Session:
        token = self._generate_token()
        session = Session(user, token)
        self._sessions[user.name] = session
        return session

    def _generate_token(self) -> str:
        import secrets
        return secrets.token_hex(32)

    def logout(self, user: User):
        self._sessions.pop(user.name, None)
"""

FILE_UTILS_PY = b"""\
import os
import hashlib

def hash_string(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def read_config(path: str) -> dict:
    import json
    with open(path) as f:
        return json.load(f)
"""

# Fake extraction result that graphify.extract would return
FAKE_EXTRACTION = {
    "nodes": [
        {"id": "user", "label": "User", "file_type": "code",
         "source_file": "models.py", "source_location": "L4"},
        {"id": "session", "label": "Session", "file_type": "code",
         "source_file": "models.py", "source_location": "L13"},
        {"id": "authservice", "label": "AuthService", "file_type": "code",
         "source_file": "service.py", "source_location": "L3"},
        {"id": "hash_string", "label": "hash_string()", "file_type": "code",
         "source_file": "utils.py", "source_location": "L4"},
        {"id": "read_config", "label": "read_config()", "file_type": "code",
         "source_file": "utils.py", "source_location": "L7"},
    ],
    "edges": [
        {"source": "authservice", "target": "user", "relation": "imports",
         "confidence": "EXTRACTED", "source_file": "service.py"},
        {"source": "authservice", "target": "session", "relation": "imports",
         "confidence": "EXTRACTED", "source_file": "service.py"},
        {"source": "session", "target": "user", "relation": "uses",
         "confidence": "INFERRED", "source_file": "models.py"},
    ],
    "input_tokens": 0,
    "output_tokens": 0,
}


def _make_fake_nx_graph():
    """Build a networkx Graph matching FAKE_EXTRACTION."""
    import networkx as nx
    G = nx.Graph()
    for n in FAKE_EXTRACTION["nodes"]:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for e in FAKE_EXTRACTION["edges"]:
        src, tgt = e["source"], e["target"]
        attrs = {k: v for k, v in e.items() if k not in ("source", "target")}
        G.add_edge(src, tgt, **attrs)
    return G


def _make_mock_fs(files=None, fail_reads=None):
    """Create a mock filesystem service.

    files: dict mapping relative_path -> bytes content
    fail_reads: set of paths that raise on read_file
    """
    if files is None:
        files = {
            "models.py": FILE_MODELS_PY,
            "service.py": FILE_SERVICE_PY,
            "utils.py": FILE_UTILS_PY,
        }
    if fail_reads is None:
        fail_reads = set()

    svc = MagicMock()

    def search(root, pattern, recursive=True):
        ext = pattern.lstrip("*")
        return [p for p in files if p.endswith(ext)]

    def read_file(path):
        if path in fail_reads:
            raise IOError(f"Cannot read {path}")
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    svc.search = MagicMock(side_effect=search)
    svc.read_file = MagicMock(side_effect=read_file)
    return svc


@pytest.fixture(autouse=True)
def _clear_singleton_cache():
    """Clear ProjectGraph._instances between tests."""
    from core.project_graph import ProjectGraph
    ProjectGraph._instances.clear()
    yield
    ProjectGraph._instances.clear()


# ── Test: build_from_relay with 3 Python files ───────────────────────────────

@patch("core.graphify.build.build", return_value=_make_fake_nx_graph())
@patch("core.graphify.extract.extract", return_value=FAKE_EXTRACTION)
def test_build_from_relay(mock_extract, mock_build, tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    fs = _make_mock_fs()

    result = pg.build_from_relay(fs, root_path=".")

    assert result["status"] == "built"
    assert result["nodes"] == 5
    assert result["edges"] == 3
    assert result["files"] == 3

    # Verify nodes/edges populated
    assert len(pg.nodes) == 5
    assert len(pg.edges) == 3

    # Verify search was called for code extensions
    assert fs.search.call_count > 0

    # Verify read_file called for each discovered file
    assert fs.read_file.call_count == 3

    # Verify persistence
    assert graph_file.exists()
    saved = json.loads(graph_file.read_text(encoding="utf-8"))
    assert len(saved["nodes"]) == 5
    assert saved["metadata"]["total_files"] == 3


# ── Test: build_from_relay empty (no code files) ─────────────────────────────

def test_build_from_relay_empty(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    fs = _make_mock_fs(files={})

    result = pg.build_from_relay(fs, root_path=".")

    assert result["status"] == "skipped"
    assert "no code files" in result["reason"]
    assert not pg.has_graph()


# ── Test: build_from_relay read errors (partial results) ──────────────────────

@patch("core.graphify.build.build", return_value=_make_fake_nx_graph())
@patch("core.graphify.extract.extract", return_value=FAKE_EXTRACTION)
def test_build_from_relay_read_errors(mock_extract, mock_build, tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    fs = _make_mock_fs(fail_reads={"service.py"})

    result = pg.build_from_relay(fs, root_path=".")

    # Should still succeed with partial results (2 of 3 files fetched)
    assert result["status"] == "built"
    assert result["files"] == 2


# ── Test: build_from_relay all reads fail ─────────────────────────────────────

def test_build_from_relay_all_reads_fail(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    fs = _make_mock_fs(fail_reads={"models.py", "service.py", "utils.py"})

    result = pg.build_from_relay(fs, root_path=".")

    assert result["status"] == "error"
    assert "could not read" in result["reason"]


# ── Test: query with BFS traversal ───────────────────────────────────────────

def test_query_bfs(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))

    # Manually set graph data
    pg._graph = {
        "nodes": FAKE_EXTRACTION["nodes"],
        "edges": FAKE_EXTRACTION["edges"],
        "metadata": {},
    }

    # Query for "auth" should match AuthService, then BFS to neighbors
    results = pg.query("auth")
    assert len(results) > 0
    # AuthService connects to user and session via imports
    sources_and_targets = set()
    for e in results:
        sources_and_targets.add(e["source"])
        sources_and_targets.add(e["target"])
    assert "authservice" in sources_and_targets

    # Query for something not in graph
    results = pg.query("nonexistent_keyword")
    assert results == []


# ── Test: query depth limit ──────────────────────────────────────────────────

def test_query_depth_limit(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))

    # Chain: A -> B -> C -> D -> E
    pg._graph = {
        "nodes": [
            {"id": "a", "label": "A_target"},
            {"id": "b", "label": "B_node"},
            {"id": "c", "label": "C_node"},
            {"id": "d", "label": "D_node"},
            {"id": "e", "label": "E_node"},
        ],
        "edges": [
            {"source": "a", "target": "b", "relation": "uses", "confidence": "EXTRACTED"},
            {"source": "b", "target": "c", "relation": "uses", "confidence": "EXTRACTED"},
            {"source": "c", "target": "d", "relation": "uses", "confidence": "EXTRACTED"},
            {"source": "d", "target": "e", "relation": "uses", "confidence": "EXTRACTED"},
        ],
        "metadata": {},
    }

    # depth=1 should only reach B from A
    results = pg.query("a_target", depth=1)
    reached = set()
    for e in results:
        reached.add(e["source"])
        reached.add(e["target"])
    assert "a" in reached
    assert "b" in reached
    assert "d" not in reached
    assert "e" not in reached


# ── Test: get_node fuzzy label matching ───────────────────────────────────────

def test_get_node_fuzzy(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    pg._graph = {
        "nodes": FAKE_EXTRACTION["nodes"],
        "edges": FAKE_EXTRACTION["edges"],
        "metadata": {},
    }

    # Fuzzy match on label
    node = pg.get_node("auth")
    assert node is not None
    assert node["label"] == "AuthService"
    assert "neighbors" in node
    assert "neighbor_edges" in node
    # AuthService has 2 edges (to user and session)
    assert node["neighbors"] == 2

    # Fuzzy match on id
    node = pg.get_node("hash_string")
    assert node is not None
    assert node["label"] == "hash_string()"

    # No match
    node = pg.get_node("zzz_no_match_zzz")
    assert node is None


# ── Test: get_report format ───────────────────────────────────────────────────

def test_get_report(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))
    pg._graph = {
        "nodes": FAKE_EXTRACTION["nodes"],
        "edges": FAKE_EXTRACTION["edges"],
        "metadata": {"root": ".", "total_files": 3},
    }

    report = pg.get_report()
    assert "Project Graph: ." in report
    assert "Nodes: 5" in report
    assert "Edges: 3" in report
    assert "Files: 3" in report
    assert "EXTRACTED" in report
    assert "INFERRED" in report
    assert "God nodes" in report
    # authservice has highest degree (2 edges as source)
    assert "AuthService" in report


# ── Test: has_graph ───────────────────────────────────────────────────────────

def test_has_graph(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))

    assert pg.has_graph() is False

    pg._graph["nodes"] = [{"id": "x", "label": "X"}]
    assert pg.has_graph() is True


# ── Test: for_conversation path generation and singleton ──────────────────────

def test_for_conversation(tmp_path):
    from core.project_graph import ProjectGraph

    # Patch _DEFAULT_DIR to use tmp_path
    with patch("core.project_graph._DEFAULT_DIR", str(tmp_path)):
        pg1 = ProjectGraph.for_conversation("user/one", "conv:123")
        pg2 = ProjectGraph.for_conversation("user/one", "conv:123")

        # Singleton: same instance
        assert pg1 is pg2

        # Path sanitization: slashes and colons replaced
        assert "user_one" in str(pg1._path)
        assert "conv_123" in str(pg1._path)
        assert str(pg1._path).endswith("graph.json")

        # Different conv returns different instance
        pg3 = ProjectGraph.for_conversation("user/one", "conv:456")
        assert pg3 is not pg1


# ── Test: persistence — build, reload from disk ──────────────────────────────

@patch("core.graphify.build.build", return_value=_make_fake_nx_graph())
@patch("core.graphify.extract.extract", return_value=FAKE_EXTRACTION)
def test_persistence_reload(mock_extract, mock_build, tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"

    # Build and save
    pg1 = ProjectGraph(str(graph_file))
    fs = _make_mock_fs()
    result = pg1.build_from_relay(fs, root_path="/project")
    assert result["status"] == "built"

    # Create a new instance from the same file (simulates server restart)
    pg2 = ProjectGraph(str(graph_file))

    assert pg2.has_graph()
    assert len(pg2.nodes) == len(pg1.nodes)
    assert len(pg2.edges) == len(pg1.edges)

    # Verify node data integrity
    labels = {n["label"] for n in pg2.nodes}
    assert "User" in labels
    assert "AuthService" in labels
    assert "hash_string()" in labels

    # Verify edge data integrity
    relations = {e["relation"] for e in pg2.edges}
    assert "imports" in relations
    assert "uses" in relations

    # Verify metadata survived
    assert pg2._graph["metadata"]["root"] == "/project"
    assert pg2._graph["metadata"]["fetched_files"] == 3


# ── Test: corrupted graph file loads gracefully ──────────────────────────────

def test_load_corrupted_file(tmp_path):
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    graph_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

    pg = ProjectGraph(str(graph_file))
    # Should fall back to empty graph
    assert pg.has_graph() is False
    assert pg.nodes == []
    assert pg.edges == []


# ── Test: build_from_relay string content (not bytes) ────────────────────────

@patch("core.graphify.build.build", return_value=_make_fake_nx_graph())
@patch("core.graphify.extract.extract", return_value=FAKE_EXTRACTION)
def test_build_from_relay_string_content(mock_extract, mock_build, tmp_path):
    """read_file returning str instead of bytes should still work."""
    from core.project_graph import ProjectGraph

    graph_file = tmp_path / "graph.json"
    pg = ProjectGraph(str(graph_file))

    # Mock FS returning string instead of bytes
    files = {"app.py": "class App:\n    pass\n"}
    fs = _make_mock_fs(files=files)

    result = pg.build_from_relay(fs, root_path=".")
    assert result["status"] == "built"
