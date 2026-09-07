"""Native compaction during a captured turn must hand back to PawFlow."""

import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from core.llm_client import LLMMessage
from services.cc_interactive_event_service import CCInteractiveEventService
from tasks.ai.agent_loop import AgentLoopTask


@pytest.fixture
def capture_compact(monkeypatch, tmp_path):
    from core.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(ConversationStore, "instance", lambda: store)
    cid = "capture-compact"
    messages = [
        {"role": role, "content": text, "msg_id": uuid.uuid4().hex,
         "ts": time.time(), "source": {"type": role, "name": "assistant",
                                      "target_agent": "assistant"}}
        for role, text in [("user", "initial request"), ("assistant", "working"),
                           ("user", "continue"), ("assistant", "still working")]
    ]
    messages.append({
        "role": "user", "content": "latest screenshot", "msg_id": "latest",
        "ts": time.time(), "source": {"type": "user", "target_agent": "assistant"},
        "attachments": [{"file_id": "shot", "filename": "image.png",
                         "mime_type": "image/png"}],
    })
    store.save(cid, messages, user_id="user")
    store.set_extra(cid, "conv_agents", {"assistant": {
        "definition": "assistant", "params": {"name": "assistant"},
        "llm_service": ""}}, user_id="user")
    task = AgentLoopTask({"system_prompt": "test", "max_context_size": 64000})
    task._active_contexts_lock = threading.RLock()
    task._active_turns = {}
    task._active_contexts = {}
    monkeypatch.setattr(AgentLoopTask, "_live_instance", task)
    svc = CCInteractiveEventService({"token": "token", "_service_id": "events"})
    state = svc.register_session(
        "old-session", user_id="user", conversation_id=cid,
        agent_name="assistant", provider="codex-interactive")
    state.manual_capture_active = True
    trace = []
    controls = {"fail": False, "force_stop": False, "flush": True}
    pending = []
    controls["store"] = store

    def save_messages(messages):
        agents = store.get_extra(cid, "conv_agents")
        store.save(cid, messages, user_id="user")
        store.set_extra(cid, "conv_agents", agents, user_id="user")

    controls["save_messages"] = save_messages

    def flush(**_kwargs):
        trace.append("flush")
        if controls["flush"] and pending:
            save_messages(store.load(cid, user_id="user") + pending)
            pending.clear()
        return controls["flush"]

    writer = SimpleNamespace(enqueue_sse_events=lambda *_args: None, flush=flush,
                             enqueue_message=lambda msg, **_kwargs: pending.append(msg))
    monkeypatch.setattr("core.conversation_writer.ConversationWriter.for_conversation",
                        lambda _cid: writer)
    bus = SimpleNamespace(publish_event=lambda _cid, event, data: trace.append(
        (event, data)))
    monkeypatch.setattr("core.conversation_event_bus.ConversationEventBus.instance",
                        lambda: bus)
    monkeypatch.setattr(task, "cancel_agent", lambda *_args, **_kwargs: trace.append("cancel"))
    monkeypatch.setattr(task, "_get_summarizer_client", lambda *_a, **_k: (None, 0, ""))
    monkeypatch.setattr(task, "_acquire_context_op", lambda *_a, **_k: (
        trace.append("lock") or True))
    monkeypatch.setattr(task, "_release_context_op", lambda *_a, **_k: trace.append("unlock"))
    monkeypatch.setattr(task, "_clear_claude_session", lambda *_a: trace.append("invalidate"))

    def release(token, *, reason):
        assert (token, reason) == (state.session_token, "compact_started")
        assert "lock" in trace
        assert state.closed
        assert svc.claim_consumer(token) == 0, "a retired stream cannot be reclaimed"
        trace.append("kill")
        svc.unregister_session(state.session_token)
        return 1

    monkeypatch.setattr(svc, "_pool_for", lambda _state: SimpleNamespace(
        kill_and_evict_by_session_token=release))

    def compact(source, **kwargs):
        assert kwargs["force"] is True
        assert (kwargs["conversation_id"], kwargs["agent_name"], kwargs["user_id"]) == (
            cid, "assistant", "user")
        assert state.closed, "the native session must stop before PawFlow compact"
        assert trace.index("flush") < trace.index("kill")
        assert trace.count("flush") == 2
        trace.append("compact")
        controls["compacted_messages"] = source.load(cid, user_id="user")
        latest = next(msg for msg in controls["compacted_messages"] if msg["msg_id"] == "latest")
        assert latest["msg_id"] == "latest"
        assert latest["attachments"][0]["file_id"] == "shot"
        if controls["fail"]:
            raise RuntimeError("compact failed")
        if controls["force_stop"]:
            source.set_extra(cid, "last_force_stop_at:assistant", time.time())
        return [LLMMessage(role="user", content=latest["content"],
                           conversation_id=cid)]

    monkeypatch.setattr(task, "_compact_context_from_store", compact)
    monkeypatch.setattr("tasks.ai.context_usage.reset_cli_context_usage", lambda *_a, **_k: {
        "used": 10, "max": 64000, "pct": 0.01})
    monkeypatch.setattr("tasks.ai.context_usage.persist_context_usage", lambda *_a, **_k: None)

    def wake(conv, agent, **kwargs):
        assert (conv, agent) == (cid, "assistant")
        assert kwargs["reason"].startswith("[compact_resume:assistant]")
        assert kwargs["user_id"] == "user"
        assert trace.index("compact") < trace.index("invalidate") < trace.index("unlock")
        trace.append("wake")

    monkeypatch.setattr(AgentLoopTask, "wake_agent", wake)
    return svc, state, task, trace, controls


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
def test_captured_native_compact_stops_flushes_compacts_and_resumes(capture_compact, hook):
    svc, state, task, trace, _controls = capture_compact
    state.events.put({"type": "hook", "hook_event_name": hook})
    svc._run_manual_capture(state.session_token)
    assert "wake" in trace
    assert "cancel" not in trace, "capture teardown must not cancel a replacement worker"
    assert state.closed
    assert not state.manual_capture_active
    assert not state.manual_capture_pending
    assert task._active_turns == {}


