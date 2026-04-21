"""Tests for _select_recent_messages split-point algorithm.

Post-compact we must guarantee a real conversation window, not just "25
* messages* of any kind". Assistant turns that only carry tool_calls and
have empty text do NOT count toward the min_conversation budget — they
are preserved in the window but via the surrounding include-between-
text-turns rule, not by counting them.
"""

import unittest

from core.llm_client import LLMMessage, LLMToolCall
from tasks.ai.agent_compaction import _select_recent_messages


def _user(t):
    return LLMMessage(role="user", content=t, conversation_id="test_conv")


def _asst(t):
    return LLMMessage(role="assistant", content=t, conversation_id="test_conv")


def _asst_tool(name="bash", args=None):
    return LLMMessage(
        role="assistant", content="",
        tool_calls=[LLMToolCall(id="t", name=name, arguments=args or {})], conversation_id="test_conv")


def _tool(t):
    return LLMMessage(role="tool", content=t, tool_call_id="t", conversation_id="test_conv")


class TestSelectRecentMessages(unittest.TestCase):

    def test_below_min_conversation_returns_start(self):
        msgs = [_user("sys")] + [_user("hi"), _asst("hello")] * 3
        # 6 msgs + 1 system = 7 total. min_conversation default = 25.
        split = _select_recent_messages(msgs, start_idx=1, min_conversation=25)
        self.assertEqual(split, 1)

    def test_text_turns_counted(self):
        msgs = [_user("sys")]
        for i in range(30):
            msgs.append(_user(f"u{i}"))
            msgs.append(_asst(f"a{i}"))
        split = _select_recent_messages(msgs, start_idx=1, min_conversation=5)
        # Need 5 text turns → last 5 messages cover them (2.5 pairs → 3 pairs).
        kept = msgs[split:]
        text_count = sum(1 for m in kept if m.role in ("user", "assistant") and
                          isinstance(m.content, str) and m.content.strip())
        self.assertGreaterEqual(text_count, 5)

    def test_tool_only_assistants_do_not_count(self):
        """If the tail is mostly tool-call-only assistants, they must not
        be counted toward min_conversation — otherwise the serializer drops
        them and the compacted window ends up empty."""
        msgs = [_user("sys")]
        # 3 real text exchanges at the head, then 20 tool-call-only turns,
        # then 3 more text exchanges at the tail.
        for i in range(3):
            msgs.append(_user(f"u{i}"))
            msgs.append(_asst(f"a{i}"))
        for _ in range(20):
            msgs.append(_asst_tool())
            msgs.append(_tool("ok"))
        for i in range(3):
            msgs.append(_user(f"tail_u{i}"))
            msgs.append(_asst(f"tail_a{i}"))
        split = _select_recent_messages(msgs, start_idx=1, min_conversation=5)
        kept = msgs[split:]
        text_count = sum(1 for m in kept if m.role in ("user", "assistant") and
                          isinstance(m.content, str) and m.content.strip())
        # min_conversation=5 requires 5 text turns in kept, even though the
        # last ~40 messages are mostly tool-only.
        self.assertGreaterEqual(text_count, 5)

    def test_empty_text_assistants_ignored(self):
        msgs = [_user("sys")]
        # 25 empty-text assistants (no tool_calls at all either)
        for _ in range(25):
            msgs.append(_asst(""))
        # 3 real text exchanges at tail
        for i in range(3):
            msgs.append(_user(f"u{i}"))
            msgs.append(_asst(f"a{i}"))
        split = _select_recent_messages(msgs, start_idx=1, min_conversation=3)
        kept = msgs[split:]
        text_count = sum(1 for m in kept if m.role in ("user", "assistant") and
                          isinstance(m.content, str) and m.content.strip())
        self.assertGreaterEqual(text_count, 3)

    def test_max_total_caps_window(self):
        msgs = [_user("sys")]
        for i in range(50):
            msgs.append(_user(f"u{i}"))
            msgs.append(_asst(f"a{i}"))
        # 100 messages; cap to 20.
        split = _select_recent_messages(msgs, start_idx=1,
                                         min_conversation=5, max_total=20)
        kept = msgs[split:]
        self.assertLessEqual(len(kept), 20)


if __name__ == "__main__":
    unittest.main()
