"""Tests for claude-code provider in LLMClient."""

import json
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMClientError,
)


class TestBuildToolPrompt(unittest.TestCase):
    """Test _build_tool_prompt rendering."""

    def setUp(self):
        self.client = LLMClient(provider="claude-code", api_key="test-key")

    def test_empty_tools(self):
        self.assertEqual(self.client._build_tool_prompt([]), "")

    def test_single_tool(self):
        tools = [LLMToolDefinition(
            name="fetch_http",
            description="Fetch a URL via HTTP.",
            parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        )]
        result = self.client._build_tool_prompt(tools)
        self.assertIn("<available_tools>", result)
        self.assertIn("## fetch_http", result)
        self.assertIn("Fetch a URL via HTTP.", result)
        self.assertIn("</available_tools>", result)
        self.assertIn("<tool_call>", result)

    def test_multiple_tools(self):
        tools = [
            LLMToolDefinition(name="t1", description="D1", parameters={}),
            LLMToolDefinition(name="t2", description="D2", parameters={}),
        ]
        result = self.client._build_tool_prompt(tools)
        self.assertIn("## t1", result)
        self.assertIn("## t2", result)


class TestSerializeMessages(unittest.TestCase):
    """Test _serialize_messages_for_cli."""

    def setUp(self):
        self.client = LLMClient(provider="claude-code", api_key="test-key")

    def test_simple_user_message(self):
        msgs = [LLMMessage(role="user", content="Hello")]
        sys_prompt, user_text = self.client._serialize_messages_for_cli(msgs, None)
        self.assertEqual(sys_prompt, "")
        self.assertIn("Hello", user_text)

    def test_system_plus_user(self):
        msgs = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hi"),
        ]
        sys_prompt, user_text = self.client._serialize_messages_for_cli(msgs, None)
        self.assertIn("You are helpful.", sys_prompt)
        self.assertIn("Hi", user_text)

    def test_conversation_history(self):
        msgs = [
            LLMMessage(role="user", content="Search for Python"),
            LLMMessage(role="assistant", content="I'll search for that."),
            LLMMessage(role="user", content="Tell me more"),
        ]
        sys_prompt, user_text = self.client._serialize_messages_for_cli(msgs, None)
        self.assertIn("<conversation_history>", user_text)
        self.assertIn("Search for Python", user_text)
        self.assertIn("I'll search for that.", user_text)
        self.assertIn("Tell me more", user_text)
        self.assertIn('role="user"', user_text)
        self.assertIn('role="assistant"', user_text)

    def test_tool_calls_in_history(self):
        msgs = [
            LLMMessage(role="user", content="Search"),
            LLMMessage(
                role="assistant", content="Searching...",
                tool_calls=[LLMToolCall(id="tc1", name="web_search", arguments={"q": "test"})],
            ),
            LLMMessage(role="tool", content="Found 5 results", tool_call_id="tc1"),
            LLMMessage(role="user", content="Thanks"),
        ]
        _, user_text = self.client._serialize_messages_for_cli(msgs, None)
        self.assertIn("Searching...", user_text)
        self.assertIn("<tool_call>", user_text)
        self.assertIn("Found 5 results", user_text)
        self.assertIn('role="tool"', user_text)

    def test_system_with_tools(self):
        msgs = [
            LLMMessage(role="system", content="System instruction"),
            LLMMessage(role="user", content="Do something"),
        ]
        tools = [LLMToolDefinition(name="t1", description="D1", parameters={})]
        sys_prompt, _ = self.client._serialize_messages_for_cli(msgs, tools)
        self.assertIn("System instruction", sys_prompt)
        self.assertIn("<available_tools>", sys_prompt)


class TestExtractToolCalls(unittest.TestCase):
    """Test _extract_tool_calls parsing."""

    def setUp(self):
        self.client = LLMClient(provider="claude-code", api_key="test-key")

    def test_no_tool_calls(self):
        clean, tcs = self.client._extract_tool_calls("Just a plain response.")
        self.assertEqual(clean, "Just a plain response.")
        self.assertEqual(tcs, [])

    def test_single_tool_call(self):
        text = 'I will search.\n<tool_call>{"name": "web_search", "arguments": {"query": "test"}}</tool_call>'
        clean, tcs = self.client._extract_tool_calls(text)
        self.assertEqual(clean, "I will search.")
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].name, "web_search")
        self.assertEqual(tcs[0].arguments, {"query": "test"})
        self.assertTrue(tcs[0].id.startswith("cc_"))

    def test_multiple_tool_calls(self):
        text = (
            '<tool_call>{"name": "t1", "arguments": {"a": 1}}</tool_call>\n'
            '<tool_call>{"name": "t2", "arguments": {"b": 2}}</tool_call>'
        )
        clean, tcs = self.client._extract_tool_calls(text)
        self.assertEqual(len(tcs), 2)
        self.assertEqual(tcs[0].name, "t1")
        self.assertEqual(tcs[1].name, "t2")

    def test_malformed_json_skipped(self):
        text = '<tool_call>{not valid json}</tool_call>\nOK response.'
        clean, tcs = self.client._extract_tool_calls(text)
        self.assertEqual(tcs, [])
        self.assertIn("OK response.", clean)

    def test_multiline_tool_call(self):
        text = '<tool_call>\n{\n  "name": "t1",\n  "arguments": {"x": 1}\n}\n</tool_call>'
        _, tcs = self.client._extract_tool_calls(text)
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].name, "t1")


