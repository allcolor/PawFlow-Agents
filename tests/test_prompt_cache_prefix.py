"""Prompt-cache prefix stability.

Anthropic (and OpenAI) caching is prefix-based: a single changed byte in the
system block invalidates the system block, the tool definitions and every
message behind it. The cognitive digests are rebuilt from live stores on every
turn, so they must never sit in that prefix on API providers.
"""

import re
from pathlib import Path

from core.cache_diagnostics import CacheBreakDetector

ROOT = Path(__file__).resolve().parents[1]


class _Ctx(dict):
    """Minimal stand-in for the agent state's ctx mapping."""


class _State:
    def __init__(self, blocks, max_ctx=200000, conversation_id="conv-1"):
        self.ctx = _Ctx({"_datetime_str": "2026-07-28 00:00:00",
                         "_dynamic_blocks": blocks,
                         "chars_per_token": 4})
        self._max_ctx = max_ctx
        self.tool_defs = []
        self.conversation_id = conversation_id


def _inject(blocks, messages):
    from tasks.ai.agent_loop import AgentLoopTask
    st = _State(blocks)
    task = AgentLoopTask.__new__(AgentLoopTask)
    return task._alc_inject_dynamic_metadata(st, messages)


def _user_message(text):
    from core.llm_client import LLMMessage
    return LLMMessage(role="user", content=text, conversation_id="conv-1")


# -- digests ride the tail channel -------------------------------------


def test_digests_are_merged_into_the_last_user_message():
    messages = [_user_message("first"), _user_message("latest")]

    result = _inject([("Persistent memory", "Identity: Quentin"),
                      ("Knowledge graph", "A -> B")], messages)

    tail = result[-1].content
    assert "## Persistent memory\nIdentity: Quentin" in tail
    assert "## Knowledge graph\nA -> B" in tail
    # Everything before the last user message must be untouched: it is the
    # part the cache breakpoints protect.
    assert result[0].content == "first"


def test_empty_digest_bodies_are_not_injected():
    messages = [_user_message("latest")]

    result = _inject([("Persistent memory", ""), ("Your diary", None)], messages)

    assert "## Persistent memory" not in result[-1].content
    assert "## Your diary" not in result[-1].content


def test_no_dynamic_blocks_leaves_the_metadata_note_alone():
    messages = [_user_message("latest")]

    result = _inject([], messages)

    assert "Current date/time: 2026-07-28 00:00:00" in result[-1].content
    assert "##" not in result[-1].content


def test_context_builder_routes_digests_by_provider_kind():
    # Structural: the digests must go through _add_digest, which defers to the
    # tail channel on API providers and keeps the old system-prompt placement
    # on CLI providers (where the same text would land in the cold-start
    # bootstrap file and in the prompt handed to the CLI binary).
    src = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")

    assert "st._defer_digests = not st._is_cli_provider" in src
    for title in ("Persistent memory", "Your diary (past observations)",
                  "Knowledge graph", "Project structure"):
        assert f'_add_digest("{title}"' in src
        # None of them may be concatenated into the prefix directly any more.
        assert f'system_prompt += f"\\n\\n## {title}' not in src


def test_the_timestamp_never_enters_the_system_prompt():
    # A per-turn timestamp in the prefix would break the cache on every single
    # turn, unconditionally. It is built in p1 and consumed by the tail channel.
    p1 = (ROOT / "tasks" / "ai" / "_agentctx_p1.py").read_text(encoding="utf-8")
    p3 = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")

    assert re.search(r"st\._datetime_str\s*=\s*datetime\.now\(\)", p1)
    assert "_datetime_str" not in p3


# -- cache break detector ----------------------------------------------


def _call(detector, conv, system, cache_read, tools=None, model="claude"):
    detector.record_pre_call(system, tools or [], model, conversation_id=conv)
    return detector.check_post_call(cache_read, 0, conversation_id=conv)


def test_detector_does_not_diagnose_across_conversations():
    # One LLMClient is shared by every conversation using the same service, so
    # a single slot of state would compare conv B's turn against conv A's and
    # cry "cache break" on every switch.
    d = CacheBreakDetector()

    assert _call(d, "a", "SYS-A", 50000) is None
    assert _call(d, "b", "SYS-B", 100) is None      # first call of b, not a break
    assert _call(d, "a", "SYS-A", 52000) is None    # a still warm
    assert _call(d, "b", "SYS-B", 200) is None      # b still warm at its own scale


def test_detector_reports_a_real_break_with_its_cause():
    d = CacheBreakDetector()
    _call(d, "a", "SYS-A", 50000)

    diagnosis = _call(d, "a", "SYS-A-CHANGED", 0)

    assert diagnosis is not None
    assert "system prompt changed" in diagnosis
    assert "50000" in diagnosis


def test_detector_blames_tools_and_model_separately():
    d = CacheBreakDetector()
    _call(d, "a", "SYS", 40000, tools=[{"name": "read"}])
    diagnosis = _call(d, "a", "SYS", 0, tools=[{"name": "read"}, {"name": "write"}])
    assert "tool definitions changed" in diagnosis
    assert "system prompt changed" not in diagnosis

    d2 = CacheBreakDetector()
    _call(d2, "a", "SYS", 40000, model="opus")
    diagnosis2 = _call(d2, "a", "SYS", 0, model="sonnet")
    assert "model changed" in diagnosis2


def test_detector_state_is_bounded():
    d = CacheBreakDetector()
    for i in range(CacheBreakDetector.MAX_TRACKED + 20):
        _call(d, f"conv-{i}", "SYS", 1000)

    assert len(d._last) <= CacheBreakDetector.MAX_TRACKED


def test_post_call_without_pre_call_is_ignored():
    # A post-call with no matching pre-call carries no prefix to compare, so it
    # must neither diagnose nor overwrite the conversation's recorded state.
    d = CacheBreakDetector()
    _call(d, "a", "SYS", 50000)

    assert d.check_post_call(0, 0, conversation_id="a") is None
    # The next real call still compares against the 50000 recorded above.
    assert _call(d, "a", "SYS", 50000) is None
    assert _call(d, "a", "SYS-CHANGED", 0) is not None
