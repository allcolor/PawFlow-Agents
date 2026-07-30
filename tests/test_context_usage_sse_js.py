"""Run the browser SSE-to-context-gauge lifecycle regression suite."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "js" / "context_usage_sse_spec.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not available to run the JS suite")
def test_context_usage_sse_lifecycle():
    proc = subprocess.run(
        ["node", str(SPEC)], capture_output=True, text=True,
        cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)
