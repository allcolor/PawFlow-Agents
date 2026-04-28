"""Lock the codex mid-turn compact threshold check + the
`trigger_fraction` propagation through the CCCompactDetected handler.

When `compact_threshold_pct` is set, the codex stream MUST trigger a
compact as soon as the running prompt size crosses the threshold,
NOT wait for the final `turn.completed` event. A long tool-heavy turn
would otherwise blow past the limit and grow until codex itself
yields a final assistant message — by which time the next call risks
rejecting an over-budget context.
"""

import re
from pathlib import Path

_CODEX = Path("core/llm_providers/codex.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")


def _item_completed_block() -> str:
    """Source slice from `if is_done:` (item.completed for tools) up
    to the next sibling block at the same indent."""
    start = _CODEX.index("if is_done:")
    # The next branch in the parser is the hosted-tool-items block.
    end = _CODEX.index(
        "# Hosted Responses-API tool items (web_search, view_image,",
        start)
    return _CODEX[start:end]


def test_mid_turn_compact_check_present_at_item_completed():
    block = _item_completed_block()
    assert "_compact_pending[0]" in block, (
        "item.completed handler must consult `_compact_pending` to"
        " avoid double-firing the mid-turn compact")
    assert "compact_threshold_pct" in block, (
        "item.completed handler must read compact_threshold_pct"
        " from the agent client config")
    # The actual gate: prompt_tokens >= int(_ctx_max_mid * _cthp_mid / 100)
    pattern = re.compile(
        r"prompt_tokens >= int\(\s*_ctx_max_mid \* _cthp_mid / 100\)")
    assert pattern.search(block), (
        "item.completed handler missing the"
        " `prompt_tokens >= max * pct/100` mid-turn gate")


def test_mid_turn_compact_kills_codex_to_break_loop():
    """Setting `_compact_pending` alone is not enough — codex would
    keep streaming until natural end-of-turn. The handler must also
    `_kill_codex_hard(proc)` so the dispatch loop exits and the
    post-loop branch raises CCCompactDetected."""
    block = _item_completed_block()
    assert "_kill_codex_hard(proc)" in block, (
        "mid-turn compact must kill the in-flight codex CLI to break"
        " the dispatch loop — otherwise the threshold cross is logged"
        " but codex keeps generating tools.")
    assert "break" in block, (
        "mid-turn compact branch must `break` out of the dispatch"
        " loop after killing codex")


def test_ccd_handler_propagates_trigger_fraction():
    """agent_core.CCCompactDetected handler at L~1208 must pass the
    user-configured `compact_threshold_pct` as `trigger_fraction` to
    `_compact()`, otherwise the [compact] log line shows the 0.8
    default and operators get misled when debugging."""
    # Locate the handler body.
    h_start = _AGENT_CORE.index("PawFlow compact: %d → %d messages")
    # Walk backwards to find the matching `messages = list(self._compact(`
    # call.
    handler = _AGENT_CORE[max(0, h_start - 4000):h_start]
    assert "trigger_fraction=_ccd_trigger_frac" in handler, (
        "CCCompactDetected handler must pass the per-service"
        " trigger_fraction to _compact() (was using the 0.8 default,"
        " producing a misleading [compact] log on every fired threshold)")
    assert "compact_threshold_pct" in handler, (
        "CCCompactDetected handler must read compact_threshold_pct"
        " from the agent client config")
