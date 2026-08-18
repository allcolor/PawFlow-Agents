"""Persistent terminal, hook synchronization, and webchat routing tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

from core import FlowFile
from core.mcp_server_store import MCPServerStore
from services import mcp_server_endpoint as endpoint
from services.mcp_terminal_router import route_published_terminal_prompt
from services.mcp_terminal_router import complete_published_terminal_turn


ROOT = Path(__file__).resolve().parents[1]


def _load_script(filename: str, module_name: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Request:
    def __init__(self, body=None, server_id="srv_test"):
        self.body = json.dumps(body or {}).encode("utf-8")
        self.path_params = {"server_id": server_id}
        self.headers = {}
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


def _payload(request):
    status, _headers, body = request.completed
    return status, json.loads(body.decode("utf-8"))


def test_terminal_registration_is_private_and_lease_scoped(monkeypatch, tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "External")
    server_id = server["server_id"]
    store.claim_client(server_id, "cli-1", "Codex", "relay-1")
    monkeypatch.setattr(endpoint.MCPServerStore, "instance", lambda: store)
    monkeypatch.setattr(
        endpoint, "_authenticate",
        lambda _request, _server_id: (store.get(_server_id), {"key_id": "k"}))

    request = _Request({
        "client_id": "cli-1", "session_id": "tmux-1", "kind": "tmux",
        "target": "tmux-1:0.0", "secret": "never-public",
        "state_path": str(tmp_path / "markers.jsonl"),
    }, server_id=server_id)
    endpoint.handle_relay_terminal(request)

    assert _payload(request) == (200, {"ok": True, "terminal_ready": True})
    public = store.get(server_id)
    assert public["terminal_ready"] is True
    assert "terminal_secret" not in public
    private = store.get_terminal_registration(server_id)
    assert private["terminal_secret"] == "never-public"

    mismatch = _Request({
        "client_id": "other", "session_id": "x", "kind": "tmux",
        "target": "x:0.0",
    }, server_id=server_id)
    endpoint.handle_relay_terminal(mismatch)
    assert _payload(mismatch)[0] == 409


def test_webchat_router_uses_existing_relay_and_distinguishes_unavailable(
        monkeypatch):
    calls = []

    class Store:
        ready = True

        def get_for_conversation(self, _conversation_id, agent_name=""):
            if agent_name and agent_name != "External":
                return None
            return {
                "server_id": "srv-1", "owner_user_id": "alice",
                "conversation_id": "conv-1", "agent_name": "External",
                "enabled": True, "terminal_ready": self.ready,
            }

        def get_terminal_registration(self, _server_id):
            return {
                "active_relay_id": "relay-1", "terminal_session_id": "tmux-1",
                "terminal_kind": "tmux", "terminal_target": "tmux-1:0.0",
                "terminal_secret": "", "terminal_state_path": "/tmp/markers",
            }

    class Relay:
        def is_connected(self):
            return True

        def _request(self, action, path, **kwargs):
            calls.append((action, path, kwargs))
            return {"ok": True, "data": {"terminal_kind": "tmux"}}

    store = Store()
    monkeypatch.setattr(
        "core.mcp_server_store.MCPServerStore.instance", lambda: store)
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: type("Registry", (), {"resolve": lambda self, *a, **k: Relay()})())

    assert route_published_terminal_prompt(
        "conv-1", "External", "hello", "m-web") is True
    assert calls[0][0] == "mcp_terminal_inject"
    assert calls[0][2]["message_id"] == "m-web"
    assert calls[0][2]["_retry_on_disconnect"] is False

    store.ready = False
    assert route_published_terminal_prompt(
        "conv-1", "External", "again", "m-2") is False
    assert route_published_terminal_prompt(
        "conv-1", "Other", "normal", "m-3") is None


def test_streaming_persists_before_external_terminal_and_starts_no_llm_worker():
    from tasks.ai.agent_loop import AgentLoopTask

    events = []
    fake_store = MagicMock()
    fake_store.get_extra_snapshot.return_value = 1
    fake_store.resolve_owner.return_value = "alice"
    task = AgentLoopTask({
        "api_key": "test", "streaming": True, "conversation_store": False})
    conversation_id = "conv-external-terminal"
    agent_key = f"{conversation_id}:External"
    with task._active_contexts_lock:
        task._active_turns.pop(agent_key, None)
        task._active_contexts.pop(agent_key, None)
        task._active_claude_client.pop(agent_key, None)

    class HookRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            return {"decision": "allow"}

    writer = MagicMock()
    writer.enqueue_message.side_effect = lambda *_a, **_k: events.append("persist")

    def route(*_args, **_kwargs):
        events.append("route")
        return True

    class NoThread:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("external terminal routing must not start an LLM worker")

    ff = FlowFile(content=json.dumps({
        "message": "hello external", "conversation_id": conversation_id,
        "target_agent": "External", "msg_id": "m-web",
    }).encode("utf-8"), attributes={"http.auth.principal": "alice"})
    with patch("core.conversation_access.authorize_message_submission"), \
            patch("core.conversation_store.ConversationStore.instance",
                  return_value=fake_store), \
            patch("core.agent_hooks.AgentHookRunner", HookRunner), \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=writer), \
            patch("services.mcp_terminal_router.route_published_terminal_prompt",
                  side_effect=route), \
            patch("tasks.ai.agent_streaming.threading.Thread", NoThread):
        result = task._execute_streaming(ff)

    body = json.loads(result[0].get_content().decode("utf-8"))
    assert events == ["persist", "route"]
    assert body["status"] == "accepted"
    assert body["external_terminal"] is True
    assert body["wait_for_done"] is True


def test_streaming_routes_already_persisted_delegate_to_external_terminal():
    from tasks.ai.agent_loop import AgentLoopTask

    fake_store = MagicMock()
    fake_store.get_extra_snapshot.return_value = 1
    fake_store.resolve_owner.return_value = "alice"
    task = AgentLoopTask({
        "api_key": "test", "streaming": True, "conversation_store": False})
    conversation_id = "conv-external-delegate"
    agent_key = f"{conversation_id}:External"
    with task._active_contexts_lock:
        task._active_turns.pop(agent_key, None)
        task._active_contexts.pop(agent_key, None)
        task._active_claude_client.pop(agent_key, None)

    class HookRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            return {"decision": "allow"}

    writer = MagicMock()
    route = MagicMock(return_value=True)
    ff = FlowFile(content=json.dumps({
        "message": "private delegate", "conversation_id": conversation_id,
        "target_agent": "External", "msg_id": "delegate-message",
    }).encode("utf-8"), attributes={
        "http.auth.principal": "alice",
        "skip_pre_persist": "1",
        "message_source": json.dumps({
            "type": "agent_delegate", "from": "Caller", "to": "External",
            "task_id": "task-1",
        }),
    })
    with patch("core.conversation_access.authorize_message_submission"), \
            patch("core.conversation_store.ConversationStore.instance",
                  return_value=fake_store), \
            patch("core.agent_hooks.AgentHookRunner", HookRunner), \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=writer), \
            patch("services.mcp_terminal_router.route_published_terminal_prompt",
                  route):
        result = task._execute_streaming(ff)

    body = json.loads(result[0].get_content().decode("utf-8"))
    assert body["external_terminal"] is True
    assert body["wait_for_done"] is True
    writer.enqueue_message.assert_not_called()
    route.assert_called_once()
    assert route.call_args.args[3] == "delegate-message"


def test_tmux_injection_marks_prompt_before_enter(monkeypatch, tmp_path):
    from pawflow_relay import mcp_terminal_inject as inject

    marker = tmp_path / "markers.jsonl"
    commands = []

    class Result:
        returncode = 0
        stderr = b""

    def run(command, **_kwargs):
        commands.append(command)
        if "send-keys" in command:
            assert marker.is_file()
            assert "m-web" in marker.read_text(encoding="utf-8")
        return Result()

    monkeypatch.setattr(inject.subprocess, "run", run)
    result = inject.inject(
        "tmux", "pawflow:0.0", "", str(marker), "m-web", "hello\nworld")

    assert result["ok"] is True
    assert [command[1] for command in commands] == [
        "has-session", "load-buffer", "paste-buffer", "send-keys"]


def test_session_launcher_creates_deterministic_tmux_with_terminal_env(
        monkeypatch, tmp_path):
    launcher = _load_script(
        "mcp-session-launcher.py", "pawflow_mcp_persistent_launcher_test")
    session = tmp_path / "session.json"
    session.write_text("{}", encoding="utf-8")
    marker = tmp_path / "markers.jsonl"
    runs = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/tmux")
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda command, **kwargs: runs.append((command, kwargs)) or Result(
            1 if "has-session" in command else 0))
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: 0)

    assert launcher._run_tmux(
        session, "codex", ["codex", "-C", "/work"], {}, "/work", marker) == 0
    name = launcher.terminal_session_name(session, "codex")
    create = runs[1][0]
    assert create[:6] == [
        "/usr/bin/tmux", "new-session", "-d", "-s", name, "-c"]
    shell_command = create[-1]
    assert "PAWFLOW_MCP_TERMINAL_KIND=tmux" in shell_command
    assert f"PAWFLOW_MCP_TERMINAL_TARGET={name}:0.0" in shell_command


class _FakeHookAPI:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        value = self.responses[name]
        return value(arguments) if callable(value) else dict(value)


def test_hook_bootstrap_and_server_injected_prompt_delta_are_deduplicated(
        monkeypatch, tmp_path):
    hook = _load_script("mcp-client-hook.py", "pawflow_mcp_hook_dedupe_test")
    state = tmp_path / "hook-state.json"
    marker = tmp_path / "markers.jsonl"
    prompt = "from webchat"
    marker.write_text(json.dumps({
        "message_id": "m-web",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "created_at": hook.time.time(),
    }) + "\n", encoding="utf-8")
    state.write_text(json.dumps({"cursor": 3, "bootstrapped": True}),
                     encoding="utf-8")
    api = _FakeHookAPI({
        "get_context_updates": {
            "messages": [
                {"seq": 4, "msg_id": "m-web", "content": prompt},
                {"seq": 5, "msg_id": "m-other", "content": "other delta"},
            ],
            "cursor": 5,
        },
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)

    output = hook.process_hook(
        "codex", "UserPromptSubmit", {"prompt": prompt, "turn_id": "t1"},
        {}, state, marker)

    context = output["hookSpecificOutput"]["additionalContext"]
    assert "other delta" in context
    assert "from webchat" not in context
    assert [name for name, _args in api.calls] == ["get_context_updates"]
    assert json.loads(state.read_text())["cursor"] == 5
    assert "consumed_at" in marker.read_text(encoding="utf-8")


def test_hook_persists_manual_prompt_and_final_response_idempotently(
        monkeypatch, tmp_path):
    hook = _load_script("mcp-client-hook.py", "pawflow_mcp_hook_messages_test")
    state = tmp_path / "hook-state.json"
    marker = tmp_path / "markers.jsonl"
    state.write_text(json.dumps({"cursor": 1, "bootstrapped": True}),
                     encoding="utf-8")
    api = _FakeHookAPI({
        "get_context_updates": {"messages": [], "cursor": 1},
        "send_user_message": {"cursor": 2},
        "send_agent_message": {"cursor": 3},
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)

    hook.process_hook(
        "cc", "UserPromptSubmit",
        {"prompt": "typed locally", "session_id": "s", "turn_id": "t"},
        {}, state, marker)
    hook.process_hook(
        "cc", "Stop",
        {"last_assistant_message": "final answer", "session_id": "s",
         "turn_id": "t"},
        {}, state, marker)

    names = [name for name, _args in api.calls]
    assert names == [
        "get_context_updates", "send_user_message", "send_agent_message"]
    user_args = api.calls[1][1]
    assistant_args = api.calls[2][1]
    assert user_args["content"] == "typed locally"
    assert assistant_args["content"] == "final answer"
    assert user_args["message_id"].startswith("pfhook_")
    assert assistant_args["message_id"].startswith("pfhook_")
    assert user_args["message_id"] != assistant_args["message_id"]


def test_hook_correlates_injected_prompt_with_final_response(
        monkeypatch, tmp_path):
    hook = _load_script(
        "mcp-client-hook.py", "pawflow_mcp_hook_correlation_test")
    state = tmp_path / "hook-state.json"
    marker = tmp_path / "markers.jsonl"
    prompt = "delegated work"
    marker.write_text(json.dumps({
        "message_id": "delegate-request-1",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "created_at": hook.time.time(),
    }) + "\n", encoding="utf-8")
    state.write_text(json.dumps({"cursor": 1, "bootstrapped": True}),
                     encoding="utf-8")
    api = _FakeHookAPI({
        "get_context_updates": {"messages": [], "cursor": 1},
        "send_agent_message": {"cursor": 2},
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)

    hook.process_hook(
        "cc", "UserPromptSubmit",
        {"prompt": prompt, "session_id": "s", "turn_id": "t"},
        {}, state, marker)
    hook.process_hook(
        "cc", "Stop",
        {"last_assistant_message": "completed", "session_id": "s",
         "turn_id": "t"},
        {}, state, marker)

    assert api.calls[-1][0] == "send_agent_message"
    assert api.calls[-1][1]["reply_to_message_id"] == "delegate-request-1"
    assert "reply_to_message_id" not in json.loads(
        state.read_text(encoding="utf-8"))


def test_external_terminal_completion_delivers_private_delegate_result(
        monkeypatch):
    class Store:
        def load(self, conversation_id, user_id=""):
            assert (conversation_id, user_id) == ("conv-1", "alice")
            return [{
                "role": "user", "msg_id": "request-1",
                "source": {
                    "type": "agent_delegate", "from": "Caller",
                    "to": "External", "task_id": "task-1",
                },
            }]

    bus = MagicMock()
    delivered = MagicMock()
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance", lambda: Store())
    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        lambda: bus)
    monkeypatch.setattr(
        "core.external_call_router.complete_task", lambda *_args: False)
    monkeypatch.setattr(
        "core.handlers.resource_agent.SpawnAgentsHandler._deliver_to_caller",
        delivered)

    assert complete_published_terminal_turn({
        "conversation_id": "conv-1", "owner_user_id": "alice",
        "agent_name": "External",
    }, "request-1", "external result") is True

    done = bus.publish_event.call_args.args
    assert done[0:2] == ("conv-1", "done")
    assert done[2]["turn_id"] == "request-1"
    assert done[2]["response"] == "external result"
    args = delivered.call_args.args
    assert args[0:3] == ("conv-1", "Caller", "alice")
    assert "external result" in args[3]
    assert args[5:7] == ("task-1", "External")


def test_external_terminal_completion_returns_to_published_mcp_caller(
        monkeypatch):
    class Store:
        def load(self, *_args, **_kwargs):
            return [{
                "role": "user", "msg_id": "request-2",
                "source": {
                    "type": "agent_delegate",
                    "from": "published_mcp:call-1", "to": "External",
                    "task_id": "task-2",
                },
            }]

    completed = MagicMock(return_value=True)
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance", lambda: Store())
    monkeypatch.setattr(
        "core.conversation_event_bus.ConversationEventBus.instance",
        lambda: MagicMock())
    monkeypatch.setattr(
        "core.external_call_router.complete_task", completed)
    deliver = MagicMock()
    monkeypatch.setattr(
        "core.handlers.resource_agent.SpawnAgentsHandler._deliver_to_caller",
        deliver)

    assert complete_published_terminal_turn({
        "conversation_id": "conv-1", "owner_user_id": "alice",
        "agent_name": "External",
    }, "request-2", "nested result") is True
    assert completed.call_args.args[0] == "task-2"
    assert completed.call_args.args[1]["response"] == "nested result"
    deliver.assert_not_called()


def test_agy_preinvocation_bootstraps_with_inject_steps(monkeypatch, tmp_path):
    hook = _load_script("mcp-client-hook.py", "pawflow_mcp_hook_agy_test")
    api = _FakeHookAPI({
        "get_initial_context": {"document": "initial document", "cursor": 8},
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)

    output = hook.process_hook(
        "agy", "PreInvocation", {}, {}, tmp_path / "state.json",
        tmp_path / "markers.jsonl")

    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreInvocation"
    assert specific["injectSteps"] == [
        {"type": "context", "content": "initial document"}]


def test_extension_hook_bootstraps_persists_and_returns_context_in_one_event(
        monkeypatch, tmp_path):
    hook = _load_script("mcp-client-hook.py", "pawflow_mcp_extension_hook_test")
    api = _FakeHookAPI({
        "get_initial_context": {"document": "initial document", "cursor": 2},
        "get_context_updates": {
            "messages": [{"seq": 3, "msg_id": "remote", "content": "delta"}],
            "cursor": 3,
        },
        "send_user_message": {"cursor": 4},
        "send_agent_message": {"cursor": 5},
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)
    state = tmp_path / "state.json"
    marker = tmp_path / "markers.jsonl"

    output = hook.process_hook(
        "pi", "before_agent_start",
        {"prompt": "local prompt", "session_id": "pi-session"},
        {}, state, marker)
    hook.process_hook(
        "pi", "agent_end",
        {"last_assistant_message": "final", "session_id": "pi-session"},
        {}, state, marker)

    assert "initial document" in output["context"]
    assert "delta" in output["context"]
    assert [name for name, _args in api.calls] == [
        "get_initial_context", "get_context_updates",
        "send_user_message", "send_agent_message"]


def test_jcode_hook_reads_prompt_from_isolated_session(monkeypatch, tmp_path):
    hook = _load_script("mcp-client-hook.py", "pawflow_mcp_jcode_hook_test")
    home = tmp_path / "jcode-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session-1.json").write_text(json.dumps({
        "messages": [
            {"id": "u-1", "role": "user",
             "content": [{"type": "text", "text": "typed"}]},
            {"id": "a-1", "role": "assistant",
             "content": [{"type": "text", "text": "answer"}]},
        ]
    }), encoding="utf-8")
    api = _FakeHookAPI({
        "get_context_updates": {"messages": [], "cursor": 1},
        "send_user_message": {"cursor": 2},
        "send_agent_message": {"cursor": 3},
    })
    monkeypatch.setattr(hook, "PublishedMCPClient", lambda _profile: api)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cursor": 1, "bootstrapped": True}),
                     encoding="utf-8")
    raw = {"session_id": "session-1", "jcode_home": str(home)}

    hook.process_hook("jcode", "turn_start", raw, {}, state, tmp_path / "m")
    hook.process_hook("jcode", "turn_end", raw, {}, state, tmp_path / "m")

    assert api.calls[1][1]["content"] == "typed"
    assert api.calls[2][1]["content"] == "answer"
    assert api.calls[1][1]["message_id"] != api.calls[2][1]["message_id"]
