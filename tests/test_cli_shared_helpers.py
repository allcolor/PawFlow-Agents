"""Unit tests for cli_shared tool-call synopsis helpers.

These helpers are shared by the CC prompt serializer AND the compaction
summarizer input — both need tool-calls and tool results to leave a
readable trace so the LLM does not forget that work happened.
"""

import unittest

from core.llm_client import LLMMessage, LLMToolCall
from core.llm_providers.cli_shared import (
    summarize_tool_call,
    textualize_message,
    _TOOL_ARG_TRUNC,
    _TOOL_RESULT_TRUNC,
)


class TestSummarizeToolCall(unittest.TestCase):

    def test_simple_string_arg(self):
        out = summarize_tool_call("bash", {"command": "ls -la"})
        self.assertEqual(out, 'bash(command="ls -la")')

    def test_multiple_args(self):
        out = summarize_tool_call("edit", {"path": "/a", "old": "x", "new": "y"})
        self.assertIn('path="/a"', out)
        self.assertIn('old="x"', out)
        self.assertIn('new="y"', out)
        self.assertTrue(out.startswith("edit("))

    def test_list_and_dict_collapsed(self):
        out = summarize_tool_call(
            "run_tests",
            {"test_files": ["a.py", "b.py"], "env": {"K": "v"}},
        )
        self.assertIn("test_files=<list:2>", out)
        self.assertIn("env=<dict:1>", out)

    def test_string_truncation(self):
        big = "x" * 500
        out = summarize_tool_call("t", {"s": big})
        # value must be truncated
        self.assertLess(len(out), 200)
        self.assertIn("...", out)

    def test_none_and_numeric_args(self):
        out = summarize_tool_call("t", {"a": None, "b": 42, "c": True})
        self.assertIn("a=None", out)
        self.assertIn("b=42", out)
        self.assertIn("c=True", out)

    def test_empty_name_fallback(self):
        out = summarize_tool_call("", {"k": "v"})
        self.assertTrue(out.startswith("<tool>("))

    def test_non_dict_args(self):
        out = summarize_tool_call("t", "raw")
        self.assertEqual(out, "t(...)")

    def test_mcp_wrapper_unwrapped(self):
        """mcp__pawflow__use_tool(tool_name=X, arguments=Y) → X(Y-synopsis)."""
        out = summarize_tool_call(
            "mcp__pawflow__use_tool",
            {"tool_name": "bash", "arguments": {"command": "pwd"}},
        )
        self.assertEqual(out, 'bash(command="pwd")')
        self.assertNotIn("mcp__", out)

    def test_use_tool_wrapper_unwrapped(self):
        out = summarize_tool_call(
            "use_tool",
            {"tool_name": "read", "arguments": {"path": "/f"}},
        )
        self.assertEqual(out, 'read(path="/f")')

    def test_quote_escaping(self):
        out = summarize_tool_call("t", {"s": 'he said "hi"'})
        # inner quotes are escaped so the wrapping quotes stay balanced
        self.assertIn('\\"hi\\"', out)


class TestTextualizeMessage(unittest.TestCase):

    def test_user_message(self):
        m = LLMMessage(role="user", content="Hello")
        self.assertEqual(textualize_message(m), "Hello")

    def test_empty_user_returns_none(self):
        m = LLMMessage(role="user", content="   ")
        self.assertIsNone(textualize_message(m))

    def test_assistant_text_only(self):
        m = LLMMessage(role="assistant", content="Sure, done.")
        self.assertEqual(textualize_message(m), "Sure, done.")

    def test_assistant_tool_only(self):
        m = LLMMessage(
            role="assistant", content="",
            tool_calls=[LLMToolCall(id="a", name="bash",
                                     arguments={"command": "git status"})],
        )
        out = textualize_message(m)
        self.assertTrue(out.startswith("[ran: "))
        self.assertIn("bash", out)
        self.assertIn('command="git status"', out)

    def test_assistant_text_and_tool(self):
        m = LLMMessage(
            role="assistant", content="Let me check.",
            tool_calls=[LLMToolCall(id="a", name="bash",
                                     arguments={"command": "ls"})],
        )
        out = textualize_message(m)
        self.assertIn("Let me check.", out)
        self.assertIn("[ran: bash", out)

    def test_assistant_multiple_tool_calls(self):
        m = LLMMessage(
            role="assistant", content="",
            tool_calls=[
                LLMToolCall(id="a", name="bash", arguments={"command": "ls"}),
                LLMToolCall(id="b", name="read", arguments={"path": "/f"}),
            ],
        )
        out = textualize_message(m)
        self.assertIn("bash", out)
        self.assertIn("read", out)
        self.assertIn("; ", out)  # separator between synopses

    def test_assistant_mcp_wrapper_unwrapped_in_synopsis(self):
        m = LLMMessage(
            role="assistant", content="",
            tool_calls=[LLMToolCall(
                id="a", name="mcp__pawflow__use_tool",
                arguments={"tool_name": "grep", "arguments": {"pattern": "foo"}},
            )],
        )
        out = textualize_message(m)
        self.assertIn("grep", out)
        self.assertNotIn("mcp__pawflow__use_tool", out)

    def test_tool_result_short(self):
        m = LLMMessage(role="tool", content="5 files", tool_call_id="a")
        out = textualize_message(m)
        self.assertEqual(out, "[tool_result: 5 files]")

    def test_tool_result_truncated(self):
        big = "x" * (_TOOL_RESULT_TRUNC + 200)
        m = LLMMessage(role="tool", content=big, tool_call_id="a")
        out = textualize_message(m)
        self.assertTrue(out.startswith("[tool_result: "))
        self.assertIn("...[+200c]", out)
        self.assertLess(len(out), _TOOL_RESULT_TRUNC + 80)

    def test_tool_result_empty(self):
        m = LLMMessage(role="tool", content="   ", tool_call_id="a")
        self.assertIsNone(textualize_message(m))

    def test_system_message(self):
        m = LLMMessage(role="system", content="You are X.")
        self.assertEqual(textualize_message(m), "You are X.")

    def test_unknown_role(self):
        m = LLMMessage(role="weird", content="hi")
        self.assertIsNone(textualize_message(m))


if __name__ == "__main__":
    unittest.main()
