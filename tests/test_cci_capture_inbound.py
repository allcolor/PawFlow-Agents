"""A webchat message must reach a tmux session running a captured turn.

Production sequence (2026-07-28): a PawFlow turn ended while a background
tool was still running. Its result landed in the Claude Code container, which
resumed on its own — a captured turn: `_active_turns` registered, but no
streaming worker, no `_active_contexts` entry and no `_active_claude_client`.

agent_streaming reads that combination as "already active but not preemptable"
and parks the message in the PendingQueue. For a real turn that is a brief
settling window and the turn drains at its end; for a captured turn it holds
for the whole turn and nothing ever drains. The user saw the agent up in
Active Agents, the tmux visibly working, and every message they sent reaching
nothing — until a force stop discarded the queue.

The MITM proxy between the tmux and PawFlow settles the question the stale
bookkeeping could not: its WebSocket is up exactly while a container lives,
so a connected session proves there is a tmux to deliver into.
"""

import pytest

from tasks.ai.agent_streaming import AgentStreamingMixin


class _Container:
    name = "cci-test"


class _Pool:
    """Stand-in for InteractiveClaudeCodePool."""

    def __init__(self, container=None):
        self.container = container
        self.typed = []

    def find_live_by_conv_agent(self, _conv, _agent):
        return self.container

    def send_interrupt(self, state, text):
        self.typed.append((state, text))
        return True


@pytest.fixture
def wired(monkeypatch):
    """Patch the proxy-session lookup + pool; return (holder, pool)."""
    import services.cc_interactive_event_service as ev
    import core.claude_code_interactive_pool as poolmod

    holder = {"live": None}
    pool = _Pool(_Container())

    monkeypatch.setattr(ev.CCInteractiveEventService, "live_session",
                        classmethod(lambda cls, c, a: holder["live"]))
    monkeypatch.setattr(poolmod.InteractiveClaudeCodePool, "instance",
                        classmethod(lambda cls: pool))
    return holder, pool


def test_message_is_typed_into_the_live_tmux(wired):
    holder, pool = wired
    holder["live"] = object()

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", "hello") is True
    assert [t for _s, t in pool.typed] == ["hello"]


def test_no_live_proxy_session_means_no_injection(wired):
    """No connected proxy = no live tmux = nothing to type into.

    The proxy WebSocket is the evidence of liveness; without it there is no
    container, and the message belongs in the queue.
    """
    holder, pool = wired
    holder["live"] = None

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", "hello") is False
    assert pool.typed == []


def test_dead_container_falls_back_to_the_queue(wired):
    holder, pool = wired
    holder["live"] = object()
    pool.container = None

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", "hello") is False


def test_refused_send_falls_back_to_the_queue(wired):
    holder, pool = wired
    holder["live"] = object()
    pool.send_interrupt = lambda _s, _t: False

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", "hello") is False


def test_codex_captured_turn_uses_the_codex_pool_and_preempts(wired, monkeypatch):
    holder, claude_pool = wired
    import core.codex_interactive_pool as codex_poolmod

    live = type("Live", (), {"provider": "codex-interactive"})()
    holder["live"] = live
    codex_pool = _Pool(_Container())
    monkeypatch.setattr(codex_poolmod.CodexInteractivePool, "instance",
                        classmethod(lambda cls: codex_pool))

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "assistant", "preempt now") is True
    assert codex_pool.typed == [(codex_pool.container, "preempt now")]
    assert claude_pool.typed == []


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_text_is_never_typed(wired, text):
    holder, pool = wired
    holder["live"] = object()

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", text) is False
    assert pool.typed == []


def test_delivery_never_raises_into_the_request_path(monkeypatch):
    """A broken lookup must fall back to queuing, not 500 the user's POST."""
    import services.cc_interactive_event_service as ev

    def _boom(cls, _c, _a):
        raise RuntimeError("pool exploded")

    monkeypatch.setattr(ev.CCInteractiveEventService, "live_session",
                        classmethod(_boom))

    assert AgentStreamingMixin._deliver_to_captured_tmux(
        "80c37670", "claude", "hello") is False
