"""Run the behavioural JS suite for the chat message source badge.

A delegate reply (source.type = 'agent_delegate') that reclaims the live token
bubble must keep the "Agent via service" header; see
tests/js/source_badge_spec.js.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "source_badge_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_source_badge_rendering():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present():
    # A missing spec would turn the skip above into silent zero coverage.
    assert SPEC.is_file()
