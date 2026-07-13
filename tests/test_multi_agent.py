"""Tests for SubAgentExecutor — multi-agent orchestration."""

import inspect
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
    """Create a mock LLMClient that returns responses in sequence.

    Both `complete` and `complete_stream` draw from the same iterator —
    the loop uses complete_stream; the post-max-iterations synthesis
    uses complete. Sharing the iterator keeps tests that set up an exact
    response sequence deterministic.
    """
    client = MagicMock(spec=LLMClient)
    _it = iter(responses)
    def _next(*a, **kw):
        r = next(_it)
        if isinstance(r, Exception):
            raise r
        return r
    client.complete = MagicMock(side_effect=_next)
    client.complete_stream = MagicMock(side_effect=_next)
    # SubAgentExecutor calls clone_for_call() before every LLM call;
    # return self so the mock chain stays addressable in tests.
    client.clone_for_call = MagicMock(return_value=client)
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
        assert task.internal is False
        assert task.ephemeral is False
        assert task.read_only is False

    def test_internal_ephemeral_task_has_no_trace_or_persistence(self):
        client = make_client_mock([simple_response("plan")])
        registry = make_registry()
        executor = SubAgentExecutor(
            client, registry,
            client_resolver=lambda *_args: (client, None),
            on_event=None)
        store = MagicMock()
        store.generate_id.return_value = "ephemeral-conv"
        task = AgentTask(
            id="internal-1", agent_name="advisor", message="analyze",
            system_prompt="plan only", llm_service="advisor_llm",
            user_id="alice", parent_conversation_id="parent-conv",
            internal=True, ephemeral=True, read_only=True)

        from core.tool_approval import ToolApprovalGate
        marked = []
        _orig_mark = ToolApprovalGate.mark_advisor_read_only.__func__

        def _spy_mark(cls, conversation_id):
            marked.append(conversation_id)
            _orig_mark(cls, conversation_id)

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store), \
             patch.object(ToolApprovalGate, "mark_advisor_read_only",
                          classmethod(_spy_mark)):
            result = executor.execute_agent(task)

        assert result.status == "completed"
        store.create_display_trace.assert_not_called()
        store.load.assert_not_called()
        store.save.assert_not_called()
        # The read-only mode lives in the in-process ToolApprovalGate
        # registry (extras would silently no-op for an unpersisted conv)
        # and is always unregistered when the run finishes.
        store.set_extra.assert_not_called()
        assert marked == ["ephemeral-conv"]
        assert not ToolApprovalGate.is_advisor_read_only_conv("ephemeral-conv")
        store.delete.assert_called_once_with(
            "ephemeral-conv", user_id="alice")
        assert client.complete_stream.call_args.kwargs[
            "call_ephemeral_stream"] is True
        executor.shutdown()


class TestAgentResult:
    def test_to_dict(self):
        r = AgentResult(task_id="t1", agent_name="test", response="ok", status="completed")
        d = r.to_dict()
        assert d["task_id"] == "t1"
        assert d["response"] == "ok"
        assert d["status"] == "completed"
        assert "tools_called" in d


