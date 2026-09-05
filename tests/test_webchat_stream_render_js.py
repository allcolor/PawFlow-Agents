"""Run the real stream handlers with deterministic frame and timer scheduling."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize(("script", "expected"), [
    ("stream_render_spec.js", "10 stream-render tests passed"),
    ("live_window_spec.js", "5 live-window tests passed"),
])
def test_webchat_stream_render_js(script, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/js/" + script], cwd=root,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
