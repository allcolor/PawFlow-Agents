"""A conversation-wide invalidation must not kill a sub-agent's session.

2026-10-25 00:23:00: a flash agent ``GameDev7::flash::M0-distance-Eb`` died with
``CC interactive session closed`` in the middle of its work. The cause was
``invalidate_claude_sessions(<parent conversation>)``: it evicts every live
interactive session whose conversation id equals the parent's, and a sub-agent
running in ``full`` context mode (``_agent_executor_loop`` leaves
``sub_conv_id`` empty) is keyed to exactly that parent conversation.

A user editing the parent's context, or any parent-scoped compaction, therefore
tore down every sub-agent that happened to be running -- their work lost, their
turn failed with a non-retryable error.

The kill must skip sessions whose AGENT is a sub-agent (``::flash::``,
``::task::``, ``::delegate::``, ``::a2a::``, ``::task_verify::``): those are not
part of the parent's context, and their own cleanup path owns them.
"""

from core.claude_code_interactive_pool import InteractiveClaudeCodePool


class _State:
    def __init__(self, name, conv_id, agent_name):
        self.name = name
        self.conversation_id = conv_id
        self.agent_name = agent_name

    def __repr__(self):
        return f"<state {self.name}>"


def _pool_with(monkeypatch, sessions):
    pool = InteractiveClaudeCodePool()
    killed = []
    pool._sessions = dict(sessions)
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda _s: None)
    monkeypatch.setattr(pool, "_unregister_event_session", lambda _s: None)
    monkeypatch.setattr(pool, "_kill_container", lambda name: killed.append(name))
    return pool, killed


def test_parent_invalidation_keeps_subagent_sessions(monkeypatch):
    """The parent's main agent dies; the running flash agent must survive."""
    parent = _State("c-main", "conv", "GameDev7")
    flash = _State("c-flash", "conv", "GameDev7::flash::M0-distance-Eb")
    key_main = ("user", "conv", "GameDev7", "")
    key_flash = ("user", "conv", "GameDev7::flash::M0-distance-Eb", "")
    pool, killed = _pool_with(
        monkeypatch, {key_main: parent, key_flash: flash})

    count = pool.kill_and_evict_by_conv("conv", reason="invalidate_claude_sessions")

    assert count == 1
    assert killed == ["c-main"]
    assert key_flash in pool._sessions
    assert key_main not in pool._sessions


def test_a_subagent_conversation_cleanup_still_kills_its_sessions(monkeypatch):
    """A sub-agent's OWN cleanup must not be skipped by the new rule.

    When the conversation being cleaned up is itself a sub-conversation
    (``parent::task::<id>``), every session in it is a victim -- including the
    flash worker. Otherwise finishing a task would leak its container.
    """
    sub_conv = "conv::task::done"
    victim = _State("c-victim", sub_conv, "assistant::flash::worker")
    key = ("user", sub_conv, "assistant::flash::worker", "svc")
    pool, killed = _pool_with(monkeypatch, {key: victim})

    count = pool.kill_and_evict_by_conv(sub_conv, reason="subagent_run_finished")

    assert count == 1
    assert killed == ["c-victim"]
    assert pool._sessions == {}


def test_task_and_delegate_sessions_also_survive(monkeypatch):
    markers = ["::task::t1", "::delegate::d1", "::a2a::a1",
               "::task_verify::v1"]
    sessions = {("user", "conv", "GameDev7", ""): _State("c-main", "conv", "GameDev7")}
    for marker in markers:
        agent = f"GameDev7{marker}"
        sessions[("user", "conv", agent, "")] = _State(f"c-{agent}", "conv", agent)
    pool, killed = _pool_with(monkeypatch, sessions)

    count = pool.kill_and_evict_by_conv("conv", reason="invalidate_claude_sessions")

    assert count == 1
    assert killed == ["c-main"]
    assert len(pool._sessions) == len(markers)
