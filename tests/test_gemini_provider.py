"""Tests for Gemini CLI LLM provider."""

import json
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from core.llm_client import LLMClient, LLMMessage, LLMClientError

class TestCompleteGemini(unittest.TestCase):
    """Test _complete_gemini_cli with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(
            provider="gemini-cli", api_key="test-key",
            default_model="gemini-2.5-flash", timeout=30,
        )

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_basic_complete(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "response": "Hello from Gemini!",
                "stats": {"models": {"gemini-2.5-flash": {"inputTokens": 10, "outputTokens": 5}}},
            }),
            stderr="",
        )
        resp = self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertEqual(resp.content, "Hello from Gemini!")
        self.assertEqual(resp.tokens_in, 10)
        self.assertEqual(resp.tokens_out, 5)
        self.assertEqual(resp.tool_calls, [])
        # Verify -p flag and -m flag
        cmd = mock_run.call_args[0][0]
        self.assertIn("-p", cmd)
        self.assertIn("-m", cmd)

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_complete_with_tool_calls(self, mock_run):
        response_text = 'Searching.\n<tool_call>{"name": "fetch_http", "arguments": {"url": "https://example.com"}}</tool_call>'
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"response": response_text}),
            stderr="",
        )
        resp = self.client.complete([LLMMessage(role="user", content="Fetch example.com")])
        self.assertEqual(resp.content, "Searching.")
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "fetch_http")
        self.assertEqual(resp.finish_reason, "tool_use")

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_env_vars(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"response": "ok"}),
            stderr="",
        )
        self.client.complete([LLMMessage(role="user", content="test")])
        env = mock_run.call_args[1]["env"]
        self.assertEqual(env["GEMINI_API_KEY"], "test-key")

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_system_prompt_via_temp_file(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"response": "ok"}),
            stderr="",
        )
        msgs = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        self.client.complete(msgs)
        # GEMINI_SYSTEM_MD should have been set in env
        env = mock_run.call_args[1]["env"]
        self.assertIn("GEMINI_SYSTEM_MD", env)
        # The temp file should have been cleaned up already
        import os
        self.assertFalse(os.path.exists(env["GEMINI_SYSTEM_MD"]))

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_cli_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: auth failed",
        )
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertIn("exited with code 1", str(ctx.exception))

    @patch("core.llm_providers.gemini_cli.subprocess.run")
    def test_binary_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertIn("not found", str(ctx.exception))
        self.assertIn("@google/gemini-cli", str(ctx.exception))


class TestStreamGemini(unittest.TestCase):
    """Test _stream_gemini_cli with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(
            provider="gemini-cli", api_key="test-key",
            default_model="gemini-2.5-flash",
        )

    @patch("core.llm_providers.gemini_cli.subprocess.Popen")
    def test_stream_basic(self, mock_popen):
        events = [
            json.dumps({"type": "message", "message": {"content": [{"type": "text", "text": "Hi "}]}}),
            json.dumps({"type": "result", "response": "", "stats": {"models": {"gemini-2.5-flash": {"inputTokens": 5, "outputTokens": 2}}}}),
        ]
        mock_stdout = MagicMock()
        mock_stdout.__iter__ = MagicMock(return_value=iter([line + "\n" for line in events]))
        mock_proc = MagicMock()
        mock_proc.stdout = mock_stdout
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        tokens = []
        resp = self.client.complete_stream(
            [LLMMessage(role="user", content="Hi")],
            callback=lambda t: tokens.append(t),
        )
        self.assertEqual(resp.content, "Hi")
        self.assertEqual(tokens, ["Hi "])


if __name__ == "__main__":
    unittest.main()
