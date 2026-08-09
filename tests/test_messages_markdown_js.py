"""Run the behavioural JS suite for webchat Markdown FileStore links."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "messages_markdown_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_messages_markdown_filestore_links():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)


def test_js_suite_is_present():
    assert SPEC.is_file()
