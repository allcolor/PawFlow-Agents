"""Tests for core.project_graph.ProjectGraph."""

import base64
import gzip
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from core.project_graph import ProjectGraph, _RELAY_PAYLOAD_PREFIX
from core._relay_naming import _prepare_relay_code_dir


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
    svc.exec.assert_called_once()
    svc.write_file.assert_not_called()
    svc.delete_file.assert_not_called()
    command = svc.exec.call_args.args[1]
    prefix = "python3 -c \"import base64;exec(base64.b64decode('"
    suffix = "'))\""
    assert command.startswith(prefix)
    assert command.endswith(suffix)
    encoded_script = command[len(prefix):-len(suffix)]
    script = base64.b64decode(encoded_script)
    assert b"PAWFLOW_GRAPH_ROOT" in script
    assert b"parallel=False" in script
    assert b"max_workers=1" in script
    assert b"FULL_BATCH_MAX_FILES = 32" in script
    assert b"FULL_BATCH_MAX_BYTES = 8 * 1024 * 1024" in script
    assert b"gzip.open" in script
    assert b"from graphify.build import build" not in script


def test_build_from_relay_decodes_compressed_graph_payload(tmp_path):
    graph_data = {
        "status": "built",
        "nodes": [{"id": "a", "label": "A", "source_file": "a.py"}],
        "edges": [],
        "parsed_files": ["a.py"],
        "removed": [],
        "mtimes": {"a.py": 1},
        "total_files": 1,
    }
    compressed = gzip.compress(
        json.dumps(graph_data, separators=(",", ":")).encode())
    stdout = _RELAY_PAYLOAD_PREFIX + base64.b64encode(compressed).decode()
    svc = _make_relay_mock(
        {"stdout": stdout, "stderr": "", "returncode": 0})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result == {
        "status": "built", "nodes": 1, "edges": 0, "files": 1,
        "reparsed": 1, "removed": 0,
    }
    assert pg.nodes[0]["id"] == "a"


def test_build_from_relay_rejects_truncated_compressed_payload(tmp_path):
    svc = _make_relay_mock({
        "stdout": _RELAY_PAYLOAD_PREFIX + "not-base64... (truncated)",
        "stderr": "", "returncode": 0,
    })

    result = ProjectGraph(str(tmp_path / "graph.json")).build_from_relay(
        svc, ".")

    assert result["status"] == "error"
    assert "compressed project graph payload" in result["reason"]


def test_build_from_relay_creates_no_helper_file_on_failure(tmp_path):
    """An execution failure cannot leave a helper file in the source tree."""
    svc = _make_relay_mock({})
    svc.exec.side_effect = RuntimeError("relay failed")

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result == {"status": "error", "reason": "relay failed"}
    svc.write_file.assert_not_called()
    svc.delete_file.assert_not_called()


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


def test_managed_relay_runtime_stages_importable_graphify(tmp_path):
    code_dir = _prepare_relay_code_dir(tmp_path / "relay")

    assert (code_dir / "graphify" / "extract.py").is_file()
    env = dict(os.environ, PYTHONPATH=str(code_dir))
    result = subprocess.run(
        [sys.executable, "-c", "from graphify.extract import extract"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_build_from_relay_invalid_json(tmp_path):
    """Relay returns non-JSON output."""
    svc = _make_relay_mock({"stdout": "not json at all", "stderr": "", "returncode": 0})

    pg = ProjectGraph(str(tmp_path / "graph.json"))
    result = pg.build_from_relay(svc, ".")

    assert result["status"] == "error"
    assert "Invalid JSON" in result["reason"]



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


def test_for_relay_is_project_scoped(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "GRAPHS_DIR", tmp_path / "graphs")
    pg1 = ProjectGraph.for_relay("user1", "relay1")
    pg2 = ProjectGraph.for_relay("user1", "relay1")
    assert pg1 is pg2

    pg3 = ProjectGraph.for_relay("user1", "relay2")
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


def test_root_change_discards_old_graph_and_sends_empty_known_map(tmp_path):
    pg = ProjectGraph(str(tmp_path / "graph.json"))
    pg._graph = {
        "nodes": [{"id": "old", "label": "Old", "source_file": "old.py"}],
        "edges": [{"source": "old", "target": "gone", "source_file": "old.py"}],
        "metadata": {"root": ".", "files": {"old.py": 100}},
    }
    svc = _make_relay_mock({
        "stdout": json.dumps({
            "status": "built",
            "nodes": [{"id": "new", "label": "New", "source_file": "new.py"}],
            "edges": [],
            "all_files": ["new.py"],
            "parsed_files": ["new.py"],
            "removed": [],
            "mtimes": {"new.py": 200},
            "total_files": 1,
        }),
        "stderr": "", "returncode": 0,
    })

    result = pg.build_from_relay(svc, "new-root")

    assert result["status"] == "built"
    assert [node["id"] for node in pg.nodes] == ["new"]
    assert pg.edges == []
    assert pg._graph["metadata"]["root"] == "new-root"
    assert json.loads(svc.exec.call_args.kwargs["env"]["PAWFLOW_GRAPH_KNOWN"]) == {}


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
