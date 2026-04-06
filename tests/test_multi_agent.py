"""Tests for SubAgentExecutor — multi-agent orchestration."""

import time
import pytest
from unittest.mock import MagicMock, patch

from core.agent_executor import (
    SubAgentExecutor, AgentTask, AgentResult,
    resolve_agent_task, _get_depth, _set_depth, MAX_GLOBAL_DEPTH,
)
from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMToolCall
from core.tool_registry import ToolRegistry, ToolHandler


# ── Helpers ───────────────────────────────────────────────────────────

class EchoToolHandler(ToolHandler):
    """Simple tool that echoes its input."""
    @property
    def name(self): return "echo"
    @property
    def description(self): return "Echoes input"
    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {"text": {"type": "string"}}}
    def execute(self, arguments):
        return f"Echo: {arguments.get('text', '')}"


class FailToolHandler(ToolHandler):
    """Tool that always fails."""
    @property
    def name(self): return "fail_tool"
    @property
    def description(self): return "Always fails"
    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}
    def execute(self, arguments):
        raise RuntimeError("Tool failure!")


def make_registry(*handlers):
    reg = ToolRegistry()
    for h in handlers:
        reg.register(h)
    return reg


def make_client_mock(responses):
    """Create a mock LLMClient that returns responses in sequence."""
    client = MagicMock(spec=LLMClient)
    client.complete = MagicMock(side_effect=responses)
    return client


def simple_response(content="Done", tool_calls=None):
    return LLMResponse(
        content=content,
        model="test-model",
        tokens_in=10,
        tokens_out=20,
        finish_reason="stop",
        tool_calls=tool_calls or [],
    )


def tool_call_response(tool_name, arguments, content=""):
    return LLMResponse(
        content=content,
        model="test-model",
        tokens_in=10,
        tokens_out=5,
        finish_reason="tool_calls",
        tool_calls=[LLMToolCall(id=f"call_{tool_name}", name=tool_name, arguments=arguments)],
    )


# ── Tests ─────────────────────────────────────────────────────────────

class TestAgentTask:
    def test_defaults(self):
        task = AgentTask(id="t1", agent_name="test", message="hello")
        assert task.max_iterations == 50
        assert task.max_depth == 1
        assert task.timeout == 300
        assert task.tools is None


class TestAgentResult:
    def test_to_dict(self):
        r = AgentResult(task_id="t1", agent_name="test", response="ok", status="completed")
        d = r.to_dict()
        assert d["task_id"] == "t1"
        assert d["response"] == "ok"
        assert d["status"] == "completed"
        assert "tools_called" in d


class TestSingleAgentExecution:
    def test_simple_response(self):
        """Agent gets a direct response (no tool calls)."""
        client = make_client_mock([simple_response("Hello world")])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="greeter", message="Hi",
            system_prompt="You greet people",
        )
        result = executor.execute_agent(task)

        assert result.status == "completed"
        assert result.response == "Hello world"
        assert result.iterations == 1
        assert result.tokens_in == 10
        assert result.tokens_out == 20
        executor.shutdown()

    def test_tool_use_loop(self):
        """Agent calls a tool, then produces final response."""
        client = make_client_mock([
            tool_call_response("echo", {"text": "test"}),
            simple_response("Final answer"),
        ])
        registry = make_registry(EchoToolHandler())
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="worker", message="Do something",
            system_prompt="Use tools",
        )
        result = executor.execute_agent(task)

        assert result.status == "completed"
        assert result.response == "Final answer"
        assert result.iterations == 2
        assert "echo" in result.tools_called
        executor.shutdown()

    def test_tool_error_handled(self):
        """Tool errors are passed back to the LLM as error messages."""
        client = make_client_mock([
            tool_call_response("fail_tool", {}),
            simple_response("I handled the error"),
        ])
        registry = make_registry(FailToolHandler())
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="worker", message="Try",
            system_prompt="Handle errors",
        )
        result = executor.execute_agent(task)

        assert result.status == "completed"
        assert result.response == "I handled the error"
        executor.shutdown()

    def test_unknown_tool(self):
        """Unknown tool call returns error message."""
        client = make_client_mock([
            tool_call_response("nonexistent", {}),
            simple_response("Ok"),
        ])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="worker", message="Try",
            system_prompt="...",
        )
        result = executor.execute_agent(task)
        assert result.status == "completed"
        executor.shutdown()

    def test_max_iterations(self):
        """Agent forced to synthesize when max iterations reached."""
        # Always returns tool calls — will hit max_iterations
        responses = [
            tool_call_response("echo", {"text": f"iter{i}"})
            for i in range(5)
        ] + [simple_response("Forced synthesis")]

        client = make_client_mock(responses)
        registry = make_registry(EchoToolHandler())
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="looper", message="Loop",
            system_prompt="...", max_iterations=5,
        )
        result = executor.execute_agent(task)

        assert result.status == "completed"
        assert result.response == "Forced synthesis"
        executor.shutdown()

    def test_llm_error(self):
        """LLM client error results in error status."""
        client = make_client_mock([Exception("API error")])
        # MagicMock side_effect with Exception raises it
        client.complete = MagicMock(side_effect=Exception("API error"))
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="broken", message="Hi",
            system_prompt="...",
        )
        result = executor.execute_agent(task)
        assert result.status == "error"
        assert "API error" in result.error
        executor.shutdown()


