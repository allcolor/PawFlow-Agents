import json
import threading
from concurrent.futures import ThreadPoolExecutor

from core.handlers.file_ops import ScheduleContinuationHandler
from core.handlers.todolist import TodoListHandler
from core.llm_client import LLMToolCall
from core.tool_registry import ToolRegistry
from tasks.ai.agent_tool_config import AgentToolConfigMixin
from tasks.ai.agent_tool_exec import AgentToolExecMixin


class _Agent(AgentToolExecMixin, AgentToolConfigMixin):
    config = {}

    def _find_filesystem_service(self, user_id):
        return None


def _allow_tools(monkeypatch):
    from core.agent_hooks import AgentHookRunner
    from core.conversation_store import ConversationStore
    from core.tool_approval import ToolApprovalGate

    monkeypatch.setattr(
        ConversationStore,
        "get_extra",
        lambda self, conversation_id, key: (
            "auto" if key == "permission_mode" else None),
    )
    monkeypatch.setattr(
        ToolApprovalGate, "check", lambda *args, **kwargs: "approved")
    monkeypatch.setattr(
        AgentHookRunner, "run",
        lambda self, event, payload, **kwargs: {"decision": "allow"},
    )


def _execute(agent, registry, conversation_id, tool_name, arguments):
    call = LLMToolCall(
        id=f"use-tool-{tool_name}-{conversation_id}",
        name="use_tool",
        arguments={
            "tool_name": tool_name,
            "arguments_json": json.dumps(arguments),
        },
    )
    return agent._execute_tool_calls(
        [call], registry, {}, 100,
        parallel=False,
        user_id="alice",
        conversation_id=conversation_id,
        agent_name="assistant",
    )[0][1]


def test_concurrent_todos_keep_their_conversation_scope(monkeypatch):
    _allow_tools(monkeypatch)
    barrier = threading.Barrier(2)

    class _Store:
        def __init__(self):
            self.seen = []
            self.lock = threading.Lock()

        def create(self, user_id, conversation_id, agent_name, **changes):
            with self.lock:
                self.seen.append((user_id, conversation_id, agent_name))
            barrier.wait(timeout=5)
            return {"id": f"todo-{conversation_id}", **changes}

    store = _Store()
    from core.todo_store import TodoStore
    monkeypatch.setattr(TodoStore, "instance", staticmethod(lambda: store))

    registry = ToolRegistry()
    registry.register(TodoListHandler())
    from core.handlers.meta_tools import UseToolHandler
    registry.register(UseToolHandler(registry))
    agent = _Agent()
    # Simulate the stale shared scope left by another context preparation.
    agent._configure_tool_handlers(
        registry, user_id="alice", conversation_id="wrong",
        agent_name="assistant")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _execute, agent, registry, conversation_id, "todolist",
                {"action": "create", "subject": conversation_id},
            )
            for conversation_id in ("conv-a", "conv-b")
        ]
        results = [json.loads(f.result(timeout=10)) for f in futures]

    assert {row[1] for row in store.seen} == {"conv-a", "conv-b"}
    assert {result["id"] for result in results} == {
        "todo-conv-a", "todo-conv-b"}


def test_concurrent_continuations_keep_their_conversation_scope(monkeypatch):
    _allow_tools(monkeypatch)
    barrier = threading.Barrier(2)

    class _Scheduler:
        def __init__(self):
            self.seen = []
            self.lock = threading.Lock()

        def schedule_delay(self, conversation_id, delay, **kwargs):
            with self.lock:
                self.seen.append((conversation_id, kwargs["user_id"]))
            barrier.wait(timeout=5)
            return 1_786_000_000.0

    scheduler = _Scheduler()
    from core.poll_scheduler import PollScheduler
    monkeypatch.setattr(
        PollScheduler, "instance", staticmethod(lambda: scheduler))

    registry = ToolRegistry()
    registry.register(ScheduleContinuationHandler())
    from core.handlers.meta_tools import UseToolHandler
    registry.register(UseToolHandler(registry))
    agent = _Agent()
    agent._configure_tool_handlers(
        registry, user_id="alice", conversation_id="wrong",
        agent_name="assistant")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _execute, agent, registry, conversation_id,
                "schedule_continuation",
                {"plan": f"resume {conversation_id}", "delay_seconds": 60},
            )
            for conversation_id in ("conv-a", "conv-b")
        ]
        results = [f.result(timeout=10) for f in futures]

    assert {row[0] for row in scheduler.seen} == {"conv-a", "conv-b"}
    assert all("Continuation scheduled" in result for result in results)


def test_registry_fork_rebinds_registry_aware_handlers():
    from core.handlers.meta_tools import UseToolHandler

    registry = ToolRegistry()
    registry.register(TodoListHandler())
    registry.register(UseToolHandler(registry))

    forked = registry.fork()

    assert forked is not registry
    assert forked.get("todolist") is not registry.get("todolist")
    assert forked.get("use_tool")._registry is forked
