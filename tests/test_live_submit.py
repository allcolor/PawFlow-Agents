"""Non-user messages reach a running agent as an inbox item plus a notice.

A delegate, a background result or a due wake-up never interrupts (no
Escape) and its content is never pasted: a running agent gets an inbox item
and only the imperative notice (docs/inbox_delivery.md). An idle agent keeps
the queue and wake path. The full path on a real store is covered by
tests/test_agent_inbox.py.
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
    state = SimpleNamespace(name="c", last_error="", session_token="t",
                            send_lock=threading.RLock())

    assert pool.send_queued(state, "[PawFlow inbox: ACTION REQUIRED] go") is True

    assert pastes == ["[PawFlow inbox: ACTION REQUIRED] go"]
    assert keys == ["Enter"]


def test_send_queued_fails_when_the_paste_fails():
    keys = []
    pool = _bare_pool(InteractiveClaudeCodePool, keys, [])
    pool._paste_text = lambda state, text: False
    state = SimpleNamespace(name="c", last_error="", session_token="t",
                            send_lock=threading.RLock())

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
    wakes, stored, notices = [], [], []
    active = set()
    monkeypatch.setattr("core.pending_queue.PendingQueue.for_agent",
                        lambda cid, agent: queue)
    monkeypatch.setattr(
        "tasks.ai.agent_loop.AgentLoopTask.wake_agent",
        lambda cid, agent, **kw: wakes.append((agent, kw.get("even_if_active"))))
    monkeypatch.setattr(
        "tasks.ai.agent_loop.AgentLoopTask.is_agent_active",
        lambda cid, agent: agent in active)
    monkeypatch.setattr(
        "core.agent_inbox.enqueue_item",
        lambda cid, agent, msg, kind, sender: stored.append(
            (msg["msg_id"], kind, sender)) or True)
    monkeypatch.setattr(
        "core.agent_inbox.peek_notice",
        lambda cid, agent, only_new=False: ("[PawFlow inbox: ACTION REQUIRED] n", 7))
    monkeypatch.setattr(
        "core.agent_inbox.record_notice",
        lambda cid, agent, seq: notices.append(seq))
    inst = SimpleNamespace(_active_contexts_lock=threading.Lock(),
                           _active_claude_client={}, _active_contexts={})
    monkeypatch.setattr("tasks.ai.agent_loop.AgentLoopTask._live_instance", inst)
    monkeypatch.setattr(live_submit.time, "sleep", lambda _s: None)
    return SimpleNamespace(queue=queue, wakes=wakes, inst=inst, active=active,
                           stored=stored, notices=notices)


def _msg(mid="d1"):
    return {"role": "user", "msg_id": mid, "ts": 1.0,
            "content": "[GameDev2 to agent GameDev7]: new task"}


def _submit(agent="GameDev7"):
    return live_submit.submit_or_queue(
        "conv", agent, _msg(), "delegate_reply",
        kind="delegate_message", sender="GameDev2")


class _Client:
    def __init__(self, provider="codex-interactive", ok=True):
        self.provider = provider
        self.ok = ok
        self.sent = []

    def send_queued_message(self, text, **kw):
        self.sent.append(text)
        return self.ok


def test_idle_agent_gets_an_inbox_item_and_a_wake(wired):
    assert _submit() is True
    assert wired.queue.items == []
    assert wired.wakes == [("GameDev7", True)]
    assert wired.stored == [("d1", "delegate_message", "GameDev2")]


def test_running_api_agent_gets_an_inbox_item_and_no_paste(wired):
    wired.active.add("GameDev5")
    wired.inst._active_claude_client["conv:GameDev5"] = _Client(provider="openai")
    assert _submit("GameDev5") is True
    assert wired.stored == [("d1", "delegate_message", "GameDev2")]
    assert wired.queue.items == []
    assert wired.wakes == [("GameDev5", True)]


def test_running_cli_agent_gets_the_notice_pasted(wired):
    wired.active.add("GameDev7")
    client = _Client()
    live_submit._paste_notice(client, "conv", "GameDev7", "u")
    assert client.sent == ["[PawFlow inbox: ACTION REQUIRED] n"]
    assert wired.notices == [7]


def test_a_refused_notice_is_retried_while_the_turn_runs(wired):
    client = _Client(ok=False)
    wired.inst._active_claude_client["conv:GameDev7"] = client
    live_submit._paste_notice(client, "conv", "GameDev7", "u")
    assert len(client.sent) == 1 + len(live_submit._RETRY_DELAYS_SECONDS)
    assert wired.notices == [], "an unpasted notice is not recorded"


def test_running_cli_agent_is_found_case_insensitively(wired):
    client = _Client(provider="claude-code-interactive")
    wired.inst._active_claude_client["conv:GameDev7"] = client
    assert live_submit._active_cli_client("conv", "gamedev7") is client


def test_client_dispatch_only_for_tmux_providers():
    from core.llm_client import LLMClient
    assert LLMClient("openai", config={"api_key": "k"}).send_queued_message("x") is False