class TestToolWhitelist:
    def test_whitelist_filters_tools(self):
        """Only whitelisted tools are available to sub-agent."""
        client = make_client_mock([simple_response("Done")])
        registry = make_registry(EchoToolHandler(), FailToolHandler())
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="filtered", message="Hi",
            system_prompt="...", tools=["echo"],  # only echo allowed
        )
        result = executor.execute_agent(task)
        assert result.status == "completed"

        # Verify that only 1 tool def was passed to LLM
        call_args = client.complete.call_args
        tools_passed = call_args.kwargs.get("tools") or call_args[1].get("tools", [])
        assert len(tools_passed) == 1
        assert tools_passed[0].name == "echo"
        executor.shutdown()


class TestDepthControl:
    def test_depth_tracking(self):
        assert _get_depth() == 0
        _set_depth(2)
        assert _get_depth() == 2
        _set_depth(0)
        assert _get_depth() == 0

    def test_depth_limit_blocks(self):
        """Agent at max depth gets error instead of executing."""
        client = make_client_mock([])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        _set_depth(1)
        try:
            task = AgentTask(
                id="t1", agent_name="deep", message="Hi",
                system_prompt="...", max_depth=1,
            )
            result = executor.execute_agent(task)
            assert result.status == "error"
            assert "depth" in result.error.lower()
            # LLM should NOT have been called
            client.complete.assert_not_called()
        finally:
            _set_depth(0)
            executor.shutdown()

    def test_depth_restored_after_execution(self):
        """Depth counter is restored even if agent errors."""
        client = make_client_mock([simple_response("ok")])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        assert _get_depth() == 0
        task = AgentTask(
            id="t1", agent_name="test", message="Hi",
            system_prompt="...",
        )
        executor.execute_agent(task)
        assert _get_depth() == 0
        executor.shutdown()

    def test_global_max_depth(self):
        """MAX_GLOBAL_DEPTH caps the agent's own max_depth."""
        client = make_client_mock([])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        _set_depth(MAX_GLOBAL_DEPTH)
        try:
            task = AgentTask(
                id="t1", agent_name="deep", message="Hi",
                system_prompt="...", max_depth=999,  # agent wants more
            )
            result = executor.execute_agent(task)
            assert result.status == "error"
        finally:
            _set_depth(0)
            executor.shutdown()


class TestParallelSpawn:
    def test_spawn_wait(self):
        """Spawn multiple agents and wait for results."""
        client = make_client_mock([
            simple_response("Result A"),
            simple_response("Result B"),
        ])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry, max_workers=2)

        tasks = [
            AgentTask(id="a", agent_name="agent_a", message="Task A", system_prompt="..."),
            AgentTask(id="b", agent_name="agent_b", message="Task B", system_prompt="..."),
        ]
        results = executor.spawn(tasks, wait=True)

        assert len(results) == 2
        assert all(r.status == "completed" for r in results)
        responses = {r.task_id: r.response for r in results}
        assert "a" in responses
        assert "b" in responses
        executor.shutdown()

    def test_spawn_no_wait(self):
        """Spawn without waiting returns pending results."""
        client = make_client_mock([
            simple_response("A"),
            simple_response("B"),
        ])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        tasks = [
            AgentTask(id="a", agent_name="a", message="Hi", system_prompt="..."),
        ]
        results = executor.spawn(tasks, wait=False)
        assert results[0].status == "pending"

        # Wait a bit then check
        time.sleep(0.2)
        final = executor.get_results(["a"])
        assert final[0].status == "completed"
        executor.shutdown()

    def test_get_results_unknown_id(self):
        """get_results with unknown task ID returns error."""
        client = make_client_mock([])
        registry = make_registry()
        executor = SubAgentExecutor(client, registry)

        results = executor.get_results(["nonexistent"])
        assert results[0].status == "error"
        assert "No task found" in results[0].error
        executor.shutdown()



class TestResolveAgentTask:
    def test_resolve_from_store(self):
        """resolve_agent_task loads agent definition from ResourceStore."""
        with patch("core.resource_store.ResourceStore.instance") as mock_store:
            mock_store.return_value.get_any.return_value = {
                "prompt": "You are an analyst",
                "model": "gpt-4",
                "tools": ["execute_script", "web_search"],
                "max_depth": 2,
                "timeout": 60,
            }
            task = resolve_agent_task("analyst", "Analyze this", "user1")

            assert task.agent_name == "analyst"
            assert task.message == "Analyze this"
            assert "You are an analyst" in task.system_prompt
            assert task.model == "gpt-4"
            assert task.tools == ["execute_script", "web_search"]
            assert task.max_depth == 2
            assert task.timeout == 60

    def test_resolve_not_found(self):
        """resolve_agent_task raises KeyError if agent not in store."""
        with patch("core.resource_store.ResourceStore.instance") as mock_store:
            mock_store.return_value.get_any.return_value = None
            with pytest.raises(KeyError, match="not found"):
                resolve_agent_task("nope", "Hi", "user1")


class TestDelegateExcluded:
    def test_delegate_excluded_by_default(self):
        """delegate tool is excluded from sub-agent tools by default."""
        class DelegateHandler(ToolHandler):
            @property
            def name(self): return "delegate"
            @property
            def description(self): return "Delegate"
            @property
            def parameters_schema(self): return {"type": "object"}
            def execute(self, args): return ""

        client = make_client_mock([simple_response("ok")])
        registry = make_registry(EchoToolHandler(), DelegateHandler())
        executor = SubAgentExecutor(client, registry)

        task = AgentTask(
            id="t1", agent_name="test", message="Hi",
            system_prompt="...", tools=["echo"],  # explicit whitelist excludes delegate
        )
        executor.execute_agent(task)

        call_args = client.complete.call_args
        tools_passed = call_args.kwargs.get("tools") or []
        tool_names = [t.name for t in tools_passed]
        assert "echo" in tool_names
        assert "delegate" not in tool_names
        executor.shutdown()
