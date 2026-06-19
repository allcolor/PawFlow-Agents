"""Reconstruct the pre-split AgentCoreMixin source for source-scan tests.

tasks/ai/agent_core.py was split (<=800-line rule) into _alc_base/_alc_setup/
_alc_iteration/_alc_llm_turn/_alc_closures1/_alc_closures2: per-call locals route
through a state object ``st.`` and the loop's closures became ``_alc_*`` methods
(``st.X = lambda ...: self._alc_X(st, ...)``). This concatenates the parts and
reverses those mechanical artifacts so structural markers match the original.

Note: tests that slice a contiguous *region* of the old single-file body
(``src[src.index(A):src.index(B)]``) were updated where the split moved A and B
into different files — the concat preserves every marker but not the original
cross-region adjacency.
"""
import re
from pathlib import Path

_FILES = (
    "agent_core.py", "_alc_base.py", "_alc_setup.py", "_alc_iteration.py",
    "_alc_llm_turn.py", "_alc_closures1.py", "_alc_closures2.py",
)


def agent_core_src():
    raw = "".join(
        Path(f"tasks/ai/{_f}").read_text(encoding="utf-8") for _f in _FILES)
    raw = re.sub(r"\bself\._alc_(\w+)\(st(?:, )?", r"_\1(", raw)
    raw = re.sub(r"\bdef _alc_(\w+)\(self, st(?:, )?", r"def _\1(", raw)
    raw = re.sub(r"\breturn _ALC_BREAK\b", "break", raw)
    raw = re.sub(r"\breturn _ALC_CONTINUE\b", "continue", raw)
    raw = re.sub(r"\bst\.", "", raw)
    return raw
