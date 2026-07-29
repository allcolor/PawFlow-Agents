"""Run the behavioural JS suite for the admin update wait loop.

The loop decides when an update is over and reloads the page. Source-string
assertions cannot check that decision: the version that shipped reloaded on the
first successful /health poll, so an updater that died before stopping anything
looked exactly like a finished update. tests/js/admin_update_spec.js drives the
real function against a stub; this wrapper puts it on the pytest gate.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "admin_update_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_admin_update_wait_loop_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present_and_wired():
    # A missing spec would turn the skip above into silent zero coverage.
    assert SPEC.is_file()
