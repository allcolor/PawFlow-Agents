"""Run the production socket/storage acceptance with process-isolated stores."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


@pytest.mark.skipif(os.name != "posix", reason="The acceptance job uses a Linux runner")
@pytest.mark.parametrize("exit_code", [0, 19])
def test_mounted_ci_preserves_process_status_and_log(tmp_path, exit_code):
    root = Path(physical_server_probe.__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/physical-runtime-acceptance.yml").read_text())
    step = next(item for item in workflow["jobs"]["static-runtime"]["steps"]
                if item.get("name") == "Exercise production authentication and storage with mounted workers")
    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\nprintf '%s\\n' fixture-log\nexit " + str(exit_code) + "\n")
    binary.chmod(0o755)
    (tmp_path / "acceptance-output").mkdir()
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["RUNNER_TEMP"] = str(tmp_path)
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", step["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=10, check=False,
    )
    assert (tmp_path / "acceptance-output/server-listener.log").read_text() == "fixture-log\n"
    assert result.returncode == exit_code


@pytest.mark.parametrize("kind", ["missing", "readable", "directory"])
def test_mounted_read_probe_accepts_only_filesystem_denials(tmp_path, kind):
    target = tmp_path / "foreign"
    if kind == "readable":
        target.write_bytes(b"foreign bytes")
    elif kind == "directory":
        target.mkdir()
    result = subprocess.run(
        [sys.executable, "-c", physical_server_probe.DENIED_READ_SCRIPT, str(target)],
        text=True, capture_output=True, timeout=10, check=False,
    )
    if kind == "missing":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [2]
    else:
        assert result.returncode != 0
