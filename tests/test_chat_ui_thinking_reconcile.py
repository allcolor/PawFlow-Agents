"""Thinking preview → durable reconciliation in the chat UI and openspace.

The thinking_delta preview is truncated BY DESIGN (the emitter flushes
~250-char word-boundary chunks and never flushes the final fragment); the
durable thinking_content (with msg_id) must SUPERSEDE it everywhere. The bug:
any tool_call/token/message finalized the live block first, so the durable
text created a duplicate block next to the truncated copy (chat detail view,
turn view projection, and the openspace thought bubble all showed it).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_thinking_reconcile_functional():
    """Replay the real event sequence against the real sse_state.js."""
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "thinking_reconcile_harness.mjs")],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sse_state_reconciles_into_finalized_preview_blocks():
    source = _text("tasks/io/chat_ui/sse_state.js")
    # Finalized preview blocks stay reachable for their durable text...
    assert "_pendingThinkingPreviews" in source
    assert "_takePendingThinkingPreview" in source
    # ...matched by exact prefix (the preview is a contiguous slice)...
    assert "finalText.startsWith(pending[i].text)" in source
    # ...and never resurrected across turns.
    assert "if (reason === 'done') delete _pendingThinkingPreviews[aKey];" in source
    assert "reason !== 'done'" in source


def test_sse_handlers_pass_reconcile_flag_for_durable_content():
    source = _text("tasks/io/chat_ui/sse_handlers_a.js")
    # thinking_content carries msg_id (durable, complete); thinking_delta
    # never does (preview). The flag drives the reconcile path.
    assert "renderThinkingContent(data, !!data.msg_id)" in source
    assert "renderThinkingContent(data, false)" in source


def test_openspace_thought_bubble_splices_durable_text():
    source = _text("tasks/io/chat_ui/openspace_runtime.js")
    # The bubble tracks the current block's preview region...
    assert "rec._tbStart" in source and "rec._tbEnd" in source
    # ...and the durable thinking_content splices over it, keeping what
    # was appended after (tool emojis), instead of being lost at the next
    # coalesced flush of the stale thoughtText.
    assert "txt.slice(0, start) + full + txt.slice(end)" in source
    # The answer closing the thought resets the region markers.
    assert "rec._tbStart = -1; rec._tbEnd = -1;" in source