class TestSingleAgentExecution:
    def test_sub_agent_tool_execution_records_registry_metrics(self):
        """Sub-agent tool calls must go through ToolRegistry.execute."""
        ToolRegistry.reset_metrics()
        registry = make_registry(EchoToolHandler())
        executor = SubAgentExecutor(make_client_mock([]), registry)

        result = executor._execute_tool(
            LLMToolCall(id="call_echo", name="echo", arguments={"text": "hi"}),
            {h.name: h for h in registry.list_tools()},
        )

        assert result == "Echo: hi"
        missing = executor._execute_tool(
            LLMToolCall(id="call_missing", name="missing", arguments={}),
            {h.name: h for h in registry.list_tools()},
        )

        assert "unknown tool" in missing
        metrics = ToolRegistry.get_metrics()
        assert metrics["echo"]["calls"] == 1
        assert metrics["echo"]["successes"] == 1
        assert metrics["missing"]["errors"] == 1

    def test_read_only_task_blocks_unadvertised_tool(self):
        registry = make_registry(EchoToolHandler())
        executor = SubAgentExecutor(make_client_mock([]), registry)

        result = executor._execute_tool(
            LLMToolCall(
                id="call_echo", name="echo", arguments={"text": "write"}),
            {}, read_only=True,
        )

        assert "blocked for this read-only advisor" in result

        unexposed = executor._execute_tool(
            LLMToolCall(id="call_notify", name="notify_user", arguments={}),
            {}, read_only=True,
        )
        assert "blocked for this read-only advisor" in unexposed

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
        call_args = client.complete_stream.call_args
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
        """resolve_agent_task loads prompt from ResourceStore, runtime from conv_agent_config."""
        with patch("core.resource_store.ResourceStore.instance") as mock_store, \
             patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"analyst": {"llm_service": "svc_a", "definition": "analyst"}}), \
             patch("core.conv_agent_config.get_agent_config",
                   return_value={"llm_service": "svc_a", "definition": "analyst", "params": {}}):
            mock_store.return_value.get_any.return_value = {
                "prompt": "You are an analyst",
            }
            task = resolve_agent_task(
                "analyst", "Analyze this", "user1",
                conversation_id="conv1",
            )

            assert task.agent_name == "analyst"
            assert task.message == "Analyze this"
            assert "You are an analyst" in task.system_prompt
            assert task.llm_service == "svc_a"

    def test_resolve_not_found(self):
        """resolve_agent_task raises KeyError if agent not in conversation."""
        with pytest.raises(KeyError, match="cannot be delegated"):
            resolve_agent_task("nope", "Hi", "user1")

    def test_resolve_not_in_conv(self):
        """resolve_agent_task raises KeyError if agent not in conv_agents."""
        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={}):
            with pytest.raises(KeyError, match="not in conversation"):
                resolve_agent_task("nope", "Hi", "user1",
                                   conversation_id="conv1")


class TestBroadcastAgents:
    def test_broadcast_all_uses_conversation_agents(self):
        from tasks.ai.agent_side_channels import AgentSideChannelsMixin

        src = inspect.getsource(AgentSideChannelsMixin._broadcast_agents)
        assert "get_all_agent_configs(conversation_id)" in src
        assert "rs.list_all(\"agent\", user_id)" not in src
        assert "_resolve_agent_client(\"\", user_id, conversation_id)" not in src
        assert "conversation_id=conversation_id" in src
        assert "_resolve_llm_service(\n                    svc_id, uid, conversation_id)" in src


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

        call_args = client.complete_stream.call_args
        tools_passed = call_args.kwargs.get("tools") or []
        tool_names = [t.name for t in tools_passed]
        assert "echo" in tool_names
        assert "delegate" not in tool_names
        executor.shutdown()


def test_conv_agent_max_depth_does_not_clobber_max_iterations():
    """Regression: conv_agents `max_depth` is sub-agent recursion depth only.

    It must NOT be assigned into the tool-use loop cap `max_iterations` (a
    per-LLM-service setting). Conflating them silently throttled tool-using
    agents whose max_depth was lowered to forbid delegation (e.g. a help bot
    with max_depth=1 died after one tool call). See agent_context.py.
    """
    from pathlib import Path
    import re

    # agent_context.py split into _agentctx_*; strip state-obj `st.` namespacing
    src = re.sub(r"\bst\.", "", "".join(
        Path(f"tasks/ai/{_f}").read_text(encoding="utf-8")
        for _f in ("agent_context.py", "_agentctx_base.py", "_agentctx_p1.py",
                   "_agentctx_p2.py", "_agentctx_p3.py")))
    # The old conflation explicitly assigned the loop cap from max_depth.
    assert "max_iterations = _md" not in src
    # max_iterations stays resolved from service/config, never from max_depth.
    assert 'max_iterations = int(_cfg("max_iterations", 1000))' in src