@pytest.mark.parametrize("failure", ["fail", "force_stop", "flush"])
def test_failed_or_force_stopped_capture_compact_never_resumes(capture_compact, failure):
    svc, state, task, trace, controls = capture_compact
    controls[failure] = failure != "flush"
    state.events.put({"type": "hook", "hook_event_name": "PreCompact"})
    svc._run_manual_capture(state.session_token)
    assert ("kill" in trace) is (failure != "flush")
    assert "wake" not in trace
    assert "unlock" in trace
    assert state.closed is (failure != "flush")
    assert task._active_turns == {}


@pytest.mark.parametrize("takeover", ["consumer", "marker"])
def test_capture_rechecks_ownership_after_waiting_for_context_lock(
        capture_compact, monkeypatch, takeover):
    svc, state, task, trace, _controls = capture_compact

    def acquire(*_args, **_kwargs):
        if takeover == "consumer":
            svc.claim_consumer(state.session_token)
        else:
            task._active_turns["capture-compact:assistant"] = {"owner_id": "replacement"}
        return True

    monkeypatch.setattr(task, "_acquire_context_op", acquire)
    state.events.put({"type": "hook", "hook_event_name": "PreCompact"})
    svc._run_manual_capture(state.session_token)
    assert not state.closed
    assert not any(event in trace for event in ("kill", "cancel", "compact", "wake"))
    assert "unlock" in trace


def test_short_capture_preserves_native_session_when_compaction_is_rejected(capture_compact):
    svc, state, _task, trace, controls = capture_compact
    store = controls["store"]
    controls["save_messages"](store.load("capture-compact", user_id="user")[:3])
    state.events.put({"type": "hook", "hook_event_name": "PreCompact"})
    svc._run_manual_capture(state.session_token)
    assert not state.closed
    assert "kill" not in trace and "flush" in trace and "unlock" in trace
    assert "compact" not in trace and "wake" not in trace
    assert any(isinstance(item, tuple) and item[1].get("error") ==
               "Not enough messages to compact" for item in trace)


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("provider", ["codex-interactive", "claude-code-interactive"])
def test_capture_persists_unfinished_blocks_before_counting_and_compacting(
        capture_compact, hook, provider):
    svc, state, _task, trace, controls = capture_compact
    state.provider = provider
    store = controls["store"]
    original = store.load("capture-compact", user_id="user")
    controls["save_messages"](original[:2] + [original[-1]])
    for kind, text in [("reasoning_summary_text", "pending thought"),
                       ("output_text", "accepted text before compact")]:
        if provider == "codex-interactive":
            payload = {"type": "response." + kind + ".delta", "delta": text}
        else:
            payload = {"type": "content_block_delta", "delta": {
                "type": "text_delta" if kind == "output_text" else "thinking_delta",
                "text": text}}
        state.events.put({"type": "sse", "payload": payload})
    state.events.put({"type": "hook", "hook_event_name": hook})
    svc._run_manual_capture(state.session_token)
    assert "wake" in trace
    messages = controls["compacted_messages"]
    assert sum(msg.get("content") == "accepted text before compact" for msg in messages) == 1
    assert sum(msg.get("role") == "thinking" and msg.get("content") == "pending thought"
               for msg in messages) == 1
    assert len(messages) >= 4


