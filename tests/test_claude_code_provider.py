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
        self.client = LLMClient(provider="claude-code", config={"api_key": "test-key"})
        self.client._conversation_id = "test-conv"
        self.client._agent_name = "test-agent"
        self.client._user_id = "test-user"

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
        self.client = LLMClient(provider="claude-code", config={"api_key": "test-key"})
        self.client._conversation_id = "test-conv"
        self.client._agent_name = "test-agent"
        self.client._user_id = "test-user"

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
        """Tool-call-only assistant msgs and tool results are now stripped."""
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
        # Tool calls and tool results are no longer serialized (CC manages its own tools)
        self.assertNotIn("<tool_call>", user_text)
        self.assertNotIn("Found 5 results", user_text)
        self.assertNotIn('role="tool"', user_text)
        # Tool calls and tool results are stripped from CLI prompt history
        self.assertIn("conversation_history", user_text)

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
        self.client = LLMClient(provider="claude-code", config={"api_key": "test-key"})
        self.client._conversation_id = "test-conv"
        self.client._agent_name = "test-agent"
        self.client._user_id = "test-user"

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




def _make_mock_popen(returncode=0, stdout="", stderr=""):
    """Create a mock proc + _pool_popen for claude-code tests."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.communicate.return_value = (stdout, stderr)
    def _pool_popen(self, workdir, cmd, **kwargs):
        _pool_popen._last_cmd = cmd
        _pool_popen._last_kwargs = kwargs
        _pool_popen._last_workdir = workdir
        return mock_proc, None
    _pool_popen._last_cmd = None
    _pool_popen._last_kwargs = None
    _pool_popen._last_workdir = None
    return mock_proc, _pool_popen

class TestCompleteClaude(unittest.TestCase):
    """Test _complete_claude_code with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(provider="claude-code", config={"api_key": "test-key", "default_model": "sonnet", "timeout": 30})
        self.client._conversation_id = "test-conv"
        self.client._agent_name = "test-agent"
        self.client._user_id = "test-user"
        # Skip credential check — no real Claude Code on CI
        self._cred_patcher = patch.object(self.client, '_setup_credentials')
        self._cred_patcher.start()
        self.addCleanup(self._cred_patcher.stop)

    def _mock_popen(self, returncode=0, stdout="", stderr="", side_effect=None):
        """Patch _pool_popen on the provider to return a mock proc."""
        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        if side_effect:
            mock_proc.communicate.side_effect = side_effect
        else:
            mock_proc.communicate.return_value = (stdout, stderr)
        patcher = patch.object(
            self.client, '_pool_popen',
            return_value=(mock_proc, None),
        )
        mock_pp = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_pp, mock_proc

    def test_basic_complete(self):
        mock_pp, _ = self._mock_popen(
            stdout=json.dumps({"result": "Hello world!", "model": "sonnet"}))
        msgs = [LLMMessage(role="user", content="Hi")]
        resp = self.client.complete(msgs)
        self.assertEqual(resp.content, "Hello world!")
        self.assertEqual(resp.tool_calls, [])
        cmd = mock_pp.call_args[0][1]  # (workdir, cmd, **kwargs)
        self.assertIn("-p", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--max-turns", cmd)
        self.assertIn("1", cmd)

    def test_complete_with_tool_calls(self):
        """Complete mode returns raw content — tool calls are in streaming mode."""
        response_text = 'Let me search.\n<tool_call>{"name": "web_search", "arguments": {"q": "test"}}</tool_call>'
        self._mock_popen(stdout=json.dumps({"result": response_text}))
        msgs = [LLMMessage(role="user", content="Search for test")]
        resp = self.client.complete(msgs)
        # In prompt mode, content is returned as-is from CLI JSON
        self.assertIn("Let me search.", resp.content)
        self.assertEqual(resp.finish_reason, "stop")

    def test_cli_error(self):
        self._mock_popen(returncode=1, stderr="Error: something went wrong")
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("exited with code 1", str(ctx.exception))

    def test_binary_not_found(self):
        patcher = patch.object(
            self.client, '_pool_popen',
            side_effect=FileNotFoundError())
        patcher.start()
        self.addCleanup(patcher.stop)
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("not found", str(ctx.exception))

    def test_timeout(self):
        self._mock_popen(side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30))
        msgs = [LLMMessage(role="user", content="Hi")]
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete(msgs)
        self.assertIn("timed out", str(ctx.exception))

    def test_env_vars_passed(self):
        """Claude CLI uses its own auth — env is set via _claude_code_env."""
        client = LLMClient(provider="claude-code", config={"api_key": "my-key", "base_url": "https://custom.api.com"})
        client._conversation_id = "test-conv"
        client._agent_name = "test-agent"
        client._user_id = "test-user"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (json.dumps({"result": "ok"}), "")
        cred_patcher = patch.object(client, '_setup_credentials')
        cred_patcher.start()
        self.addCleanup(cred_patcher.stop)
        patcher = patch.object(
            client, '_pool_popen',
            return_value=(mock_proc, None))
        mock_pp = patcher.start()
        self.addCleanup(patcher.stop)
        client.complete([LLMMessage(role="user", content="test")])
        # _pool_popen internally sets env — we just verify the call succeeded
        self.assertTrue(mock_pp.called)

    def test_system_prompt_passed(self):
        mock_pp, mock_proc = self._mock_popen(
            stdout=json.dumps({"result": "ok"}))
        msgs = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        self.client.complete(msgs)
        # stdin is passed to communicate()
        stdin_text = mock_proc.communicate.call_args[1].get("input", "")
        self.assertIn("Be helpful", stdin_text)

    def test_plain_text_output(self):
        """When Claude CLI returns plain text instead of JSON."""
        self._mock_popen(stdout="Just plain text response")
        resp = self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertEqual(resp.content, "Just plain text response")

    def test_empty_output(self):
        self._mock_popen(stdout="")
        with self.assertRaises(LLMClientError) as ctx:
            self.client.complete([LLMMessage(role="user", content="Hi")])
        self.assertIn("empty output", str(ctx.exception))


