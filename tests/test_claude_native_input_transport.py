"""Claude native input transport wiring without authenticated model calls."""

import asyncio
import io
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.llm_providers._cc_native_input import handle_control
from core.llm_providers._cc_native_input import stdin_lock, write_message
from core.llm_providers._cc_stream_loop import _CCStreamLoopMixin
from core.llm_providers._cc_stream_turn import _CCStreamTurnMixin
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
from core.native_user_input import NativeInputRequests
from services.cc_interactive_event_service import CCInteractiveEventService
from tools import cc_interactive_hook as hook


def question_input():
    return {"questions": [
        {"question": "Which exact labels?", "header": "Labels", "multiSelect": True,
         "options": [{"label": "One, exact", "description": "First"},
                     {"label": "Deux ✓", "description": "Second"}]},
        {"question": "Your alternative?", "header": "Other", "multiSelect": False,
         "options": [{"label": "A", "description": "First"},
                     {"label": "B", "description": "Second"}]},
    ], "metadata": {"preserved": True}}


def control(request_id="request-1", tool="AskUserQuestion", inputs=None):
    return {"type": "control_request", "request_id": request_id, "request": {
        "subtype": "can_use_tool", "tool_name": tool, "tool_use_id": "tool-1",
        "input": question_input() if inputs is None else inputs}}


def hook_input():
    return {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
            "tool_use_id": "tool-1", "tool_input": question_input()}


class InteractionStore:
    def __init__(self):
        self.created = threading.Event()
        self.cancelled = threading.Event()
        self.current = {"status": "pending"}
        self.request = None

    def create_interaction(self, **kwargs):
        self.request = kwargs
        self.created.set()
        return {"request_id": "pawflow-interaction"}

    def get_confirmation(self, request_id):
        assert request_id == "pawflow-interaction"
        return self.current

    def cancel(self, request_id, **kwargs):
        assert request_id == "pawflow-interaction"
        self.cancelled.set()


@pytest.fixture
def interaction(monkeypatch):
    store = InteractionStore()
    monkeypatch.setattr("core.confirmation_store.UserInteractionStore.instance", lambda: store)
    return store


@pytest.fixture
def native_state(monkeypatch):
    threads = []
    original = threading.Thread

    def thread(*args, **kwargs):
        worker = original(*args, **kwargs)
        if kwargs.get("name") == "native-user-input":
            threads.append(worker)
        return worker

    monkeypatch.setattr(threading, "Thread", thread)
    st = SimpleNamespace(
        user_id="user", conv_id="conversation", agent_name="agent",
        proc=Mock(stdin=io.StringIO()), _native_inputs=NativeInputRequests())
    st.proc.poll.return_value = None

    def finish():
        for worker in threads:
            worker.join(1)
            assert not worker.is_alive()

    st.finish = finish
    yield st
    st._native_inputs.close()
    finish()


def read_response(st):
    st.finish()
    return json.loads(st.proc.stdin.getvalue())["response"]


def test_control_roundtrip_preserves_questions_labels_multiselect_and_free_text(native_state, interaction):
    interaction.current = {"status": "answered", "answer": {
        "question_0": ["One, exact", "Deux ✓"], "question_1": "Custom answer\nsecond line"}}
    original = question_input()
    handle_control(native_state, control(inputs=original))
    response = read_response(native_state)
    assert response == {"subtype": "success", "request_id": "request-1", "response": {
        "behavior": "allow", "updatedInput": {**original, "answers": {
            "Which exact labels?": "One, exact, Deux ✓",
            "Your alternative?": "Custom answer\nsecond line"}}}}
    assert "answers" not in original
    assert interaction.request["requester_kind"] == "provider"
    fields = interaction.request["response_schema"]["fields"]
    assert fields[0]["type"] == "multi"
    assert [option["label"] for option in fields[0]["options"]] == ["One, exact", "Deux ✓"]
    assert all(field["no_default"] and field["allow_other"] for field in fields)


@pytest.mark.parametrize("answer,behavior", [("allow", "allow"), ("deny", "deny")])
def test_control_permission_response_includes_original_input_when_allowed(
        native_state, interaction, answer, behavior):
    interaction.current = {"status": "answered", "answer": {"question_0": answer}}
    inputs = {"file_path": "/workspace/precise file"}
    handle_control(native_state, control(tool="Read", inputs=inputs))
    response = read_response(native_state)["response"]
    assert response["behavior"] == behavior
    if behavior == "allow":
        assert response["updatedInput"] == inputs
    else:
        assert response["message"]


