"""Tests for core.project_graph.ProjectGraph."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from core.project_graph import ProjectGraph


@pytest.fixture(autouse=True)
def _clear_singleton_cache():
    """Clear ProjectGraph singleton cache between tests."""
    ProjectGraph._instances.clear()
    yield
    ProjectGraph._instances.clear()


def _make_relay_mock(exec_result: dict):
    """Create a mock relay FS service that returns exec_result from exec()."""
    svc = MagicMock()
    svc.write_file = MagicMock()
    svc.delete_file = MagicMock()
    svc.exec = MagicMock(return_value=exec_result)
    return svc


# ── Build tests ──────────────────────────────────────────────────────


def test_build_from_relay(tmp_path):
    """Successful build with nodes and edges."""
    graph_data = {
        "status": "built",
        "nodes": [
            {"id": "models", "label": "models.py", "file_type": "code",
             "source_file": "models.py", "source_location": "L1"},
            {"id": "models_user", "label": "User", "file_type": "code",
             "source_file": "models.py", "source_location": "L3"},
            {"id": "service", "label": "service.py", "file_type": "code",
             "source_file": "service.py", "source_location": "L1"},
        ],
        "edges": [
            {"source": "models", "target": "models_user", "relation": "contains",
             "confidence": "EXTRACTED", "source_file": "models.py"},
            {"source": "service", "target": "models", "relation": "imports",
             "confidence": "EXTRACTED", "source_file": "service.py"},
        ],
        "total_files": 2,
    }
    svc = _make_relay_mock({"stdout": json.dumps(graph_data), "stderr": "", "returncode": 0})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "built"
    assert result["nodes"] == 3
    assert result["edges"] == 2
    assert pg.has_graph()
    svc.write_file.assert_called_once()
    svc.exec.assert_called_once()
    svc.delete_file.assert_called_once()


def test_build_from_relay_empty(tmp_path):
    """No code files found."""
    svc = _make_relay_mock({
        "stdout": json.dumps({"status": "skipped", "reason": "no code files found"}),
        "stderr": "", "returncode": 0,
    })

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "skipped"
    assert not pg.has_graph()


def test_build_from_relay_script_error(tmp_path):
    """Relay script fails with non-zero exit."""
    svc = _make_relay_mock({"stdout": "", "stderr": "ModuleNotFoundError: No module", "returncode": 1})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "error"
    assert "exit 1" in result["reason"]


def test_build_from_relay_invalid_json(tmp_path):
    """Relay returns non-JSON output."""
    svc = _make_relay_mock({"stdout": "not json at all", "stderr": "", "returncode": 0})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "error"
    assert "Invalid JSON" in result["reason"]


def test_build_from_relay_fallback(tmp_path):
    """Relay returns fallback mode (no tree-sitter)."""
    graph_data = {
        "status": "built_fallback",
        "nodes": [{"id": "main", "label": "main.py", "file_type": "code", "source_file": "main.py"}],
        "edges": [{"source": "main", "target": "os", "relation": "imports", "confidence": "EXTRACTED"}],
        "total_files": 1,
        "error": "tree_sitter_python not installed",
    }
    svc = _make_relay_mock({"stdout": json.dumps(graph_data), "stderr": "", "returncode": 0})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "built_fallback"
    assert pg.has_graph()


# ── Query tests ──────────────────────────────────────────────────────


def test_query_bfs(tmp_path):
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [
            {"id": "auth", "label": "AuthService"},
            {"id": "user", "label": "User"},
            {"id": "session", "label": "Session"},
        ],
        "edges": [
            {"source": "auth", "target": "user", "relation": "uses", "confidence": "EXTRACTED"},
            {"source": "auth", "target": "session", "relation": "creates", "confidence": "EXTRACTED"},
        ],
        "metadata": {},
    }

    results = pg.query("auth")
    assert len(results) == 2

    results = pg.query("nonexistent")
    assert results == []


def test_get_node_fuzzy(tmp_path):
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [{"id": "auth_svc", "label": "AuthService"}],
        "edges": [],
        "metadata": {},
    }

    node = pg.get_node("auth")
    assert node is not None
    assert node["label"] == "AuthService"

    assert pg.get_node("nonexistent") is None


def test_get_report(tmp_path):
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [
            {"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"},
        ],
        "edges": [
            {"source": "a", "target": "b", "relation": "r", "confidence": "EXTRACTED"},
            {"source": "a", "target": "c", "relation": "r", "confidence": "INFERRED"},
        ],
        "metadata": {"root": "/workspace", "total_files": 3},
    }

    report = pg.get_report()
    assert "Nodes: 3" in report
    assert "Edges: 2" in report
    assert "EXTRACTED=1" in report
    assert "A (2 connections)" in report


def test_has_graph(tmp_path):
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    assert not pg.has_graph()
    pg._graph["nodes"].append({"id": "x", "label": "X"})
    assert pg.has_graph()


def test_for_conversation(tmp_path):
    pg1 = ProjectGraph.for_conversation("user1", "conv1")
    pg2 = ProjectGraph.for_conversation("user1", "conv1")
    assert pg1 is pg2

    pg3 = ProjectGraph.for_conversation("user1", "conv2")
    assert pg3 is not pg1


def test_persistence_reload(tmp_path):
    path = str(tmp_path / "graph.json")
    pg = ProjectGraph(path)
    pg._graph = {
        "nodes": [{"id": "a", "label": "A"}],
        "edges": [{"source": "a", "target": "b", "relation": "r"}],
        "metadata": {"root": "."},
    }
    pg._save()

    pg2 = ProjectGraph(path)
    assert len(pg2.nodes) == 1
    assert pg2.nodes[0]["label"] == "A"


def test_load_corrupted_file(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text("not valid json", encoding="utf-8")
    pg = ProjectGraph(str(path))
    assert not pg.has_graph()


# ── Incremental build (mtime-based merge) ─────────────────────────
# These tests exercise the SERVER-SIDE merge logic by mocking the
# relay's exec to return crafted partial-build JSONs. The relay-side
# script (mtime diff) isn't unit-testable from here; integration
# coverage for it lives in test_project_graph_relay_extract.py.


def test_incremental_unchanged_keeps_graph(tmp_path):
    """status=unchanged → keep nodes/edges, refresh mtimes only."""
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [{"id": "a", "label": "A", "source_file": "a.py"}],
        "edges": [{"source": "a", "target": "b", "source_file": "a.py"}],
        "metadata": {"root": ".", "files": {"a.py": 100}},
    }
    pg._save()

    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "unchanged",
            "all_files": ["a.py"],
            "mtimes": {"a.py": 100},
            "total_files": 1,
        }),
        "stderr": "", "returncode": 0,
    })
    result = pg.build_from_relay(svc, ".")
    assert result["status"] == "unchanged"
    assert result["nodes"] == 1
    assert pg.nodes[0]["id"] == "a"
    assert pg._graph["metadata"]["files"] == {"a.py": 100}


def test_incremental_replaces_reparsed_file(tmp_path):
    """status=built with parsed_files → drop+replace nodes/edges from
    those files, keep nodes/edges from unchanged files."""
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [
            {"id": "a_old", "label": "A", "source_file": "a.py"},
            {"id": "b", "label": "B", "source_file": "b.py"},
        ],
        "edges": [
            {"source": "a_old", "target": "b", "source_file": "a.py"},
            {"source": "b", "target": "x", "source_file": "b.py"},
        ],
        "metadata": {"root": ".", "files": {"a.py": 100, "b.py": 200}},
    }
    pg._save()

    # a.py was modified: relay re-parsed and returned new nodes/edges
    # tagged source_file=a.py. b.py unchanged.
    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "built",
            "nodes": [{"id": "a_new", "label": "ANew", "source_file": "a.py"}],
            "edges": [{"source": "a_new", "target": "b", "source_file": "a.py"}],
            "all_files": ["a.py", "b.py"],
            "parsed_files": ["a.py"],
            "removed": [],
            "mtimes": {"a.py": 150, "b.py": 200},
            "total_files": 2,
        }),
        "stderr": "", "returncode": 0,
    })
    result = pg.build_from_relay(svc, ".")
    assert result["status"] == "built"
    assert result["reparsed"] == 1
    assert result["removed"] == 0
    node_ids = {n["id"] for n in pg.nodes}
    assert node_ids == {"a_new", "b"}  # a_old dropped, a_new added, b kept
    edge_targets = {(e["source"], e["target"]) for e in pg.edges}
    assert ("a_new", "b") in edge_targets
    assert ("b", "x") in edge_targets
    assert ("a_old", "b") not in edge_targets


def test_incremental_garbage_collects_removed_files(tmp_path):
    """status=built with `removed` → nodes/edges from those files dropped."""
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [
            {"id": "a", "label": "A", "source_file": "a.py"},
            {"id": "orphan", "label": "O", "source_file": "deleted.py"},
        ],
        "edges": [
            {"source": "a", "target": "orphan", "source_file": "a.py"},
            {"source": "orphan", "target": "a", "source_file": "deleted.py"},
        ],
        "metadata": {"root": ".", "files": {"a.py": 100, "deleted.py": 50}},
    }
    pg._save()

    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "built",
            "nodes": [],   # nothing reparsed
            "edges": [],
            "all_files": ["a.py"],
            "parsed_files": [],
            "removed": ["deleted.py"],
            "mtimes": {"a.py": 100},
            "total_files": 1,
        }),
        "stderr": "", "returncode": 0,
    })
    result = pg.build_from_relay(svc, ".")
    assert result["removed"] == 1
    node_ids = {n["id"] for n in pg.nodes}
    assert node_ids == {"a"}
    # The edge whose source_file was deleted.py is dropped. The edge
    # owned by a.py keeps its `orphan` target reference (target IDs
    # aren't reverse-indexed; agents calling get_node('orphan') just
    # see no node).
    edge_files = {e.get("source_file") for e in pg.edges}
    assert "deleted.py" not in edge_files


def test_incremental_passes_known_mtimes_to_script(tmp_path):
    """build_from_relay forwards the cached files map as PAWFLOW_GRAPH_KNOWN."""
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [], "edges": [],
        "metadata": {"root": ".", "files": {"a.py": 42, "b.py": 99}},
    }
    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "unchanged",
            "all_files": ["a.py", "b.py"],
            "mtimes": {"a.py": 42, "b.py": 99},
            "total_files": 2,
        }),
        "stderr": "", "returncode": 0,
    })
    pg.build_from_relay(svc, ".")
    # exec was called with env containing the JSON-serialised known
    # files map.
    _, kwargs = svc.exec.call_args
    env = kwargs.get("env", {})
    assert "PAWFLOW_GRAPH_KNOWN" in env
    known = json.loads(env["PAWFLOW_GRAPH_KNOWN"])
    assert known == {"a.py": 42, "b.py": 99}


def test_incremental_first_build_sends_empty_known(tmp_path):
    """No prior cache → PAWFLOW_GRAPH_KNOWN={} so the relay treats it
    as a full build."""
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "built",
            "nodes": [{"id": "a", "label": "A", "source_file": "a.py"}],
            "edges": [],
            "all_files": ["a.py"],
            "parsed_files": ["a.py"],
            "removed": [],
            "mtimes": {"a.py": 100},
            "total_files": 1,
        }),
        "stderr": "", "returncode": 0,
    })
    pg.build_from_relay(svc, ".")
    _, kwargs = svc.exec.call_args
    env = kwargs.get("env", {})
    assert env["PAWFLOW_GRAPH_KNOWN"] == "{}"
    # Cache populated for next time
    assert pg._graph["metadata"]["files"] == {"a.py": 100}