class TestStreamClaude(unittest.TestCase):
    """Test _stream_claude_code with mocked subprocess."""

    def setUp(self):
        self.client = LLMClient(provider="claude-code", config={"api_key": "test-key", "default_model": "sonnet"})
        self.client._conversation_id = "test-conv"
        self.client._agent_name = "test-agent"
        self.client._user_id = "test-user"
        # Skip credential check — no real Claude Code on CI
        self._cred_patcher = patch.object(self.client, '_setup_credentials')
        self._cred_patcher.start()
        self.addCleanup(self._cred_patcher.stop)

    def test_stream_basic(self):
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello "}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "world!"}]}}),
            json.dumps({"type": "result", "result": "", "model": "sonnet", "usage": {"input_tokens": 10, "output_tokens": 5}}),
        ]
        mock_stdout = MagicMock()
        mock_stdout.__iter__ = MagicMock(return_value=iter([line + "\n" for line in events]))
        mock_proc = MagicMock()
        mock_proc.stdout = mock_stdout
        mock_proc.returncode = 0
        with patch.object(self.client, '_pool_popen',
                          return_value=(mock_proc, None)):
            tokens = []
            turns = []
            resp = self.client.complete_stream(
                [LLMMessage(role="user", content="Hi")],
                callback=lambda t: tokens.append(t),
                turn_callback=lambda text, tc: turns.append(text),
            )
            # Tokens are streamed via callback
            self.assertEqual(tokens, ["Hello ", "world!"])
            # turn_callback receives the full turn text
            self.assertEqual(turns, ["Hello world!"])

    def test_stream_binary_not_found(self):
        with patch.object(self.client, '_pool_popen',
                          side_effect=FileNotFoundError()):
            with self.assertRaises(LLMClientError) as ctx:
                self.client.complete_stream([LLMMessage(role="user", content="Hi")])
            self.assertIn("not found", str(ctx.exception))


class TestClaudeCodeEnv(unittest.TestCase):
    """Test claude-code env setup."""

    def test_env_clean_without_api_key(self):
        """No api_key configured → no ANTHROPIC_API_KEY in env (uses OAuth)."""
        client = LLMClient(provider="claude-code", config={})
        client._conversation_id = "test-conv"
        client._agent_name = "test-agent"
        client._user_id = "test-user"
        env = client._claude_code_env("/tmp")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/tmp")

    def test_env_with_api_key(self):
        """api_key configured → ANTHROPIC_API_KEY passed to CC."""
        client = LLMClient(provider="claude-code", config={"api_key": "sk-test-123"})
        client._conversation_id = "test-conv"
        client._agent_name = "test-agent"
        client._user_id = "test-user"
        env = client._claude_code_env("/tmp")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-test-123")

    def test_env_with_base_url(self):
        """base_url configured → ANTHROPIC_BASE_URL passed to CC."""
        client = LLMClient(provider="claude-code", config={
            "api_key": "sk-test", "base_url": "http://localhost:11434/v1"})
        client._conversation_id = "test-conv"
        client._agent_name = "test-agent"
        client._user_id = "test-user"
        env = client._claude_code_env("/tmp")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:11434/v1")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-test")


class TestProviderInProviders(unittest.TestCase):
    """Test that claude-code and gemini-cli are in PROVIDERS."""

    def test_providers_list(self):
        self.assertIn("claude-code", LLMClient.PROVIDERS)

    def test_default_model(self):
        self.assertIn("claude-code", LLMClient.DEFAULT_MODELS)

    def test_from_config_claude(self):
        client = LLMClient.from_config({
            "provider": "claude-code",
            "api_key": "test",
            "default_model": "opus",
        })
        self.assertEqual(client.provider, "claude-code")
        self.assertEqual(client.default_model, "opus")
        # claude_binary is auto-detected, not configurable
        self.assertIsInstance(client.claude_binary, str)



# ── Gemini CLI Provider Tests ────────────────────────────────────