@pytest.mark.parametrize("payload", [
    None, [], "invalid", {}, {"subtype": "hook_callback"},
    {"subtype": "can_use_tool", "tool_name": None, "input": {}, "tool_use_id": "tool"},
    {"subtype": "can_use_tool", "tool_name": "Read", "input": [], "tool_use_id": "tool"},
    {"subtype": "can_use_tool", "tool_name": "Read", "input": {}},
    {"subtype": "can_use_tool", "tool_name": "Read", "input": {"text": "x" * 64001},
     "tool_use_id": "tool"},
])
def test_malformed_control_request_returns_error_without_form(native_state, interaction, payload):
    handle_control(native_state, {"type": "control_request", "request_id": "bad", "request": payload})
    response = read_response(native_state)
    assert response["subtype"] == "error"
    assert response["request_id"] == "bad"
    assert not interaction.created.is_set()


def test_control_worker_exception_returns_error(native_state, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("private implementation detail")
    monkeypatch.setattr("core.llm_providers._cc_native_input.answer_claude_hook", broken)
    handle_control(native_state, control())
    response = read_response(native_state)
    assert response["subtype"] == "error"
    assert "private implementation detail" not in response["error"]


@pytest.mark.parametrize("tool", ["AskUserQuestion", "Read"])
def test_user_cancel_returns_denial(native_state, interaction, tool):
    interaction.current = {"status": "cancelled"}
    handle_control(native_state, control(tool=tool))
    assert read_response(native_state)["response"]["behavior"] == "deny"


@pytest.mark.parametrize("stop", ["control_cancel", "close", "process_exit"])
def test_transport_stop_suppresses_stale_reply(native_state, interaction, stop):
    handle_control(native_state, control())
    assert interaction.created.wait(1)
    if stop == "control_cancel":
        handle_control(native_state, {"type": "control_cancel_request", "request_id": "request-1"})
    elif stop == "close":
        native_state._native_inputs.close()
    else:
        native_state.proc.poll.return_value = 1
    interaction.current = {"status": "answered", "answer": {
        "question_0": ["One, exact"], "question_1": "A"}}
    native_state.finish()
    assert native_state.proc.stdin.getvalue() == ""
    assert interaction.cancelled.is_set()


def test_reader_and_dispatch_continue_while_user_question_waits(native_state, interaction):
    seen = []
    st = native_state
    st._reader_stop = threading.Event()
    st._event_q = queue.Queue()
    st._hb_state = {"stream_line_count": 0}
    st._live_key = None
    st._partial_current_msg_id = ""
    st.thinking_callback = None

    def display(text):
        assert interaction.created.wait(1)
        seen.append(text)

    st.callback = display
    events = [control(), {"type": "stream_event", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "still reading"}}},
        {"type": "control_cancel_request", "request_id": "request-1"}]
    st.proc.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in events))
    driver = _CCStreamLoopMixin()
    driver._ccs_reader_daemon(st)
    assert st._hb_state["stream_line_count"] == 3
    driver._ccs_dispatch_loop(st)
    st.finish()
    assert seen == ["still reading"]
    assert st.proc.stdin.getvalue() == ""
    assert interaction.cancelled.is_set()


def test_stream_flags_keep_sdk_stdio_and_permission_bypass():
    session = ClaudeCodeSessionMixin()
    session._cfg = lambda key, default: default
    args = session._build_claude_cmd("test")
    assert args[args.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--dangerously-skip-permissions" in args
    assert "AskUserQuestion" not in args[args.index("--disallowedTools") + 1].split(",")


def test_hook_settings_enable_questions_and_remove_old_deny_rule(tmp_path):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"permissions": {"deny": ["AskUserQuestion", "custom-rule"]}}))
    InteractiveClaudeCodePool()._write_hook_settings(str(tmp_path))
    settings = json.loads(path.read_text())
    assert "AskUserQuestion" not in settings["permissions"]["deny"]
    assert "custom-rule" in settings["permissions"]["deny"]
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "AskUserQuestion"
    for event in ("PreToolUse", "PermissionRequest"):
        handler = settings["hooks"][event][0]["hooks"][0]
        assert handler["timeout"] == 3600
        assert handler["args"][-2:] == ["--event", event]


@pytest.mark.parametrize("event", ["PreToolUse", "PermissionRequest"])
@pytest.mark.parametrize("raw", ["invalid JSON", "[]", "{}"])
def test_hook_malformed_stdin_returns_blocking_decision(monkeypatch, capsys, event, raw):
    monkeypatch.setenv("PAWFLOW_CCI_HOOK_CLIENT", "cc")
    monkeypatch.setattr(hook.sys, "argv", ["hook", "--event", event])
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(raw))
    assert hook.main() == 0
    specific = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert specific["hookEventName"] == event
    assert specific.get("permissionDecision", specific.get("decision", {}).get("behavior")) == "deny"