def test_exact_session_eviction_preserves_replacement_registered_during_cleanup(monkeypatch):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool

    pool = object.__new__(InteractiveClaudeCodePool)
    pool._lock = threading.RLock()
    key = ("user", "conversation", "assistant", "service")
    old = SimpleNamespace(key=key, name="old-container", session_token="old-token")
    replacement = SimpleNamespace(key=key, name="new-container", session_token="new-token")
    pool._sessions = {key: old}
    killed, unregistered = [], []
    monkeypatch.setattr(pool, "_recover_container_tokens",
                        lambda state: pool._sessions.update({key: replacement}))
    monkeypatch.setattr(pool, "_unregister_event_session",
                        lambda state: unregistered.append(state.session_token))
    monkeypatch.setattr(pool, "_kill_container", killed.append)
    assert pool.kill_and_evict_by_session_token("old-token", "compact_started") == 1
    assert pool._sessions[key] is replacement
    assert killed == ["old-container"]
    assert unregistered == ["old-token"]
    assert pool.kill_and_evict_by_session_token("old-token", "compact_started") == 0
    assert pool._sessions[key] is replacement


@pytest.mark.parametrize("superseded", ["consumer", "marker", "closed"])
def test_stale_capture_cannot_compact_a_replacement_turn(capture_compact, superseded):
    svc, state, task, trace, _controls = capture_compact
    epoch = svc.claim_consumer(state.session_token, kind="capture")
    svc._active_turn_marker(state, register=True)
    if superseded == "consumer":
        svc.claim_consumer(state.session_token)
    elif superseded == "marker":
        task._active_turns["capture-compact:assistant"] = {"owner_id": "new-worker"}
    else:
        svc.unregister_session(state.session_token)
    svc._compact_captured_turn(state, epoch)
    assert "kill" not in trace
    assert "compact" not in trace
    assert "wake" not in trace


@pytest.mark.parametrize("outcome", ["completed", "error", "timeout", "skipped"])
def test_synchronous_context_operation_returns_actual_outcome(
        capture_compact, monkeypatch, outcome):
    import json
    from core import FlowFile

    _svc, _state, task, trace, _controls = capture_compact
    if outcome == "timeout":
        monkeypatch.setattr(task, "_acquire_context_op", lambda *_a, **_k: False)

    def operation():
        trace.append("operation")
        if outcome == "error":
            raise RuntimeError("operation failed")
        return {"context_changed": False}

    flowfile = FlowFile()
    task._run_bg_context_op(
        "capture-compact", "rebuild", operation, flowfile, agent_name="assistant",
        background=False, capture_handoff=lambda: outcome != "skipped")
    result = json.loads(flowfile.get_content())
    assert result["status"] == ("error" if outcome == "timeout" else outcome)
    assert result["action"] == "rebuild"
    assert ("operation" in trace) is (outcome in ("completed", "error"))
    if outcome in ("error", "timeout"):
        assert result["error"]
        assert flowfile.get_attribute("http.response.status") == "500"
    if outcome == "skipped":
        assert result["reason"] == "Captured session ownership changed"
    assert ("unlock" in trace) is (outcome != "timeout")