class TestCompleteClaude(unittest.TestCase):
    """Test _complete_claude_code with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(
            provider="claude-code", api_key="test-key",
            default_model="sonnet", timeout=30,
        )

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_basic_complete(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "Hello world!", "model": "sonnet"}),
            stderr="",
        )
        msgs = [LLMMessage(role="user", content="Hi")]
        resp = self.client.complete(msgs)
        self.assertEqual(resp.content, "Hello world!")
        self.assertEqual(resp.tool_calls, [])
        # Verify subprocess was called with correct args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        self.assertIn("-p", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--max-turns", cmd)
        self.assertIn("1", cmd)

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_complete_with_tool_calls(self, mock_run):
        response_text = 'Let me search.\n<tool_call>{"name": "web_search", "arguments": {"q": "test"}}</tool_call>'
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": response_text}),
            stderr="",
        )
        msgs = [LLMMessage(role="user", content="Search for test")]
        resp = self.client.complete(msgs)
        self.assertEqual(resp.content, "Let me search.")
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "web_search")
        self.assertEqual(resp.finish_reason, "tool_use")

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_cli_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: something went wrong",
        )
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("exited with code 1", str(ctx.exception))

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_binary_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("not found", str(ctx.exception))

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("timed out", str(ctx.exception))

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_env_vars_passed(self, mock_run):
        """Claude CLI uses its own auth — no env vars injected."""
        client = LLMClient(
            provider="claude-code", api_key="my-key",
            base_url="https://custom.api.com",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        client.complete([LLMMessage(role="user", content="test")])
        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_system_prompt_passed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        msgs = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        self.client.complete(msgs)
        # System prompt is injected into stdin, not CLI args
        stdin_text = mock_run.call_args[1].get("input", "")
        self.assertIn("<system_instructions>", stdin_text)
        self.assertIn("Be helpful", stdin_text)

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_plain_text_output(self, mock_run):
        """When Claude CLI returns plain text instead of JSON."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Just plain text response",
            stderr="",
        )
        resp = self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertEqual(resp.content, "Just plain text response")

    @patch("core.llm_providers.claude_code.subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr="",
        )
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertIn("empty output", str(ctx.exception))


class TestStreamClaude(unittest.TestCase):
    """Test _stream_claude_code with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(
            provider="claude-code", api_key="test-key",
            default_model="sonnet",
        )

    @patch("core.llm_providers.claude_code.subprocess.Popen")
    def test_stream_basic(self, mock_popen):
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello "}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "world!"}]}}),
            json.dumps({"type": "result", "result": "", "model": "sonnet", "usage": {"input_tokens": 10, "output_tokens": 5}}),
        ]
        # Use a MagicMock for stdout that supports iteration and close
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
        self.assertEqual(resp.content, "Hello world!")
        self.assertEqual(tokens, ["Hello ", "world!"])

    @patch("core.llm_providers.claude_code.subprocess.Popen")
    def test_stream_binary_not_found(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError()
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete_stream([LLMMessage(role="user", content="Hi")])
        self.assertIn("not found", str(ctx.exception))


class TestClaudeCodeEnv(unittest.TestCase):
    """Test claude-code env setup — CLI uses its own auth."""

    def test_env_is_clean(self):
        """No ANTHROPIC_API_KEY injected — CLI handles auth via `claude login`."""
        client = LLMClient(provider="claude-code", api_key="whatever")
        env = client._claude_code_env()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)


class TestProviderInProviders(unittest.TestCase):
    """Test that claude-code and gemini-cli are in PROVIDERS."""

    def test_providers_list(self):
        self.assertIn("claude-code", LLMClient.PROVIDERS)
        self.assertIn("gemini-cli", LLMClient.PROVIDERS)

    def test_default_model(self):
        self.assertEqual(LLMClient.DEFAULT_MODELS["claude-code"], "sonnet")
        self.assertEqual(LLMClient.DEFAULT_MODELS["gemini-cli"], "gemini-2.5-flash")

    def test_from_config_claude(self):
        client = LLMClient.from_config({
            "provider": "claude-code",
            "api_key": "test",
            "claude_binary": "/usr/bin/claude",
            "default_model": "opus",
        })
        self.assertEqual(client.provider, "claude-code")
        self.assertEqual(client.claude_binary, "/usr/bin/claude")
        self.assertEqual(client.default_model, "opus")

    def test_from_config_gemini(self):
        client = LLMClient.from_config({
            "provider": "gemini-cli",
            "api_key": "test-gemini",
            "gemini_binary": "/usr/bin/gemini",
            "default_model": "gemini-2.5-pro",
        })
        self.assertEqual(client.provider, "gemini-cli")
        self.assertEqual(client.gemini_binary, "/usr/bin/gemini")
        self.assertEqual(client.default_model, "gemini-2.5-pro")


# ── Gemini CLI Provider Tests ────────────────────────────────────