@pytest.mark.parametrize("result", [
    [], {}, {"type": "error"}, {"type": "native_input_result", "output": {}},
    {"type": "native_input_result", "output": {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}},
])
def test_hook_malformed_response_denies_and_closes_socket(monkeypatch, result):
    sock = Mock()
    monkeypatch.setattr(hook, "_connect", lambda *args: sock)
    monkeypatch.setattr(hook, "_recv_json", lambda *args: result)
    output = hook._interaction_output(hook_input(), "ws://test", "token", "session")
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    sock.close.assert_called_once()


def test_hook_returns_exact_valid_answer(monkeypatch):
    sock = Mock()
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "allow", "updatedInput": {
                  **question_input(), "answers": {"Which exact labels?": "Deux ✓",
                                                  "Your alternative?": "custom\ntext"}}}}
    monkeypatch.setattr(hook, "_connect", lambda *args: sock)
    monkeypatch.setattr(hook, "_recv_json", lambda *args: {
        "type": "native_input_result", "output": output})
    assert hook._interaction_output(hook_input(), "ws://test", "token", "session") == output
    sock.sendall.assert_called_once()
    sock.close.assert_called_once()


class SocketHarness:
    def __init__(self, monkeypatch):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False
        self.writer = SimpleNamespace(close=self.close)
        monkeypatch.setattr("services.filesystem_service._ws_recv_frame", self.recv)
        monkeypatch.setattr("services.filesystem_service._ws_send_frame", self.send)

    async def recv(self, reader):
        return await reader.get()

    async def send(self, writer, payload, opcode=1):
        self.outgoing.put_nowait(json.loads(payload))

    def feed(self, data):
        self.incoming.put_nowait((1, json.dumps(data).encode()))

    def close(self):
        self.closed = True
        self.incoming.put_nowait((8, b""))

    async def next(self):
        return await asyncio.wait_for(self.outgoing.get(), 1)


@pytest.mark.parametrize("provider", ["claude-code-interactive", "cc_mcp"])
@pytest.mark.parametrize("stop", ["answer", "hook_disconnect", "unregister", "service_disconnect",
                                 "proxy_disconnect"])
def test_hook_socket_reader_remains_active_and_cancels_waits(
        monkeypatch, interaction, provider, stop):
    async def run():
        socket = SocketHarness(monkeypatch)
        svc = CCInteractiveEventService({"token": "token", "_service_id": "events"})
        state = svc.register_session("session", user_id="user", conversation_id="conversation",
                                     agent_name="agent", provider=provider)
        socket.feed({"type": "register", "token": "token",
                     "session_token": "session", "client_kind": "hook"})
        task = asyncio.create_task(svc._serve(socket.incoming, socket.writer, "test"))
        try:
            assert await socket.next() == {"type": "registered"}
            socket.feed({"type": "native_input", "input": hook_input()})
            assert await asyncio.to_thread(interaction.created.wait, 1)
            socket.feed({"type": "ping"})
            assert await socket.next() == {"type": "pong"}
            if stop == "answer":
                interaction.current = {"status": "answered", "answer": {
                    "question_0": ["Deux ✓"], "question_1": "free\ntext"}}
                result = await socket.next()
                assert result["type"] == "native_input_result"
                updated = result["output"]["hookSpecificOutput"]["updatedInput"]
                assert updated["answers"]["Your alternative?"] == "free\ntext"
            elif stop == "hook_disconnect":
                socket.close()
            elif stop == "unregister":
                svc.unregister_session("session")
            elif stop == "proxy_disconnect":
                proxy_reader = asyncio.Queue()
                proxy_reader.put_nowait((1, json.dumps({
                    "type": "register", "token": "token",
                    "session_token": "session", "client_kind": "proxy"}).encode()))
                proxy_reader.put_nowait((8, b""))
                await svc._serve(proxy_reader, SimpleNamespace(close=lambda: None), "test")
                assert await socket.next() == {"type": "registered"}
            else:
                svc.disconnect()
            await asyncio.wait_for(task, 1)
            assert await asyncio.to_thread(interaction.cancelled.wait, 1)
            assert socket.outgoing.empty()
            assert socket.closed
            assert all(cancel.is_set() for cancel in state.native_input_cancels)
        finally:
            socket.close()
            await asyncio.wait_for(task, 1)
    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, [], "bad", {"hook_event_name": "unknown"}])
def test_invalid_hook_payload_returns_error_and_closes_socket(monkeypatch, interaction, invalid):
    async def run():
        socket = SocketHarness(monkeypatch)
        svc = CCInteractiveEventService({"token": "token", "_service_id": "events"})
        svc.register_session("session", user_id="user", conversation_id="conversation",
                             agent_name="agent", provider="cc_mcp")
        socket.feed({"type": "register", "token": "token",
                     "session_token": "session", "client_kind": "hook"})
        socket.feed({"type": "native_input", "input": invalid})
        task = asyncio.create_task(svc._serve(socket.incoming, socket.writer, "test"))
        assert (await socket.next())["type"] == "registered"
        assert (await socket.next())["type"] == "error"
        await asyncio.wait_for(task, 1)
        assert not interaction.created.is_set()
        assert socket.closed
    asyncio.run(run())


