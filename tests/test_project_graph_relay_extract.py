"""The relay extraction script must run against the real graphify API.

Regression: the script called extract(batch, root=..., parallel=..., 
max_workers=...) but graphify's extract() only accepts the path list — every
relay build died with TypeError and the graph silently stayed stale.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from core.project_graph import ProjectGraph, _RELAY_EXTRACT_SCRIPT, _decode_relay_payload


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_relay_script(project_root: Path, extra_env=None):
    env = dict(os.environ)
    env["PAWFLOW_GRAPH_ROOT"] = str(project_root)
    # The script only imports graphify from /opt/pawflow or this variable.
    env["PAWFLOW_RELAY_CODE_DIR"] = str(REPO_ROOT / "core")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", _RELAY_EXTRACT_SCRIPT],
        capture_output=True, text=True, env=env, timeout=120,
        cwd=str(project_root))


def test_relay_script_builds_graph_from_real_extract(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "alpha.py").write_text(
        "import beta\n\n\nclass Alpha:\n    def run(self):\n"
        "        return beta.helper()\n", encoding="utf-8")
    (project / "beta.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8")

    proc = _run_relay_script(project)
    assert proc.returncode == 0, proc.stderr
    payload = _decode_relay_payload(proc.stdout)

    assert payload["status"] == "built"
    assert sorted(payload["all_files"]) == ["alpha.py", "beta.py"]
    assert sorted(payload["parsed_files"]) == ["alpha.py", "beta.py"]
    labels = {node["label"] for node in payload["nodes"]}
    assert "Alpha" in labels
    assert payload["edges"], "cross-file extraction produced no edges"


def test_batched_graph_resolves_cross_file_edges_after_incremental_merge(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "alpha.py").write_text(
        "from beta import Beta\n\nclass Alpha:\n    pass\n", encoding="utf-8")
    target = project / "beta.py"
    target.write_text("class Beta:\n    pass\n", encoding="utf-8")
    for index in range(31):
        (project / f"filler_{index}.py").write_text("# filler\n", encoding="utf-8")

    def relay_exec(*args, **kwargs):
        proc = _run_relay_script(project, kwargs["env"])
        return {"stdout": proc.stdout, "stderr": proc.stderr,
                "returncode": proc.returncode}

    relay = MagicMock()
    relay.exec.side_effect = relay_exec
    graph = ProjectGraph(str(tmp_path / "graph.json"))

    def rebuild():
        result = graph.build_from_relay(relay, str(project))
        assert result["status"] == "built", result
        ids = [node["id"] for node in graph.nodes]
        assert len(ids) == len(set(ids))
        assert all(edge[key] in ids for edge in graph.edges
                   for key in ("source", "target"))
        return result

    def uses_target():
        edges = [edge for edge in graph.edges
                 if edge["relation"] == "uses" and edge["source_file"] == "alpha.py"]
        assert len(edges) == 1
        return edges[0]["target"]

    rebuild()
    expected = next(node["id"] for node in graph.nodes
                    if node["label"] == "Beta" and node["source_file"] == "beta.py")
    assert uses_target() == expected
    assert any(edge["relation"] == "imports_from"
               and edge["target"] == "source:beta.py:beta" for edge in graph.edges)

    target.write_text("class Beta:\n    updated = True\n", encoding="utf-8")
    assert rebuild()["reparsed"] == 1
    assert uses_target() == expected
    target.unlink()
    rebuild()
    assert uses_target().startswith("external:")
    target.write_text("class Beta:\n    pass\n", encoding="utf-8")
    rebuild()
    assert uses_target() == expected

    (project / "duplicate").mkdir()
    (project / "duplicate" / "beta.py").write_text(
        "class Beta:\n    pass\n", encoding="utf-8")
    rebuild()
    assert uses_target().startswith("external:")


def test_relay_script_reports_unchanged_with_matching_known_map(tmp_path):
    import base64
    import gzip

    project = tmp_path / "proj"
    project.mkdir()
    source = project / "only.py"
    source.write_text("def solo():\n    return 2\n", encoding="utf-8")
    info = source.stat()
    known = {"only.py": f"{info.st_mtime_ns}:{info.st_size}"}
    encoded = base64.b64encode(
        gzip.compress(json.dumps(known).encode("utf-8"))).decode("ascii")

    proc = _run_relay_script(project, {"PAWFLOW_GRAPH_KNOWN": encoded})
    assert proc.returncode == 0, proc.stderr
    payload = _decode_relay_payload(proc.stdout)
    assert payload["status"] == "unchanged"
    assert payload["all_files"] == ["only.py"]


def test_relay_script_scopes_duplicate_symbol_ids_by_source_path(tmp_path):
    project = tmp_path / "proj"
    (project / "left").mkdir(parents=True)
    (project / "right").mkdir(parents=True)
    (project / "left" / "worker.py").write_text(
        "class LeftWorker:\n    pass\n", encoding="utf-8")
    (project / "right" / "worker.py").write_text(
        "class RightWorker:\n    pass\n", encoding="utf-8")

    proc = _run_relay_script(project)
    assert proc.returncode == 0, proc.stderr
    payload = _decode_relay_payload(proc.stdout)
    worker_files = [
        node for node in payload["nodes"] if node["label"] == "worker.py"
    ]

    assert {node["source_file"] for node in worker_files} == {
        "left/worker.py", "right/worker.py",
    }
    assert len({node["id"] for node in worker_files}) == 2
    assert all(node["id"].startswith("source:") for node in worker_files)


def test_relay_script_skips_generated_and_vendor_javascript(tmp_path):
    project = tmp_path / "proj"
    (project / "vendor").mkdir(parents=True)
    (project / "assets").mkdir(parents=True)
    (project / "app.js").write_text("function app() {}\n", encoding="utf-8")
    (project / "library.min.js").write_text("function minified(){}", encoding="utf-8")
    (project / "vendor" / "library.js").write_text(
        "function vendored() {}\n", encoding="utf-8")
    (project / "assets" / "index-AbCdEf123.js").write_text(
        "function bundled() {}\n", encoding="utf-8")

    proc = _run_relay_script(project)
    assert proc.returncode == 0, proc.stderr
    payload = _decode_relay_payload(proc.stdout)

    assert payload["all_files"] == ["app.js"]
    assert {node["source_file"] for node in payload["nodes"] if node["source_file"]} == {
        "app.js",
    }
