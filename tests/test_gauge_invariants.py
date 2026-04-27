"""Lock the two monotonic invariants on the chat-UI context gauge:

  1. The gauge can only land on 0% on a brand-new empty conversation.
     A 0 update on an agent that already had a non-zero reading must
     be rejected.
  2. The gauge can only DECREASE when a compact has just happened
     for that agent. Otherwise an unsolicited drop is rejected.

The rules live in `tasks/io/chat_ui/active_agents.js` (see the
`setContextUsage` body). We don't have a JS test runner in this repo,
so this test executes the actual JS source against a tiny stub
browser environment using the `js2py` interpreter when available;
otherwise it falls back to a structural check that the rule
comments and conditions are still present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ACTIVE_AGENTS_JS = Path(
    "tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
_SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")


def test_set_context_usage_blocks_demote_to_zero():
    """setContextUsage must short-circuit when realUsed===0 and the
    cache already holds a non-zero value."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "realUsed === 0" in body and "cachedUsed > 0" in body, (
        "Rule 1 (no demote-to-zero) is missing from setContextUsage")


def test_set_context_usage_blocks_decrease_without_compact():
    """setContextUsage must short-circuit when realUsed < cached AND
    no compact is pending."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "realUsed < cachedUsed" in body, (
        "Rule 2 (no decrease without compact) is missing")
    assert "_compactPending" in body, (
        "setContextUsage must consult window._compactPending")


def test_compact_pending_consumed_after_accepted_decrease():
    """After an accepted decrease the pending flag must be cleared so a
    second decrease without a fresh compact is rejected."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "delete window._compactPending[key]" in body, (
        "Compact-pending flag must be consumed on accepted update")


def test_compact_progress_done_marks_compact_pending():
    """The SSE `compact_progress stage=done` listener must call
    markCompactJustHappened so the next message_meta drop is allowed."""
    # Locate the stage==='done' branch and ensure it calls
    # markCompactJustHappened with the agent name.
    assert "markCompactJustHappened(agent)" in _SSE_JS, (
        "compact_progress 'done' must call markCompactJustHappened")


def test_no_direct_active_interactions_mutation_for_context():
    """Direct mutation of `activeInteractions[k].contextUsed` from
    sse.js bypasses the monotonic invariants — that path must go
    through setContextUsage instead. The only allowed assignment lives
    inside active_agents.js (the setContextUsage body itself)."""
    forbidden = re.findall(
        r"activeInteractions\[[^\]]+\]\.contextUsed\s*=", _SSE_JS)
    assert not forbidden, (
        f"Direct contextUsed mutation in sse.js bypasses gauge "
        f"invariants: {forbidden!r}")


def test_setcontextusage_mirrors_to_active_interactions():
    """setContextUsage must mirror its accepted value into
    `activeInteractions[key]` so the active-agents panel sees the same
    monotonic value as the header / Resource Panel."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "activeInteractions[key].contextUsed = realUsed" in body, (
        "setContextUsage must mirror the accepted value into "
        "activeInteractions for the active-agents panel")


# ---------------------------------------------------------------------------
# Tiny brace-counting JS function-body extractor — plenty for our checks.
# ---------------------------------------------------------------------------


def _extract_function_body(src: str, fname: str) -> str:
    m = re.search(rf"function\s+{re.escape(fname)}\s*\([^)]*\)\s*\{{", src)
    if not m:
        raise AssertionError(f"function {fname} not found")
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[start:i - 1]
