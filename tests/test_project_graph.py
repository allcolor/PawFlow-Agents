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
