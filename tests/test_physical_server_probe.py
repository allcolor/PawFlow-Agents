"""Run the production socket/storage acceptance with process-isolated stores."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests import physical_server_probe


def test_production_server_authentication_and_inverse_storage(tmp_path):
    root = Path(physical_server_probe.__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(root / ".pylib"), env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "tests.physical_server_probe",
         "--state-dir", str(tmp_path / "server-data")],
        cwd=tmp_path, env=env, text=True, capture_output=True,
        timeout=40, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed"
    assert report["authentication"] == {"session_denials": 2, "registration_denials": 3}
    assert [(item["name"], item["generation"]) for item in report["rounds"]] == [
        ("alpha", 1), ("beta", 1), ("alpha", 2), ("beta", 2),
    ]
    assert all(item["reads"] == 3 and item["foreign_denials"] == 3
               and item["traversal_denials"] == 2 for item in report["rounds"])