def test_async_context_operation_still_returns_accepted(capture_compact, monkeypatch):
    import json
    from core import FlowFile
    from tasks.ai import agent_actions

    _svc, _state, task, _trace, _controls = capture_compact
    monkeypatch.setattr(agent_actions.threading, "Thread", lambda target, **_kwargs:
                        SimpleNamespace(start=target))
    flowfile = FlowFile()
    task._run_bg_context_op(
        "capture-compact", "rebuild", lambda: {"context_changed": False},
        flowfile, agent_name="assistant", capture_handoff=lambda: True)
    assert json.loads(flowfile.get_content()) == {"status": "accepted", "action": "rebuild"}


@pytest.mark.parametrize("failure", ["fail", "flush", "short", "lock"])
def test_capture_caller_observes_compaction_failure(capture_compact, monkeypatch, failure):
    svc, state, task, trace, controls = capture_compact
    epoch = svc.claim_consumer(state.session_token, kind="capture")
    svc._active_turn_marker(state, register=True)
    if failure == "short":
        controls["save_messages"](controls["store"].load(
            "capture-compact", user_id="user")[:3])
    elif failure == "lock":
        monkeypatch.setattr(task, "_acquire_context_op", lambda *_a, **_k: False)
    else:
        controls[failure] = failure != "flush"
    with pytest.raises(RuntimeError, match={
        "fail": "compact failed", "flush": "did not flush",
        "short": "Not enough messages", "lock": "Timeout waiting",
    }[failure]):
        svc._compact_captured_turn(state, epoch)
    assert ("kill" in trace) is (failure == "fail")
    assert "wake" not in trace


def test_capture_rechecks_owner_after_writer_flush(capture_compact, monkeypatch):
    from core.conversation_writer import ConversationWriter

    svc, state, task, trace, _controls = capture_compact
    epoch = svc.claim_consumer(state.session_token, kind="capture")
    svc._active_turn_marker(state, register=True)
    writer = ConversationWriter.for_conversation(state.conversation_id)
    original_flush = writer.flush

    def takeover(**kwargs):
        result = original_flush(**kwargs)
        task._active_turns["capture-compact:assistant"] = {"owner_id": "new-worker"}
        return result

    monkeypatch.setattr(writer, "flush", takeover)
    svc._compact_captured_turn(state, epoch)
    assert not state.closed
    assert not any(event in trace for event in ("kill", "cancel", "compact", "wake"))
    assert "unlock" in trace


def test_antigravity_exact_session_eviction_preserves_replacement(monkeypatch):
    from core.antigravity_observer_pool import AntigravityObserverPool

    pool = object.__new__(AntigravityObserverPool)
    pool._lock = threading.RLock()
    key = ("user", "conversation", "assistant", "service")
    old = SimpleNamespace(key=key, name="old", session_token="old-token")
    replacement = SimpleNamespace(key=key, name="new", session_token="new-token")
    other_key = ("user", "conversation", "other", "service")
    other = SimpleNamespace(key=other_key, name="other", session_token="other-token")
    pool._sessions = {key: old, other_key: other}
    killed = []

    def kill(state):
        assert key not in pool._sessions
        pool._sessions[key] = replacement
        killed.append(state)

    monkeypatch.setattr(pool, "kill", kill)
    assert pool.kill_and_evict_by_session_token("old-token", "compact_started") == 1
    assert killed == [old]
    assert pool._sessions == {key: replacement, other_key: other}
    assert pool.kill_and_evict_by_session_token("old-token", "compact_started") == 0
    assert pool.kill_and_evict_by_session_token("", "compact_started") == 0
    assert pool._sessions[key] is replacement


def test_context_lock_grants_only_one_of_two_simultaneous_contenders():
    from concurrent.futures import ThreadPoolExecutor
    from tasks.ai.agent_actions import AgentActionsMixin

    event = threading.Event()
    event.set()
    barrier = threading.Barrier(2)
    first_wait = threading.local()

    def wait(timeout):
        ready = event.wait(timeout)
        if not getattr(first_wait, "done", False):
            first_wait.done = True
            barrier.wait(timeout=2)
        return ready

    shared = SimpleNamespace(wait=wait, is_set=event.is_set, clear=event.clear)
    task = SimpleNamespace(_context_op_lock=threading.Lock(),
                           _get_context_op_event=lambda *_args: shared)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(AgentActionsMixin._acquire_context_op,
                                   task, "conv", "assistant", timeout=0.2)
                   for _ in range(2)]
        assert sorted(future.result(timeout=3) for future in futures) == [False, True]
