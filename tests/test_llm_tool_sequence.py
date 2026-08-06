"""Sequence-repair tests for core/llm_tool_sequence.py.

Strict OpenAI providers 400 any context where an assistant tool_calls block
is not immediately followed by its tool results. These tests cover the
disorders a cancel/compact/rewind can leave behind.
"""

import unittest

from core.llm_client import LLMMessage, LLMToolCall
from core.llm_tool_sequence import repair_tool_sequence


def _assistant(calls, content=""):
    return LLMMessage(
        role="assistant", content=content,
        tool_calls=[LLMToolCall(id=cid, name="tool", arguments={}) for cid in calls],
        conversation_id="c1")


def _tool(cid, content="done"):
    return LLMMessage(
        role="tool", content=content, tool_call_id=cid, conversation_id="c1")


def _roles(messages):
    return [m.role for m in messages]


class TestRepairToolSequence(unittest.TestCase):

    def test_perfect_sequence_unchanged(self):
        seq = [_assistant(["a"]), _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is False
        assert repaired == seq

    def test_result_before_assistant_is_moved_after(self):
        # The old repair left the result in place AND synthesized a duplicate,
        # producing a tool message before its assistant: a provider 400.
        seq = [_tool("a"), _assistant(["a"])]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert _roles(repaired) == ["assistant", "tool"]
        assert repaired[1] is seq[0]

    def test_result_interleaved_with_user_message_is_moved_after(self):
        seq = [_assistant(["a"]), _user("go"), _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert _roles(repaired) == ["assistant", "tool", "user"]

    def test_inter_assistant_disorder_reordered(self):
        # assistant1(a), assistant2(b), tool(a), tool(b): each assistant must
        # be immediately followed by its own results.
        seq = [_assistant(["a"]), _assistant(["b"]), _tool("a"), _tool("b")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert _roles(repaired) == ["assistant", "tool", "assistant", "tool"]
        assert repaired[1] is seq[2]
        assert repaired[3] is seq[3]

    def test_duplicate_results_keep_only_earliest(self):
        seq = [_assistant(["a"]), _tool("a"), _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert _roles(repaired) == ["assistant", "tool"]
        assert repaired[1] is seq[1]

    def test_orphan_result_dropped(self):
        seq = [_tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert repaired == []

    def test_unanswered_call_gets_synthetic_result(self):
        seq = [_assistant(["a"])]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert _roles(repaired) == ["assistant", "tool"]
        assert repaired[1].tool_call_id == "a"
        assert "unavailable" in repaired[1].content

    def test_results_placed_in_call_order(self):
        # Results exist but in reverse call order: the repair must order them
        # as the assistant declared its calls.
        seq = [_assistant(["a", "b"]), _tool("b"), _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert [m.tool_call_id for m in repaired[1:3]] == ["a", "b"]

    def test_unaddressable_call_stripped_and_message_dropped(self):
        bad = LLMMessage(
            role="assistant", content="",
            tool_calls=[LLMToolCall(id="", name="tool", arguments={})],
            conversation_id="c1")
        repaired, changed = repair_tool_sequence([bad], "c1")
        assert changed is True
        assert repaired == []

    def test_unaddressable_call_stripped_keeps_answered_call(self):
        bad = LLMToolCall(id="", name="tool", arguments={})
        good = LLMToolCall(id="a", name="tool", arguments={})
        msg = LLMMessage(
            role="assistant", content="",
            tool_calls=[bad, good], conversation_id="c1")
        seq = [msg, _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert [m.role for m in repaired] == ["assistant", "tool"]
        assert [c.id for c in repaired[0].tool_calls] == ["a"]

    def test_result_claimed_by_earlier_assistant_not_duplicated(self):
        # Two assistant blocks claiming the same call id: only the first gets
        # the real result, the second gets a synthetic one.
        seq = [_assistant(["a"]), _assistant(["a"]), _tool("a")]
        repaired, changed = repair_tool_sequence(seq, "c1")
        assert changed is True
        assert [m.role for m in repaired] == ["assistant", "tool", "assistant", "tool"]
        assert repaired[1] is seq[2]
        assert repaired[3] is not seq[2]
        assert "unavailable" in repaired[3].content

    def test_input_list_not_mutated(self):
        seq = [_tool("a"), _assistant(["a"])]
        snapshot = list(seq)
        repair_tool_sequence(seq, "c1")
        assert seq == snapshot


def _user(content="go"):
    return LLMMessage(role="user", content=content, conversation_id="c1")


if __name__ == "__main__":
    unittest.main()
