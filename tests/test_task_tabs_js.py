"""Run the browser-free behavioural suite for filtered Webchat projections."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "task_tabs_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_filtered_webchat_projection_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)], capture_output=True, text=True,
        cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present_and_wired():
    assert SPEC.is_file()
    assert (ROOT / "tests" / "js" / "dom_stub.js").is_file()
