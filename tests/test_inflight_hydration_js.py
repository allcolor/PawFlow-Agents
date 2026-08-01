"""Run the behavioural JS suite for in-flight row hydration.

The server half (ToolRelayService.inflight_snapshot -> load_history stamping
`live`) is covered by tests/test_inflight_row_hydration.py. This one holds the
other half: the renderer must treat a replayed row marked live exactly like a
streamed one -- pending bullet, BG and Kill -- because nothing else in the row
says it is still running.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "inflight_hydration_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_inflight_row_rendering():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present_and_wired():
    # A missing spec would turn the skip above into silent zero coverage.
    assert SPEC.is_file()
    assert (ROOT / "tests" / "js" / "dom_stub.js").is_file()
