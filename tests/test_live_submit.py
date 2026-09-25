"""Non-user messages are submitted to a running CLI agent one at a time.

A delegate, a background result or a due wake-up never interrupts (no
Escape) but is submitted on arrival, one paste + Enter per message.
"""

import threading
from types import SimpleNamespace

import pytest

from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.codex_interactive_pool import CodexInteractivePool
import tasks.ai._live_submit as live_submit


def _bare_pool(cls, keys, pastes):
    pool = cls.__new__(cls)
    pool._is_alive = lambda name: True
    pool._cancel_copy_mode = lambda state: None
    pool._remember_injected_prompt = lambda state, text: None
    pool._remember_injected_prompt_for_event_service = lambda state, text: None
    pool._composer_safe_text = lambda text: text
    pool._paste_settle_seconds = lambda: 0
    pool._paste_text = lambda state, text: pastes.append(text) or True
    pool.send_keys = lambda state, k: keys.extend(k) or True
    pool._verify_submitted = lambda *a, **kw: True
    pool._prepare_prompt_input = lambda state: pytest.fail(
        "a queued submission must not prepare (Escape) the composer")
    pool._check_native_compaction = lambda state: None
    pool._leave_backtrack_overlay = lambda state: True
    return pool


@pytest.mark.parametrize("cls", [InteractiveClaudeCodePool, CodexInteractivePool])
def test_send_queued_pastes_and_submits_without_escape(cls):
    keys, pastes = [], []
    pool = _bare_pool(cls, keys, pastes)
    state = SimpleNamespace(name="c", last_error="", session_token="t")

    assert pool.send_queued(state, "[GameDev2 to agent GameDev7]: go") is True

    assert pastes == ["[GameDev2 to agent GameDev7]: go"]
    assert keys == ["Enter"]


def test_send_queued_fails_when_the_paste_fails():
    keys = []
    pool = _bare_pool(InteractiveClaudeCodePool, keys, [])
    pool._paste_text = lambda state, text: False
    state = SimpleNamespace(name="c", last_error="", session_token="t")

    assert pool.send_queued(state, "x") is False
    assert keys == []


class _Queue:
    def __init__(self):
        self.items = []

    def enqueue(self, message, source=""):
        self.items.append((message["msg_id"], source))
        return True


@pytest.fixture
def wired(monkeypatch):
    queue = _Queue()
    wakes = []
    monkeypatch.setattr("core.pending_queue.PendingQueue.for_agent",
                        lambda cid, agent: queue)
    monkeypatch.setattr(
        "tasks.ai.agent_loop.AgentLoopTask.wake_agent",
        lambda cid, agent, **kw: wakes.append((agent, kw.get("even_if_active"))))
    inst = SimpleNamespace(_active_contexts_lock=threading.Lock(),
                           _active_claude_client={})
    monkeypatch.setattr("tasks.ai.agent_loop.AgentLoopTask._live_instance", inst)
    return SimpleNamespace(queue=queue, wakes=wakes, inst=inst)


def _msg(mid="d1"):
    return {"role": "user", "msg_id": mid, "ts": 1.0,
            "content": "[GameDev2 to agent GameDev7]: new task"}


class _Client:
    def __init__(self, provider="codex-interactive", ok=True):
        self.provider = provider
        self.ok = ok
        self.sent = []

    def send_queued_message(self, text, **kw):
        self.sent.append((text, kw.get("msg_id")))
        return self.ok


def test_idle_agent_gets_the_queue_and_a_wake(wired):
    assert live_submit.submit_or_queue("conv", "GameDev7", _msg(),
                                       "delegate_reply") is True
    assert wired.queue.items == [("d1", "delegate_reply")]
    assert wired.wakes == [("GameDev7", True)]


def test_api_agent_keeps_the_queue(wired):
    wired.inst._active_claude_client["conv:GameDev5"] = _Client(provider="openai")
    live_submit.submit_or_queue("conv", "GameDev5", _msg(), "delegate_reply")
    assert wired.queue.items == [("d1", "delegate_reply")]


def test_running_cli_agent_gets_it_submitted_now(wired):
    client = _Client()
    live_submit._submit_live(client, "conv", "GameDev7", _msg(),
                             "delegate_reply", "u", True, "", True)
    assert client.sent == [("[GameDev2 to agent GameDev7]: new task", "d1")]
    # Persisted by the final drain, never a second turn.
    assert wired.queue.items == [("d1", "preempt_rescue")]
    assert wired.wakes == []


def test_failed_submission_falls_back_to_queue_and_wake(wired):
    live_submit._submit_live(_Client(ok=False), "conv", "GameDev7", _msg(),
                             "delegate_reply", "u", True, "", True)
    assert wired.queue.items == [("d1", "delegate_reply")]
    assert wired.wakes == [("GameDev7", True)]


def test_running_cli_agent_is_found_case_insensitively(wired):
    client = _Client(provider="claude-code-interactive")
    wired.inst._active_claude_client["conv:GameDev7"] = client
    assert live_submit._active_cli_client("conv", "gamedev7") is client


def test_client_dispatch_only_for_tmux_providers():
    from core.llm_client import LLMClient
    assert LLMClient("openai", config={"api_key": "k"}).send_queued_message("x") is False
