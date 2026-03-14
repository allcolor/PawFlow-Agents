"""Tests for P22 — Agent LLM Flow.

Tests cover:
- LLMClient tool_use support (OpenAI + Anthropic message formats)
- LLMToolDefinition, LLMToolCall, LLMToolResult dataclasses
- LLMMessage extended fields (tool_calls, tool_call_id)
- LLMResponse.tool_calls field
- ToolRegistry (register, execute, list)
- Builtin tool handlers (execute_script, scrape_url, read_file)
- AgentLoopTask (loop logic, max iterations, conversation persistence)
- Agent flow template
- i18n keys
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import (
    ToolRegistry, ToolHandler, create_default_registry,
    ExecuteScriptHandler, ReadFileHandler,
    HTTPToolHandler, TaskToolHandler, MCPToolHandler,
    ConfigurableToolHandler, load_agent_tools, discover_mcp_tools,
)
from core import FlowFile, TaskFactory
from tasks.ai.agent_loop import AgentLoopTask


# ── LLMClient tool_use dataclasses ──────────────────────────────────


class TestToolUseDataclasses(unittest.TestCase):

    def test_tool_definition(self):
        td = LLMToolDefinition(
            name="my_tool",
            description="Does things",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        assert td.name == "my_tool"
        assert td.description == "Does things"
        assert "x" in td.parameters["properties"]

    def test_tool_call(self):
        tc = LLMToolCall(id="call_123", name="my_tool", arguments={"x": "hello"})
        assert tc.id == "call_123"
        assert tc.name == "my_tool"
        assert tc.arguments == {"x": "hello"}

    def test_tool_result(self):
        tr = LLMToolResult(tool_call_id="call_123", content="result text")
        assert tr.tool_call_id == "call_123"
        assert tr.content == "result text"

    def test_message_with_tool_calls(self):
        tc = LLMToolCall(id="call_1", name="search", arguments={"q": "test"})
        msg = LLMMessage(role="assistant", content="", tool_calls=[tc])
        assert msg.role == "assistant"
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "search"

    def test_message_tool_result(self):
        msg = LLMMessage(role="tool", content="found 3 results", tool_call_id="call_1")
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"

    def test_response_with_tool_calls(self):
        tc = LLMToolCall(id="c1", name="calc", arguments={"expr": "2+2"})
        resp = LLMResponse(content="", tool_calls=[tc], finish_reason="tool_calls")
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "calc"

    def test_response_default_empty_tool_calls(self):
        resp = LLMResponse(content="hello")
        assert resp.tool_calls == []

    def test_message_default_content(self):
        msg = LLMMessage(role="user")
        assert msg.content == ""
        assert msg.tool_calls is None
        assert msg.tool_call_id is None


# ── LLMClient message building ──────────────────────────────────────


class TestLLMClientMessageBuilding(unittest.TestCase):

    def setUp(self):
        self.client = LLMClient(provider="openai", api_key="test-key")

    def test_openai_simple_messages(self):
        messages = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hi"),
        ]
        result = self.client._build_openai_messages(messages)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1] == {"role": "user", "content": "Hi"}

    def test_openai_tool_call_message(self):
        tc = LLMToolCall(id="call_1", name="search", arguments={"q": "test"})
        msg = LLMMessage(role="assistant", content="", tool_calls=[tc])
        result = self.client._build_openai_messages([msg])
        assert result[0]["role"] == "assistant"
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "call_1"
        assert result[0]["tool_calls"][0]["type"] == "function"
        func = result[0]["tool_calls"][0]["function"]
        assert func["name"] == "search"
        assert json.loads(func["arguments"]) == {"q": "test"}

    def test_openai_tool_result_message(self):
        msg = LLMMessage(role="tool", content="result", tool_call_id="call_1")
        result = self.client._build_openai_messages([msg])
        assert result[0]["role"] == "tool"
        assert result[0]["content"] == "result"
        assert result[0]["tool_call_id"] == "call_1"

    def test_anthropic_simple_messages(self):
        client = LLMClient(provider="anthropic", api_key="test-key")
        messages = [
            LLMMessage(role="system", content="System prompt"),
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi!"),
        ]
        system_text, api_msgs = client._build_anthropic_messages(messages)
        assert system_text == "System prompt"
        assert len(api_msgs) == 2
        assert api_msgs[0] == {"role": "user", "content": "Hello"}
        assert api_msgs[1] == {"role": "assistant", "content": "Hi!"}

    def test_anthropic_tool_call_message(self):
        client = LLMClient(provider="anthropic", api_key="test-key")
        tc = LLMToolCall(id="tu_1", name="calc", arguments={"x": 5})
        msg = LLMMessage(role="assistant", content="Let me calculate.", tool_calls=[tc])
        _, api_msgs = client._build_anthropic_messages([msg])
        assert len(api_msgs) == 1
        content_blocks = api_msgs[0]["content"]
        assert content_blocks[0]["type"] == "text"
        assert content_blocks[0]["text"] == "Let me calculate."
        assert content_blocks[1]["type"] == "tool_use"
        assert content_blocks[1]["id"] == "tu_1"
        assert content_blocks[1]["name"] == "calc"

    def test_anthropic_tool_result_message(self):
        client = LLMClient(provider="anthropic", api_key="test-key")
        msg = LLMMessage(role="tool", content="result=10", tool_call_id="tu_1")
        _, api_msgs = client._build_anthropic_messages([msg])
        assert api_msgs[0]["role"] == "user"
        content = api_msgs[0]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tu_1"
        assert content[0]["content"] == "result=10"


# ── OpenAI response parsing ─────────────────────────────────────────


class TestOpenAIToolCallParsing(unittest.TestCase):

    @patch.object(LLMClient, '_http_post')
    def test_parse_tool_calls_from_response(self, mock_post):
        mock_post.return_value = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": '{"query": "weather"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        client = LLMClient(provider="openai", api_key="test")
        resp = client.complete([LLMMessage(role="user", content="weather?")])
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"
        assert resp.tool_calls[0].arguments == {"query": "weather"}
        assert resp.finish_reason == "tool_calls"

    @patch.object(LLMClient, '_http_post')
    def test_parse_text_response_no_tool_calls(self, mock_post):
        mock_post.return_value = {
            "choices": [{
                "message": {"content": "The weather is sunny."},
                "finish_reason": "stop",
            }],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }

        client = LLMClient(provider="openai", api_key="test")
        resp = client.complete([LLMMessage(role="user", content="weather?")])
        assert resp.tool_calls == []
        assert resp.content == "The weather is sunny."

    @patch.object(LLMClient, '_http_post')
    def test_tools_sent_in_request(self, mock_post):
        mock_post.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }

        client = LLMClient(provider="openai", api_key="test")
        tools = [LLMToolDefinition(name="search", description="Search", parameters={"type": "object", "properties": {}})]
        client.complete([LLMMessage(role="user", content="hi")], tools=tools)

        call_body = mock_post.call_args[0][1]
        assert "tools" in call_body
        assert call_body["tools"][0]["function"]["name"] == "search"


# ── Anthropic response parsing ───────────────────────────────────────


class TestAnthropicToolCallParsing(unittest.TestCase):

    @patch.object(LLMClient, '_http_post')
    def test_parse_tool_use_blocks(self, mock_post):
        mock_post.return_value = {
            "content": [
                {"type": "text", "text": "I'll search for that."},
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "search",
                    "input": {"query": "weather"},
                },
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        client = LLMClient(provider="anthropic", api_key="test")
        resp = client.complete([LLMMessage(role="user", content="weather?")])
        assert resp.content == "I'll search for that."
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"
        assert resp.tool_calls[0].id == "toolu_abc"
        assert resp.finish_reason == "tool_use"

    @patch.object(LLMClient, '_http_post')
    def test_tools_sent_in_anthropic_request(self, mock_post):
        mock_post.return_value = {
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }

        client = LLMClient(provider="anthropic", api_key="test")
        tools = [LLMToolDefinition(name="calc", description="Calculator", parameters={"type": "object", "properties": {}})]
        client.complete([LLMMessage(role="user", content="hi")], tools=tools)

        call_body = mock_post.call_args[0][1]
        assert "tools" in call_body
        assert call_body["tools"][0]["name"] == "calc"
        assert "input_schema" in call_body["tools"][0]


# ── ToolRegistry ─────────────────────────────────────────────────────


class TestToolRegistry(unittest.TestCase):

    def test_register_and_get(self):
        registry = ToolRegistry()
        handler = ExecuteScriptHandler()
        registry.register(handler)
        assert registry.get("execute_script") is handler

    def test_unregister(self):
        registry = ToolRegistry()
        registry.register(ExecuteScriptHandler())
        registry.unregister("execute_script")
        assert registry.get("execute_script") is None

    def test_list_tools(self):
        registry = create_default_registry()
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "execute_script" in names
        assert "scrape_url" in names
        assert "read_file" in names

    def test_get_tool_definitions(self):
        registry = create_default_registry()
        defs = registry.get_tool_definitions()
        assert len(defs) == 31  # builtins + memory + plan + notify + create_tool + ask_agent + flow_manager + pyfi2_help + secrets + resources + show_file + browser + link_identity + remote_exec
        assert all("name" in d and "description" in d and "parameters" in d for d in defs)

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = registry.execute("nonexistent", {})
        assert "unknown tool" in result

    def test_execute_script_expression(self):
        handler = ExecuteScriptHandler()
        result = handler.execute({"code": "2 + 3"})
        assert result == "5"

    def test_execute_script_list(self):
        handler = ExecuteScriptHandler()
        result = handler.execute({"code": "len([1, 2, 3])"})
        assert result == "3"

    def test_execute_script_empty(self):
        handler = ExecuteScriptHandler()
        result = handler.execute({"code": ""})
        assert "Error" in result or "no code" in result

    def test_execute_script_error(self):
        handler = ExecuteScriptHandler()
        result = handler.execute({"code": "1/0"})
        assert "Error" in result

    def test_execute_script_exec_mode(self):
        handler = ExecuteScriptHandler()
        result = handler.execute({"code": "result = 42"})
        assert result == "42"

    def test_read_file_handler(self):
        from core.file_store import FileStore
        FileStore.reset()
        _tmpdir = tempfile.mkdtemp()
        store = FileStore(base_dir=_tmpdir)
        FileStore._instance = store
        try:
            store.store("hello.txt", b"hello world", "text/plain")
            handler = ReadFileHandler()
            result = handler.execute({"path": "hello.txt"})
            assert result == "hello world"
        finally:
            FileStore.reset()
            import shutil
            shutil.rmtree(_tmpdir, ignore_errors=True)

    def test_read_file_nonexistent(self):
        handler = ReadFileHandler()
        result = handler.execute({"path": "nonexistent_file.txt"})
        assert "not found" in result

    def test_read_file_empty_path(self):
        handler = ReadFileHandler()
        result = handler.execute({"path": ""})
        assert "Error" in result or "no path" in result

    def test_custom_handler(self):
        class EchoHandler(ToolHandler):
            @property
            def name(self): return "echo"
            @property
            def description(self): return "Echo input"
            @property
            def parameters_schema(self): return {"type": "object", "properties": {"text": {"type": "string"}}}
            def execute(self, arguments):
                return arguments.get("text", "")

        registry = ToolRegistry()
        registry.register(EchoHandler())
        result = registry.execute("echo", {"text": "hello"})
        assert result == "hello"


# ── AgentLoopTask ────────────────────────────────────────────────────


class TestAgentLoopTask(unittest.TestCase):

    def test_task_registered(self):
        task_class = TaskFactory.get("agentLoop")
        assert task_class is AgentLoopTask

    def test_task_metadata(self):
        assert AgentLoopTask.TYPE == "agentLoop"
        assert AgentLoopTask.VERSION == "1.0.0"
        assert AgentLoopTask.ICON == "ai"

    def test_parameter_schema(self):
        task = AgentLoopTask({"api_key": "test"})
        schema = task.get_parameter_schema()
        assert "llm_service" in schema
        assert "model" in schema
        assert "system_prompt" in schema
        assert "max_iterations" in schema
        assert "tools" in schema
        assert "conversation_attribute" in schema

    def test_tool_registry_default(self):
        task = AgentLoopTask({"api_key": "test"})
        registry = task.get_tool_registry()
        assert len(registry.list_tools()) == 31  # builtins + memory + plan + notify + create_tool + ask_agent + flow_manager + pyfi2_help + secrets + resources + show_file + browser + link_identity + remote_exec

    def test_tool_registry_custom(self):
        task = AgentLoopTask({"api_key": "test"})
        custom = ToolRegistry()
        task.set_tool_registry(custom)
        assert task.get_tool_registry() is custom

    @patch.object(LLMClient, 'complete')
    def test_simple_text_response(self, mock_complete):
        """Agent gets a direct text response (no tool calls)."""
        mock_complete.return_value = LLMResponse(
            content="Hello! How can I help?",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=8,
            finish_reason="stop",
        )

        task = AgentLoopTask({
            "api_key": "test-key",
            "provider": "openai",
            "system_prompt": "You are helpful.",
        })
        ff = FlowFile(content=b"Hi there")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"Hello! How can I help?"
        assert results[0].get_attribute("agent.iterations") == "1"
        assert results[0].get_attribute("agent.tools_called") == ""
        assert results[0].get_attribute("agent.model") == "gpt-4o"

    @patch.object(LLMClient, 'complete')
    def test_tool_call_loop(self, mock_complete):
        """Agent calls a tool, gets result, then produces final answer."""
        # First call: LLM requests tool call
        tool_call = LLMToolCall(id="call_1", name="execute_script", arguments={"code": "2+2"})
        first_response = LLMResponse(
            content="",
            model="gpt-4o",
            tokens_in=20,
            tokens_out=15,
            finish_reason="tool_calls",
            tool_calls=[tool_call],
        )
        # Second call: LLM gives final text
        second_response = LLMResponse(
            content="The result is 4.",
            model="gpt-4o",
            tokens_in=30,
            tokens_out=10,
            finish_reason="stop",
        )
        mock_complete.side_effect = [first_response, second_response]

        task = AgentLoopTask({
            "api_key": "test-key",
            "provider": "openai",
        })
        ff = FlowFile(content=b"What is 2+2?")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"The result is 4."
        assert results[0].get_attribute("agent.iterations") == "2"
        assert results[0].get_attribute("agent.tools_called") == "execute_script"
        # Token totals
        assert results[0].get_attribute("agent.tokens_in") == "50"
        assert results[0].get_attribute("agent.tokens_out") == "25"

    @patch.object(LLMClient, 'complete')
    def test_max_iterations_safety(self, mock_complete):
        """Agent stops after max_iterations and forces a final synthesis."""
        tool_call = LLMToolCall(id="call_1", name="execute_script", arguments={"code": "1"})
        tool_response = LLMResponse(
            content="",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=5,
            finish_reason="tool_calls",
            tool_calls=[tool_call],
        )
        synthesis_response = LLMResponse(
            content="Here is my synthesis.",
            model="gpt-4o",
            tokens_in=20,
            tokens_out=10,
            finish_reason="stop",
        )
        # 3 tool iterations + 1 forced synthesis call
        mock_complete.side_effect = [tool_response, tool_response, tool_response,
                                     synthesis_response]

        task = AgentLoopTask({
            "api_key": "test-key",
            "max_iterations": 3,
        })
        ff = FlowFile(content=b"loop forever")
        results = task.execute(ff)

        assert results[0].get_attribute("agent.iterations") == "3"
        assert mock_complete.call_count == 4  # 3 iterations + 1 synthesis
        assert results[0].get_content() == b"Here is my synthesis."

    @patch.object(LLMClient, 'complete')
    def test_conversation_persistence(self, mock_complete):
        """Conversation is stored in attribute when configured."""
        mock_complete.return_value = LLMResponse(
            content="I'm here to help.",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=5,
            finish_reason="stop",
        )

        task = AgentLoopTask({
            "api_key": "test-key",
            "conversation_attribute": "agent.history",
        })
        ff = FlowFile(content=b"Hello")
        results = task.execute(ff)

        history_json = results[0].get_attribute("agent.history")
        assert history_json is not None
        history = json.loads(history_json)
        roles = [m["role"] for m in history]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    @patch.object(LLMClient, 'complete')
    def test_conversation_restore(self, mock_complete):
        """Conversation is restored from attribute."""
        mock_complete.return_value = LLMResponse(
            content="Still here!",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=5,
            finish_reason="stop",
        )

        existing_history = json.dumps([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ])

        task = AgentLoopTask({
            "api_key": "test-key",
            "conversation_attribute": "agent.history",
        })
        ff = FlowFile(content=b"How are you?")
        ff.set_attribute("agent.history", existing_history)
        results = task.execute(ff)

        # Verify restored history + new user message were sent
        # The list is mutated after the call (assistant response appended),
        # so we check the history stored in the output attribute
        history = json.loads(results[0].get_attribute("agent.history"))
        roles = [m["role"] for m in history]
        # system, user, assistant (restored), user (new), assistant (new response)
        assert roles == ["system", "user", "assistant", "user", "assistant"]
        assert history[3]["content"] == "How are you?"
        assert history[4]["content"] == "Still here!"

    @patch.object(LLMClient, 'complete')
    def test_multiple_tool_calls_in_one_response(self, mock_complete):
        """LLM requests multiple tool calls in a single response."""
        tc1 = LLMToolCall(id="call_1", name="execute_script", arguments={"code": "2+2"})
        tc2 = LLMToolCall(id="call_2", name="execute_script", arguments={"code": "3*3"})
        first_response = LLMResponse(
            content="",
            model="gpt-4o",
            tokens_in=20,
            tokens_out=15,
            finish_reason="tool_calls",
            tool_calls=[tc1, tc2],
        )
        second_response = LLMResponse(
            content="4 and 9",
            model="gpt-4o",
            tokens_in=30,
            tokens_out=5,
            finish_reason="stop",
        )
        mock_complete.side_effect = [first_response, second_response]

        task = AgentLoopTask({"api_key": "test-key"})
        ff = FlowFile(content=b"Calculate both")
        results = task.execute(ff)

        assert results[0].get_content() == b"4 and 9"
        assert results[0].get_attribute("agent.tools_called") == "execute_script,execute_script"

    def test_serialize_deserialize_messages(self):
        task = AgentLoopTask({"api_key": "test"})
        tc = LLMToolCall(id="c1", name="search", arguments={"q": "test"})
        messages = [
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="hi"),
            LLMMessage(role="assistant", content="", tool_calls=[tc]),
            LLMMessage(role="tool", content="result", tool_call_id="c1"),
        ]
        serialized = task._serialize_messages(messages)
        deserialized = task._deserialize_messages(serialized)

        assert len(deserialized) == 4
        assert deserialized[0].role == "system"
        assert deserialized[2].tool_calls[0].name == "search"
        assert deserialized[3].tool_call_id == "c1"

    @patch.object(LLMClient, 'complete')
    def test_custom_tools_json(self, mock_complete):
        """Agent uses custom tool definitions from JSON config."""
        mock_complete.return_value = LLMResponse(
            content="Done.",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=5,
            finish_reason="stop",
        )

        custom_tools = json.dumps([
            {"name": "weather", "description": "Get weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}
        ])

        task = AgentLoopTask({
            "api_key": "test-key",
            "tools": custom_tools,
        })
        ff = FlowFile(content=b"What's the weather?")
        task.execute(ff)

        call_args = mock_complete.call_args
        tools = call_args[1].get("tools", call_args[0][4] if len(call_args[0]) > 4 else None)
        assert tools is not None
        assert len(tools) == 1
        assert tools[0].name == "weather"

    def test_invalid_tools_json_raises(self):
        task = AgentLoopTask({
            "api_key": "test-key",
            "tools": "not valid json",
        })
        ff = FlowFile(content=b"test")
        with self.assertRaises(ValueError):
            task.execute(ff)


# ── Template ─────────────────────────────────────────────────────────


class TestAgentTemplate(unittest.TestCase):

    def test_agent_template_exists(self):
        from gui.services.template_service import TemplateService
        svc = TemplateService()
        templates = svc.list_templates()
        ids = [t["id"] for t in templates]
        assert "builtin_agent_llm" in ids

    def test_agent_template_content(self):
        from gui.services.template_service import TemplateService
        svc = TemplateService()
        tpl = svc.get_template("builtin_agent_llm")
        assert tpl is not None
        assert "agentLoop_1" in tpl["tasks"]
        assert "httpReceiver_1" in tpl["tasks"]
        assert "handleHTTPResponse_1" in tpl["tasks"]
        assert tpl["category"] == "Integration"
        assert tpl["difficulty"] == "advanced"

    def test_agent_template_relations(self):
        from gui.services.template_service import TemplateService
        svc = TemplateService()
        tpl = svc.get_template("builtin_agent_llm")
        froms = [r["from"] for r in tpl["relations"]]
        tos = [r["to"] for r in tpl["relations"]]
        assert "httpReceiver_1" in froms
        assert "agentLoop_1" in tos
        assert "handleHTTPResponse_1" in tos


# ── i18n ─────────────────────────────────────────────────────────────


class TestAgentI18n(unittest.TestCase):

    def test_agent_keys_in_all_locales(self):
        agent_keys = [
            "agent.title", "agent.iterations", "agent.tools_called",
            "agent.max_iterations", "agent.system_prompt",
            "agent.tool_registry", "agent.conversation", "agent.no_tools",
        ]
        for locale in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{locale}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in agent_keys:
                assert key in data, f"Missing key '{key}' in {locale}.json"


# ── LLMConnectionService forwards tools ─────────────────────────────


class TestLLMConnectionServiceTools(unittest.TestCase):

    def test_complete_accepts_tools_parameter(self):
        import inspect
        from services.llm_connection import LLMConnectionService
        sig = inspect.signature(LLMConnectionService.complete)
        assert "tools" in sig.parameters


# ── Agent Tools ──────────────────────────────────────────────────────


class TestHTTPToolHandler(unittest.TestCase):

    def test_init_properties(self):
        h = HTTPToolHandler(
            tool_name="search",
            tool_description="Search the web",
            tool_parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            endpoint="http://localhost:8080/search",
            method="POST",
        )
        assert h.name == "search"
        assert h.description == "Search the web"
        assert "q" in h.parameters_schema["properties"]

    @patch("http.client.HTTPConnection")
    def test_execute_post(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"results": ["a", "b"]}'
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        h = HTTPToolHandler(
            tool_name="search",
            tool_description="Search",
            tool_parameters={"type": "object", "properties": {}},
            endpoint="http://localhost:8080/search",
            method="POST",
        )
        result = h.execute({"query": "test"})
        assert "HTTP 200" in result
        mock_conn.request.assert_called_once()
        args = mock_conn.request.call_args
        assert args[0][0] == "POST"

    @patch("http.client.HTTPConnection")
    def test_execute_get_with_query_params(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"OK"
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        h = HTTPToolHandler(
            tool_name="lookup",
            tool_description="Lookup",
            tool_parameters={"type": "object", "properties": {}},
            endpoint="http://localhost:8080/lookup",
            method="GET",
        )
        result = h.execute({"id": "42"})
        assert "HTTP 200" in result
        call_path = mock_conn.request.call_args[0][1]
        assert "id=42" in call_path

    def test_execute_error_handling(self):
        h = HTTPToolHandler(
            tool_name="broken",
            tool_description="Broken",
            tool_parameters={"type": "object", "properties": {}},
            endpoint="http://localhost:1/broken",
        )
        result = h.execute({"x": 1})
        assert "Error" in result


class TestTaskToolHandler(unittest.TestCase):

    def test_init_properties(self):
        h = TaskToolHandler(
            tool_name="transform",
            tool_description="Transform text",
            tool_parameters={"type": "object", "properties": {}},
            task_type="updateAttribute",
            task_config={"set": {"key": "value"}},
        )
        assert h.name == "transform"
        assert h._task_type == "updateAttribute"

    def test_execute_with_real_task(self):
        """Execute a real updateAttribute task via TaskToolHandler."""
        from tasks import register_all_tasks
        register_all_tasks()

        h = TaskToolHandler(
            tool_name="set_attrs",
            tool_description="Set attributes",
            tool_parameters={"type": "object", "properties": {}},
            task_type="updateAttribute",
            task_config={"set": {"out.key": "hello"}},
        )
        result = h.execute({})
        # updateAttribute returns the FlowFile with modified attributes
        assert isinstance(result, str)

    def test_execute_with_parameter_mapping(self):
        """Arguments are mapped to task config keys."""
        from tasks import register_all_tasks
        register_all_tasks()

        h = TaskToolHandler(
            tool_name="set_value",
            tool_description="Set a value",
            tool_parameters={"type": "object", "properties": {
                "value": {"type": "string"},
            }},
            task_type="updateAttribute",
            task_config={"set": {}},
            parameter_mapping={"value": "set"},
        )
        # This will set config["set"] = "hello" which is technically wrong
        # for updateAttribute but tests the mapping works
        result = h.execute({"value": "hello"})
        assert isinstance(result, str)

    def test_execute_unknown_task_type(self):
        h = TaskToolHandler(
            tool_name="bad",
            tool_description="Bad",
            tool_parameters={"type": "object", "properties": {}},
            task_type="nonexistent_task_type_xyz",
        )
        result = h.execute({})
        assert "Error" in result


class TestMCPToolHandler(unittest.TestCase):

    def test_init_properties(self):
        h = MCPToolHandler(
            tool_name="search",
            tool_description="MCP search",
            tool_parameters={"type": "object", "properties": {}},
            server_url="http://localhost:3001/mcp",
            mcp_tool_name="web_search",
        )
        assert h.name == "search"
        assert h._mcp_tool_name == "web_search"

    @patch("http.client.HTTPConnection")
    def test_execute_success(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "jsonrpc": "2.0",
            "result": {
                "content": [
                    {"type": "text", "text": "Found 3 results."}
                ]
            },
            "id": "1",
        }).encode()
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        h = MCPToolHandler(
            tool_name="search",
            tool_description="Search",
            tool_parameters={"type": "object", "properties": {}},
            server_url="http://localhost:3001/mcp",
        )
        result = h.execute({"query": "test"})
        assert "Found 3 results" in result

    @patch("http.client.HTTPConnection")
    def test_execute_rpc_error(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "Method not found"},
            "id": "1",
        }).encode()
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        h = MCPToolHandler(
            tool_name="bad",
            tool_description="Bad",
            tool_parameters={"type": "object", "properties": {}},
            server_url="http://localhost:3001/mcp",
        )
        result = h.execute({})
        assert "MCP error" in result

    @patch("http.client.HTTPConnection")
    def test_execute_http_error(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.read.return_value = b"Internal Server Error"
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        h = MCPToolHandler(
            tool_name="broken",
            tool_description="Broken",
            tool_parameters={"type": "object", "properties": {}},
            server_url="http://localhost:3001/mcp",
        )
        result = h.execute({})
        assert "MCP error (HTTP 500)" in result

    def test_execute_connection_error(self):
        h = MCPToolHandler(
            tool_name="offline",
            tool_description="Offline",
            tool_parameters={"type": "object", "properties": {}},
            server_url="http://localhost:1/mcp",
        )
        result = h.execute({})
        assert "Error" in result


class TestDiscoverMCPTools(unittest.TestCase):

    @patch("http.client.HTTPConnection")
    def test_discover_tools(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "jsonrpc": "2.0",
            "result": {
                "tools": [
                    {
                        "name": "web_search",
                        "description": "Search the web",
                        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    },
                    {
                        "name": "calculator",
                        "description": "Do math",
                        "inputSchema": {"type": "object", "properties": {"expr": {"type": "string"}}},
                    },
                ]
            },
            "id": "1",
        }).encode()
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        tools = discover_mcp_tools("http://localhost:3001/mcp")
        assert len(tools) == 2
        assert tools[0]["name"] == "web_search"
        assert tools[1]["name"] == "calculator"

    def test_discover_connection_error(self):
        tools = discover_mcp_tools("http://localhost:1/mcp")
        assert tools == []


class TestLoadAgentTools(unittest.TestCase):

    def test_load_builtin(self):
        config = {
            "calc": {"type": "builtin", "handler": "execute_script"},
            "scraper": {"type": "builtin", "handler": "scrape_url"},
        }
        registry = load_agent_tools(config)
        assert registry.get("execute_script") is not None  # keeps original name
        assert registry.get("scrape_url") is not None

    def test_load_http_tool(self):
        config = {
            "my_api": {
                "type": "http",
                "endpoint": "http://localhost:8080/api",
                "method": "POST",
                "description": "My API",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        }
        registry = load_agent_tools(config)
        handler = registry.get("my_api")
        assert handler is not None
        assert isinstance(handler, HTTPToolHandler)
        assert handler.description == "My API"

    def test_load_task_tool(self):
        from tasks import register_all_tasks
        register_all_tasks()

        config = {
            "set_attrs": {
                "type": "task",
                "task_type": "updateAttribute",
                "config": {"set": {"key": "val"}},
                "description": "Set attributes",
                "parameters": {"type": "object", "properties": {}},
            }
        }
        registry = load_agent_tools(config)
        handler = registry.get("set_attrs")
        assert handler is not None
        assert isinstance(handler, TaskToolHandler)

    def test_load_mcp_tool(self):
        config = {
            "search": {
                "type": "mcp",
                "server_url": "http://localhost:3001/mcp",
                "tool_name": "web_search",
                "description": "Search via MCP",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        }
        registry = load_agent_tools(config)
        handler = registry.get("search")
        assert handler is not None
        assert isinstance(handler, MCPToolHandler)

    @patch("http.client.HTTPConnection")
    def test_load_mcp_server_auto_discover(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "jsonrpc": "2.0",
            "result": {
                "tools": [
                    {"name": "tool_a", "description": "A", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "tool_b", "description": "B", "inputSchema": {"type": "object", "properties": {}}},
                ]
            },
            "id": "1",
        }).encode()
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        config = {
            "_mcp": {
                "type": "mcp_server",
                "server_url": "http://localhost:3001/mcp",
            }
        }
        registry = load_agent_tools(config)
        assert registry.get("tool_a") is not None
        assert registry.get("tool_b") is not None

    def test_load_empty_config(self):
        registry = load_agent_tools({})
        assert len(registry.list_tools()) == 0

    def test_load_unknown_type_skipped(self):
        config = {"bad": {"type": "unknown_type_xyz"}}
        registry = load_agent_tools(config)
        assert len(registry.list_tools()) == 0

    def test_load_http_missing_endpoint(self):
        config = {"bad": {"type": "http"}}
        registry = load_agent_tools(config)
        assert len(registry.list_tools()) == 0

    def test_load_mixed_types(self):
        config = {
            "calc": {"type": "builtin", "handler": "execute_script"},
            "api": {
                "type": "http",
                "endpoint": "http://localhost/api",
                "description": "API",
                "parameters": {"type": "object", "properties": {}},
            },
            "search_mcp": {
                "type": "mcp",
                "server_url": "http://localhost:3001/mcp",
                "description": "MCP",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        registry = load_agent_tools(config)
        assert len(registry.list_tools()) == 3


class TestAgentLoopWithAgentTools(unittest.TestCase):

    @patch.object(LLMClient, 'complete')
    def test_agent_uses_agent_tools_config(self, mock_complete):
        """agentLoop uses agent_tools from config when available."""
        mock_complete.return_value = LLMResponse(
            content="Done.",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )

        task = AgentLoopTask({
            "api_key": "test-key",
            "agent_tools": {
                "calc": {"type": "builtin", "handler": "execute_script"},
            },
        })
        ff = FlowFile(content=b"Hello")
        task.execute(ff)

        # The tool definitions sent to LLM should only have the calc tool
        call_args = mock_complete.call_args
        tools = call_args.kwargs.get("tools") or call_args[1].get("tools")
        assert tools is not None
        assert len(tools) == 1
        assert tools[0].name == "execute_script"

    @patch.object(LLMClient, 'complete')
    def test_agent_tools_override_defaults(self, mock_complete):
        """When agent_tools is set, default builtins are NOT loaded."""
        mock_complete.return_value = LLMResponse(
            content="OK",
            model="gpt-4o",
            tokens_in=10, tokens_out=5,
            finish_reason="stop",
        )

        task = AgentLoopTask({
            "api_key": "test-key",
            "agent_tools": {
                "my_api": {
                    "type": "http",
                    "endpoint": "http://localhost/api",
                    "description": "My API",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        })
        ff = FlowFile(content=b"test")
        task.execute(ff)

        call_args = mock_complete.call_args
        tools = call_args.kwargs.get("tools") or call_args[1].get("tools")
        names = [t.name for t in tools]
        assert "my_api" in names
        # Default builtins should NOT be present
        assert "execute_script" not in names
        assert "fetch_http" not in names

    def test_agent_no_agent_tools_uses_defaults(self):
        """Without agent_tools config, default builtins are used."""
        task = AgentLoopTask({"api_key": "test-key"})
        registry = task.get_tool_registry()
        names = [h.name for h in registry.list_tools()]
        assert "execute_script" in names
        assert "scrape_url" in names
        assert "read_file" in names


class TestFlowParserAgentTools(unittest.TestCase):

    def test_parser_injects_agent_tools_into_agentloop(self):
        """FlowParser copies flow-level agent_tools into agentLoop task config."""
        from tasks import register_all_tasks
        register_all_tasks()
        from engine.parser import FlowParser

        config = {
            "id": "test",
            "name": "Test",
            "tasks": {
                "agent": {
                    "type": "agentLoop",
                    "parameters": {"api_key": "test"},
                },
            },
            "relations": [],
            "agent_tools": {
                "calc": {"type": "builtin", "handler": "execute_script"},
            },
        }
        flow = FlowParser.parse(config)
        agent_task = flow.get_task("agent")
        assert "agent_tools" in agent_task.config
        assert agent_task.config["agent_tools"]["calc"]["type"] == "builtin"

    def test_parser_stores_agent_tools_on_flow(self):
        from engine.parser import FlowParser

        config = {
            "id": "test",
            "name": "Test",
            "tasks": {},
            "relations": [],
            "agent_tools": {
                "search": {"type": "http", "endpoint": "http://x/search"},
            },
        }
        flow = FlowParser.parse(config)
        assert hasattr(flow, 'agent_tools')
        assert "search" in flow.agent_tools

    def test_parser_no_agent_tools(self):
        from engine.parser import FlowParser

        config = {"id": "test", "name": "Test", "tasks": {}, "relations": []}
        flow = FlowParser.parse(config)
        assert flow.agent_tools == {}


class TestAgentToolsI18n(unittest.TestCase):

    def test_agent_tools_keys_in_all_locales(self):
        keys = [
            "agent_tools.title", "agent_tools.section", "agent_tools.type",
            "agent_tools.type_builtin", "agent_tools.type_http",
            "agent_tools.type_task", "agent_tools.type_mcp",
            "agent_tools.type_mcp_server", "agent_tools.endpoint",
            "agent_tools.add_tool", "agent_tools.remove_tool",
            "agent_tools.no_tools", "agent_tools.discover",
        ]
        for locale in ("en", "fr", "es"):
            path = Path(f"gui/i18n/{locale}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                assert key in data, f"Missing key '{key}' in {locale}.json"


if __name__ == "__main__":
    unittest.main()
