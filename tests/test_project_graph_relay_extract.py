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

from core.project_graph import _RELAY_EXTRACT_SCRIPT, _decode_relay_payload


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


def test_relay_script_reports_unchanged_with_matching_known_map(tmp_path):
    import base64
    import gzip

    project = tmp_path / "proj"
    project.mkdir()
    source = project / "only.py"
    source.write_text("def solo():\n    return 2\n", encoding="utf-8")
    known = {"only.py": int(source.stat().st_mtime)}
    encoded = base64.b64encode(
        gzip.compress(json.dumps(known).encode("utf-8"))).decode("ascii")

    proc = _run_relay_script(project, {"PAWFLOW_GRAPH_KNOWN": encoded})
    assert proc.returncode == 0, proc.stderr
    payload = _decode_relay_payload(proc.stdout)
    assert payload["status"] == "unchanged"
    assert payload["all_files"] == ["only.py"]
