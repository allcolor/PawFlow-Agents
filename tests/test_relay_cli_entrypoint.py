"""Exercise manager command dispatch through the installed module entry point."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", ["server", "workspace", "physical", "start",
                                     "status", "cleanup", "verify", "key"])
def test_manager_command_help_through_module(command, tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "pawflow_relay", "--json", command, "--help"],
        cwd=root,
        env={**os.environ, "PAWFLOW_RELAY_HOME": str(tmp_path / "relay"),
             "PYTHONPATH": os.pathsep.join([str(root), str(root / ".pylib")])},
        text=True, capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f" {command}" in result.stdout
    assert "--docker-cpus" not in result.stdout