def test_hook_concurrency_limit_rejects_without_publishing(monkeypatch, interaction):
    async def run():
        socket = SocketHarness(monkeypatch)
        svc = CCInteractiveEventService({"token": "token", "_service_id": "events"})
        state = svc.register_session("session", user_id="user", conversation_id="conversation",
                                     agent_name="agent", provider="cc_mcp")
        state.native_input_cancels.update(threading.Event() for _ in range(16))
        socket.feed({"type": "register", "token": "token",
                     "session_token": "session", "client_kind": "hook"})
        socket.feed({"type": "native_input", "input": hook_input()})
        task = asyncio.create_task(svc._serve(socket.incoming, socket.writer, "test"))
        assert (await socket.next())["type"] == "registered"
        assert (await socket.next())["type"] == "error"
        await asyncio.wait_for(task, 1)
        assert not interaction.created.is_set()
    asyncio.run(run())


class ContendedLock:
    def __init__(self):
        self.lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, **kwargs):
        if self.lock.locked():
            self.waiting.set()
        return self.lock.acquire(**kwargs)

    def release(self):
        self.lock.release()


def test_control_and_catchup_share_process_write_lock(native_state, interaction):
    interaction.current = {"status": "answered", "answer": {
        "question_0": ["Deux ✓"], "question_1": "A"}}
    first_write = threading.Event()
    release = threading.Event()

    class SlowInput(io.StringIO):
        def write(self, message):
            if not first_write.is_set():
                first_write.set()
                assert release.wait(1)
            return super().write(message)

    st = native_state
    st.proc.stdin = SlowInput()
    lock = ContendedLock()
    st.proc._pawflow_cc_stdin_lock = lock
    handle_control(st, control())
    assert first_write.wait(1)
    driver = _CCStreamTurnMixin()
    driver._claude_proc = st.proc
    driver._build_catchup_context = lambda *args: "Concurrent catchup"
    catchup = threading.Thread(target=driver._ccs_inject_catchup, args=(st,))
    catchup.start()
    try:
        assert lock.waiting.wait(1)
    finally:
        release.set()
        catchup.join(1)
    assert not catchup.is_alive()
    st.finish()
    messages = [json.loads(line) for line in st.proc.stdin.getvalue().splitlines()]
    assert [message["type"] for message in messages] == ["control_response", "user"]
    assert messages[1]["message"]["content"] == "Concurrent catchup"


def test_cancelled_reply_does_not_wait_for_or_write_to_busy_stdin(native_state):
    st = native_state
    lock = ContendedLock()
    st.proc._pawflow_cc_stdin_lock = lock
    assert stdin_lock(st.proc) is lock
    lock.acquire()
    cancel = threading.Event()
    writer = threading.Thread(target=write_message, args=(st.proc, "stale reply", cancel))
    writer.start()
    try:
        assert lock.waiting.wait(1)
        cancel.set()
        writer.join(1)
        assert not writer.is_alive()
        assert st.proc.stdin.getvalue() == ""
    finally:
        lock.release()
        writer.join(1)


@pytest.mark.parametrize("choice", ["allow", "deny", None])
def test_permission_hook_returns_actual_decision(monkeypatch, interaction, choice):
    from services._cci_native_input import answer_native_input

    async def run():
        socket = SocketHarness(monkeypatch)
        state = SimpleNamespace(provider="cc_mcp", user_id="user",
                                conversation_id="conversation", agent_name="agent")
        interaction.current = ({"status": "cancelled"} if choice is None else {
            "status": "answered", "answer": {"question_0": choice}})
        await answer_native_input(state, {
            "hook_event_name": "PermissionRequest", "tool_name": "Read",
            "tool_input": {"file_path": "/workspace/precise file"}},
            threading.Event(), socket.writer)
        result = await socket.next()
        specific = result["output"]["hookSpecificOutput"]
        assert specific["hookEventName"] == "PermissionRequest"
        assert specific["decision"]["behavior"] == ("allow" if choice == "allow" else "deny")
        assert socket.closed
    asyncio.run(run())


@pytest.mark.parametrize("provider,user_id", [("codex-interactive", "user"), ("cc_mcp", "")])
def test_hook_requires_bound_claude_session(monkeypatch, interaction, provider, user_id):
    from services._cci_native_input import answer_native_input

    async def run():
        socket = SocketHarness(monkeypatch)
        state = SimpleNamespace(provider=provider, user_id=user_id,
                                conversation_id="conversation", agent_name="agent")
        await answer_native_input(state, hook_input(), threading.Event(), socket.writer)
        assert (await socket.next())["type"] == "error"
        assert not interaction.created.is_set()
        assert socket.closed
    asyncio.run(run())
