"""Run the behavioural JS suite for the conversation load path.

tasks/io/chat_ui/conversations.js owns pagination, gap recovery and the
handover to the turn view on every conversation switch. Those were covered
only by the browser tests in tests/test_webchat_durable_state_behavior.py,
which skip wherever headless Chromium renders nothing -- the GitHub runners
among them. tests/js/conversations_spec.js drives the same invariants against
the local DOM stub; this wrapper puts it on the pytest gate.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "conversations_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_conversation_load_path_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present_and_wired():
    # A missing spec would turn the skip above into silent zero coverage.
    assert SPEC.is_file()
    assert (ROOT / "tests" / "js" / "dom_stub.js").is_file()
