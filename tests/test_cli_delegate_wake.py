"""A delegate that wakes a live CLI agent reaches the CLI in the prompt.

Incident 2026-09-25 (GameDev2): GD4's delegate woke GD2, whose Claude Code
session was live. A live session loads no context, and a delegate wake is
never re-injected from the FlowFile body, so the delegate reached the CLI
only through the catch-up block ("since the agent's last reply"). While the
turn was being prepared, GD5's delegate was live-submitted and answered;
that reply moved the catch-up start past GD4's delegate. The prompt came
out empty, the turn failed with "nothing to submit", the Claude session
marker was wiped and GD4's delegate was never delivered.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.llm_client import LLMClient, LLMMessage
from core._cci_pool_spawn import InteractiveContainer
from tasks.ai._agentctx_p2 import _PACPhase2Mixin
from tasks.ai.agent_serialization import AgentSerializationMixin

AGENT = "GameDev2"


def _row(msg_id, ts, content, source, role="user"):
    return {"role": role, "msg_id": msg_id, "ts": ts, "content": content,
            "source": source}


def _delegate(msg_id, ts, sender, text):
    return _row(msg_id, ts, f"Here is a message from agent '{sender}': {text}",
                {"type": "agent_delegate", "from": sender, "to": AGENT,
                 "target_agent": AGENT})


def _own_reply(msg_id, ts, text):
    return _row(msg_id, ts, text, {"type": "agent", "name": AGENT},
                role="assistant")


# The incident's context: GD4's delegate, then GD5's delegate (live-
# submitted) and GD2's reply to it, all before the woken turn's prompt.
CONTEXT = [
    _own_reply("r0", 100.0, "earlier reply"),
    _delegate("gd4", 110.0, "GameDev4", "GD4 -> GD2. my claim [2] falls"),
    _delegate("gd5", 120.0, "GameDev5", "GD5 -> GD2. clarification adopted"),
    _own_reply("r1", 125.0, "Nothing to decide: GD5 adopted it"),
]


def _store(rows):
    store = MagicMock()
    store.load_agent_context.side_effect = lambda cid, agent: list(rows)
    return patch("core.conversation_store.ConversationStore.instance",
                 return_value=store)


class _Phase(_PACPhase2Mixin, AgentSerializationMixin):
    pass


def _wake_state(msg_id="gd4", messages=None):
    return SimpleNamespace(
        flowfile=SimpleNamespace(get_attribute=lambda name: ""),
        body_json={"message": "GD4 -> GD2. my claim [2] falls",
                   "msg_id": msg_id},
        messages=list(messages or []),
        conversation_id="conv",
        _context_agent=AGENT,
    )


def test_live_cli_wake_carries_its_delegate_row():
    st = _wake_state()
    with _store(CONTEXT):
        _Phase()._inject_cli_delegate_row(st)
    assert [m.msg_id for m in st.messages] == ["gd4"]
    assert st.messages[0].role == "user"
    assert "Here is a message from agent 'GameDev4'" in st.messages[0].content
    assert st.messages[0].source["type"] == "agent_delegate"


def test_wake_row_is_not_added_twice():
    st = _wake_state(messages=[LLMMessage(role="user", content="x",
                                          msg_id="gd4",
                                          conversation_id="conv")])
    with _store(CONTEXT):
        _Phase()._inject_cli_delegate_row(st)
    assert len(st.messages) == 1


def test_row_not_yet_persisted_leaves_the_catch_up_in_charge():
    st = _wake_state(msg_id="not-written-yet")
    with _store(CONTEXT):
        _Phase()._inject_cli_delegate_row(st)
    assert st.messages == []


def _live_state():
    return InteractiveContainer(
        key=("u", "conv", AGENT, ""), name="c", workdir="/w",
        container_workdir="/cw", session_token="tok",
        event_service_id="es", internal_token="it")


def _prompt(messages):
    client = LLMClient("claude-code-interactive")
    with _store(CONTEXT):
        return client._cci_prompt(
            messages, None, "/w", "/cw", "u", "conv",
            initial_context=False, agent_name=AGENT, state=_live_state())


def test_woken_prompt_holds_the_delegate_past_the_catch_up_start():
    # The catch-up starts after r1, past GD4's delegate: the prompt still
    # carries it, because the turn's own messages do.
    st = _wake_state()
    with _store(CONTEXT):
        _Phase()._inject_cli_delegate_row(st)
    prompt = _prompt([LLMMessage(role="system", content="rules",
                                 conversation_id="conv"), *st.messages])
    assert "GD4 -> GD2. my claim [2] falls" in prompt


def test_catch_up_skips_rows_the_prompt_already_carries():
    client = LLMClient("claude-code", config={"api_key": "k"})
    rows = [_own_reply("r0", 100.0, "earlier reply"),
            _delegate("gd4", 110.0, "GameDev4", "GD4 -> GD2. claim"),
            _row("n1", 115.0, "team news", {"type": "agent",
                                             "name": "GameDev7"})]
    with _store(rows):
        text = client._build_catchup_context("conv", AGENT,
                                             exclude_msg_ids={"gd4"})
        assert "team news" in text
        assert "GD4 -> GD2" not in text
        # The anchor still moved past the excluded row.
        assert client._build_catchup_context("conv", AGENT) == ""


def test_woken_prompt_sends_the_delegate_once():
    # Normal case: the catch-up starts before the delegate. It is sent in
    # the prompt's live part only, not a second time in the catch-up.
    rows = CONTEXT[:2]
    msg = LLMMessage(role="user", content=rows[1]["content"], msg_id="gd4",
                     source=rows[1]["source"], conversation_id="conv")
    client = LLMClient("claude-code-interactive")
    with _store(rows):
        prompt = client._cci_prompt(
            [msg], None, "/w", "/cw", "u", "conv",
            initial_context=False, agent_name=AGENT, state=_live_state())
    assert prompt.count("GD4 -> GD2. my claim [2] falls") == 1
