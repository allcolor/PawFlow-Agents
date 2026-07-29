"""Run the behavioural JS suite for the simplified turn view.

The controller in tasks/io/chat_ui/turn_view.js holds real state -- turn
placement, coalesced cues, eviction grouping -- that source-string assertions
cannot check. tests/js/turn_view_spec.js drives it against a local DOM stub;
this wrapper puts it on the pytest gate.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "turn_view_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_simplified_turn_view_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present_and_wired():
    # A missing spec would turn the skip above into silent zero coverage.
    assert SPEC.is_file()
    assert (ROOT / "tests" / "js" / "dom_stub.js").is_file()
