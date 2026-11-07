"""Isolated delegates receive durable inbox data, notices and explicit read ack."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import agent_inbox
from core.agent_executor import (
    AgentTask, SubAgentExecutor, drain_live_delegate_messages,
    queue_live_delegate_message, register_live_delegate, unregister_live_delegate,
)
from core.agent_inbox_store import AgentInboxStore
from core.handlers.inbox import ReadInboxHandler
from core.llm_client import LLMMessage, LLMToolCall, stamp_message
from core.pending_queue import PendingQueue
from tests.test_multi_agent import (
    make_client_mock, make_registry, simple_response, tool_call_response,
)


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    store = AgentInboxStore(tmp_path / "inbox.sqlite3")
    monkeypatch.setattr(AgentInboxStore, "_instance", store)
    PendingQueue.drop_cache()
    from tasks.ai.agent_loop import AgentLoopTask
    active = SimpleNamespace(
        _active_contexts={}, _active_claude_client={},
        _active_contexts_lock=threading.Lock())
    monkeypatch.setattr(AgentLoopTask, "_live_instance", active)
    yield store, active
    PendingQueue.drop_cache()


@pytest.mark.parametrize("mode,ephemeral", [
    ("isolated", False), ("last:2", False), ("summary:20", False),
    ("full", False), ("isolated", True),
])
def test_task_and_parent_context_only_arrive_after_read_inbox(inbox, mode, ephemeral):
    client = make_client_mock([])
    snapshots = []
    task = AgentTask(
        id="task-inbox", agent_name="worker", message="PRIVATE TASK BODY",
        source_agent="caller", user_id="alice", llm_service="svc",
        parent_conversation_id="parent", context_mode=mode, ephemeral=ephemeral,
        system_prompt="Configured system prompt", tools=[],
        context_messages=[
            LLMMessage(role="assistant", content="PRIVATE PARENT CONTEXT",
                       conversation_id="parent"),
            {"role": "system", "content": "PRIVATE PARENT SYSTEM CONTEXT"},
        ],
    )

    def complete_stream(**kwargs):
        snapshots.append([(m.role, m.content) for m in kwargs["messages"]])
        cid = kwargs["call_conversation_id"]
        assert cid == client._conversation_id
        assert client._agent_name == "worker"
        assert client._user_id == "alice"
        if mode == "full" and not ephemeral:
            assert cid == "parent"
        elif not ephemeral:
            assert cid == "parent::task::task-inbox"
        else:
            assert cid != "parent" and cid != "parent::task::task-inbox"
        assert [tool.name for tool in kwargs["tools"]] == ["read_inbox"]
        if len(snapshots) == 1:
            assert all("PRIVATE" not in str(text) for _, text in snapshots[0])
            assert "Configured system prompt" in snapshots[0][0][1]
            assert "Call read_inbox() NOW" in snapshots[0][-1][1]
            assert agent_inbox.unread_count(cid, "worker") == 2
            return tool_call_response("read_inbox", {})
        output = snapshots[-1][-1]
        assert output[0] == "tool"
        assert '<tool_output tool="read_inbox">' in output[1]
        assert "PRIVATE TASK BODY" in output[1]
        assert "PRIVATE PARENT CONTEXT" in output[1]
        assert "PRIVATE PARENT SYSTEM CONTEXT" in output[1]
        assert agent_inbox.unread_count(cid, "worker") == 0
        return simple_response("acknowledged")

    client.complete_stream.side_effect = complete_stream
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(task)
    finally:
        executor.shutdown()
    assert result.status == "completed", result.error
    assert result.response == "acknowledged"
    assert result.tools_called == ["read_inbox"]


@pytest.mark.parametrize("provider", [
    "claude-code", "claude-code-interactive", "codex-interactive",
    "antigravity-interactive", "codex-app-server", "gemini",
    "cc_mcp", "codex_mcp", "agy_mcp",
])
@pytest.mark.parametrize("session", ["new", "already-live", "lost-after-first-turn"])
def test_cli_delegate_context_tracks_live_session_without_replaying(inbox, provider, session):
    from core.llm_providers.cli_shared import LLMCliSharedMixin, cli_delta_text

    client = make_client_mock([])
    client.provider = provider
    client._pawflow_context_is_delta = True  # stale resolver state must not leak
    live = session == "already-live"
    attempts = []
    submitted = []

    def complete_stream(**kwargs):
        nonlocal live
        attempts.append(bool(getattr(client, "_pawflow_context_is_delta", False)))
        if live:
            LLMCliSharedMixin._cli_require_delta_context(client, provider)
            prompt = cli_delta_text(kwargs["messages"])
            assert "Configured system prompt" not in prompt
            assert "ignored first answer" not in prompt
        else:
            LLMCliSharedMixin._cli_require_cold_context(client, provider)
            assert kwargs["messages"][0].content.startswith("Configured system prompt")
            if submitted:
                assert any(m.content == "ignored first answer" for m in kwargs["messages"])
        submitted.append("live" if live else "cold")
        if len(submitted) == 1:
            live = session != "lost-after-first-turn"
            return simple_response("ignored first answer")
        agent_inbox.read_inbox(kwargs["call_conversation_id"], "worker")
        return simple_response("read and handled")

    client.complete_stream.side_effect = complete_stream
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="cli-context", agent_name="worker", message="private task",
            source_agent="caller", parent_conversation_id="parent",
            system_prompt="Configured system prompt", max_iterations=2))
    finally:
        executor.shutdown()
    assert result.status == "completed", result.error
    assert result.response == "read and handled"
    assert len(submitted) == 2
    expected = {"new": [False, True], "already-live": [False, True, True],
                "lost-after-first-turn": [False, True, False]}
    assert attempts == expected[session]
    assert result.iterations == 2


def test_cli_context_rebuild_does_not_retry_submitted_failures(inbox):
    client = make_client_mock([])
    client.provider = "codex-interactive"
    client.complete_stream.side_effect = RuntimeError("upstream request failed after submission")
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="no-replay", agent_name="worker", message="task",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "error"
    assert "after submission" in result.error
    assert client.complete_stream.call_count == 1


def test_cli_context_rebuild_cannot_spin_on_repeated_mismatch(inbox):
    from core.llm_client import DeltaContextRequired

    client = make_client_mock([])
    client.provider = "codex-interactive"
    client.complete_stream.side_effect = DeltaContextRequired("persistent mismatch")
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="bounded-context", agent_name="worker", message="task",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "error"
    assert "persistent mismatch" in result.error
    assert client.complete_stream.call_count == 2


def test_cli_context_rebuild_stops_when_user_cancels(inbox):
    from core.agent_executor import cancel_sub_agent_task
    from core.llm_client import DeltaContextRequired

    client = make_client_mock([])
    client.provider = "codex-interactive"

    def mismatch_then_cancel(**kwargs):
        cancel_sub_agent_task("cancel-context")
        raise DeltaContextRequired("live session discovered")

    client.complete_stream.side_effect = mismatch_then_cancel
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="cancel-context", agent_name="worker", message="task",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "cancelled"
    assert client.complete_stream.call_count == 1


def test_final_synthesis_uses_the_run_client_with_its_cli_context(inbox, monkeypatch):
    from core.llm_providers.cli_shared import LLMCliSharedMixin
    from tests.test_multi_agent import EchoToolHandler

    resolver = make_client_mock([])
    run_client = make_client_mock([])
    run_client.provider = "codex-interactive"
    resolver.clone_for_call.return_value = run_client
    def stream(**kwargs):
        agent_inbox.read_inbox(kwargs["call_conversation_id"], "worker")
        return tool_call_response("echo", {"text": "value"})

    run_client.complete_stream.side_effect = stream

    def synthesis(**kwargs):
        LLMCliSharedMixin._cli_require_delta_context(run_client, "codex-interactive")
        assert "Maximum iterations reached" in kwargs["messages"][-1].content
        return simple_response("synthesis from the same session")

    run_client.complete.side_effect = synthesis
    monkeypatch.setattr("core.tool_approval.ToolApprovalGate.check", lambda *args, **kwargs: "approved")
    executor = SubAgentExecutor(resolver, make_registry(EchoToolHandler()))
    try:
        result = executor.execute_agent(AgentTask(
            id="synthesis-context", agent_name="worker", message="task",
            source_agent="caller", parent_conversation_id="parent", max_iterations=1))
    finally:
        executor.shutdown()
    assert result.status == "completed", result.error
    assert result.response == "synthesis from the same session"
    resolver.complete.assert_not_called()
    assert run_client.complete.call_count == 1


def test_wrapped_read_inbox_uses_exact_subconversation(inbox):
    reader = ReadInboxHandler()
    reader.set_conversation_id("parent")
    reader.set_source_agent("parent-agent")
    registry = make_registry(reader)
    executor = SubAgentExecutor(make_client_mock([]), registry)
    for cid, agent, body in [
        ("parent", "parent-agent", "parent private"),
        ("parent::task::t", "worker", "worker private"),
    ]:
        agent_inbox.enqueue_item(
            cid, agent, stamp_message({"role": "user", "content": body}, cid),
            "delegate_message", "caller")
    try:
        result = executor._execute_tool(
            LLMToolCall(id="read", name="use_tool",
                        arguments={"tool_name": "read_inbox", "arguments": "{}"}),
            {}, "worker", "parent::task::t", "alice")
    finally:
        executor.shutdown()
    assert "worker private" in result
    assert "parent private" not in result
    assert agent_inbox.unread_count("parent::task::t", "worker") == 0
    assert agent_inbox.unread_count("parent", "parent-agent") == 1


def test_unread_task_repeats_three_turns_then_visible_error(inbox):
    client = make_client_mock([simple_response("false success") for _ in range(3)])
    events = []
    prompts = []

    def complete_stream(**kwargs):
        prompts.append([m.content for m in kwargs["messages"]])
        return simple_response("false success")

    client.complete_stream.side_effect = complete_stream
    executor = SubAgentExecutor(
        client, make_registry(), on_event=lambda event, data: events.append((event, data)))
    try:
        result = executor.execute_agent(AgentTask(
            id="unread", agent_name="worker", message="unread task body",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "error"
    assert result.response == ""
    assert "3 turns" in result.error
    assert len(prompts) == 3
    assert all("Call read_inbox() NOW" in prompt[-1] for prompt in prompts)
    assert agent_inbox.unread_count("parent::task::unread", "worker") == 1
    assert any(event == "sub_agent_warning" for event, _ in events)
    assert events[-1][0] == "sub_agent_done"
    assert events[-1][1]["status"] == "error"


def test_iteration_limit_cannot_synthesize_success_without_reading(inbox):
    client = make_client_mock([tool_call_response("unknown", {})])
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="limit", agent_name="worker", message="task",
            source_agent="caller", parent_conversation_id="parent", max_iterations=1))
    finally:
        executor.shutdown()
    assert result.status == "error"
    assert "unread inbox" in result.error
    client.complete.assert_not_called()


@pytest.mark.parametrize("provider", ["openai", "claude-code-interactive", "codex-interactive"])
def test_followup_during_run_never_interrupts_or_starts_worker(inbox, monkeypatch, provider):
    from tasks.ai.agent_loop import AgentLoopTask
    wake = MagicMock()
    monkeypatch.setattr(AgentLoopTask, "wake_agent", wake)
    client = make_client_mock([])
    client.provider = provider
    pasted = threading.Event()
    notices = []

    def send_queued_message(notice, **kwargs):
        notices.append((notice, kwargs))
        pasted.set()
        return True

    client.send_queued_message.side_effect = send_queued_message
    calls = []

    def complete_stream(**kwargs):
        calls.append([(m.role, m.content) for m in kwargs["messages"]])
        if len(calls) == 1:
            # Simulate the native CLI MCP read, or an API read, during the call.
            agent_inbox.read_inbox(kwargs["call_conversation_id"], "worker")
            assert queue_live_delegate_message("parent", "caller", "worker", "PRIVATE FOLLOWUP")
            if provider != "openai":
                assert pasted.wait(1)
            return tool_call_response("read_inbox", {})
        assert any(role == "tool" and "PRIVATE FOLLOWUP" in str(content)
                   for role, content in calls[-1])
        return simple_response("done")

    client.complete_stream.side_effect = complete_stream
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="followup", agent_name="worker", message="initial task",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "completed", result.error
    client.send_user_message.assert_not_called()
    wake.assert_not_called()
    if provider != "openai":
        assert len(notices) == 1
        assert "Call read_inbox() NOW" in notices[0][0]
        assert "PRIVATE FOLLOWUP" not in notices[0][0]
        assert notices[0][1]["conversation_id"] == "parent::task::followup"


@pytest.mark.parametrize("mode,cid", [
    ("isolated", "parent::task::followup"), ("full", "parent"),
    ("isolated", "ephemeral-exact"),
])
def test_live_queue_is_durable_and_drain_returns_notice_only(inbox, monkeypatch, mode, cid):
    task = AgentTask(id="followup", agent_name="worker", message="initial",
                     context_mode=mode)
    register_live_delegate("parent", "caller", "worker", task.id, None, task,
                           conversation_id=cid)
    try:
        assert queue_live_delegate_message("parent", "caller", "worker", "DURABLE BODY")
        monkeypatch.setattr(AgentInboxStore, "_instance",
                            AgentInboxStore(inbox[0]._database_path_override))
        PendingQueue.drop_cache()
        notices = drain_live_delegate_messages("parent", "caller", "worker", task.id)
        assert len(notices) == 1
        assert "Call read_inbox() NOW" in notices[0]
        assert "DURABLE BODY" not in notices[0]
        assert drain_live_delegate_messages("parent", "caller", "worker", task.id) == []
        assert agent_inbox.unread_count(cid, "worker") == 1
        assert "DURABLE BODY" in agent_inbox.read_inbox(cid, "worker")
    finally:
        unregister_live_delegate("parent", "caller", "worker")


def test_read_after_repeated_notice_restores_normal_completion(inbox):
    client = make_client_mock([
        simple_response("ignored notice"), tool_call_response("read_inbox", {}),
        simple_response("read and handled"),
    ])
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="retry-read", agent_name="worker", message="task to read",
            source_agent="caller", parent_conversation_id="parent"))
    finally:
        executor.shutdown()
    assert result.status == "completed"
    assert result.response == "read and handled"
    assert agent_inbox.unread_count("parent::task::retry-read", "worker") == 0
    assert inbox[0].notice_state("parent::task::retry-read", "worker")[1] == 0


def test_internal_instructions_keep_their_designed_inline_flow(inbox):
    client = make_client_mock([simple_response("internal result")])
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="internal", agent_name="advisor", message="internal instruction",
            source_agent="system", parent_conversation_id="parent", internal=True,
            context_messages=[{"role": "user", "content": "internal context"}]))
    finally:
        executor.shutdown()
    assert result.status == "completed"
    messages = client.complete_stream.call_args.kwargs["messages"]
    assert [m.content for m in messages][-2:] == ["internal context", "internal instruction"]
    assert agent_inbox.unread_count("parent::task::internal", "advisor") == 0


def test_followup_during_synthesis_prevents_false_success(inbox):
    client = make_client_mock([tool_call_response("read_inbox", {})])

    def synthesize(**kwargs):
        assert queue_live_delegate_message("parent", "caller", "worker", "late followup")
        return simple_response("premature success")

    client.complete.side_effect = synthesize
    executor = SubAgentExecutor(client, make_registry())
    try:
        result = executor.execute_agent(AgentTask(
            id="synth", agent_name="worker", message="initial task",
            source_agent="caller", parent_conversation_id="parent", max_iterations=1))
    finally:
        executor.shutdown()
    assert result.status == "error"
    assert result.response == ""
    assert "synthesis with unread inbox" in result.error


def test_result_queue_preserves_explicit_kind_sender_and_id(inbox):
    register_live_delegate("parent", "caller", "flash", "t", None, None)
    try:
        assert queue_live_delegate_message(
            "parent", "caller", "flash", "result body", kind="delegate_result",
            sender="responder", msg_id="result-id")
        item, = agent_inbox.unread_items("parent::task::t", "flash")
        assert item.msg_id == "result-id"
        assert item.source == "inbox:delegate_result"
        notice, _ = agent_inbox.peek_notice("parent::task::t", "flash")
        assert "delegate result from responder" in notice
        assert "result body" not in notice
    finally:
        unregister_live_delegate("parent", "caller", "flash")
