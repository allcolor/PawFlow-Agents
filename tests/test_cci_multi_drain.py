"""Tests for the CCI multi-message drain fix.

A retrigger turn can carry several drained user messages (e.g. N delegate
results preempted during the previous turn). The live-session prompt build
must:
1. paste ALL trailing not-yet-submitted user messages, not just the newest;
2. never re-paste a msg_id the session has already submitted (dedup);
3. not fall back to re-pasting the latest user text when the whole tail was
   already submitted.
"""

from core.llm_client import LLMClient, LLMMessage
from core._cci_pool_spawn import InteractiveContainer


def _client():
    return LLMClient("claude-code-interactive")


def _state(submitted=None):
    st = InteractiveContainer(
        key=("u", "conv", "a", ""), name="c", workdir="/w",
        container_workdir="/cw", session_token="tok",
        event_service_id="es", internal_token="it")
    if submitted:
        st.submitted_msg_ids.update(submitted)
    return st


def _msgs():
    return [
        LLMMessage(role="system", content="rules", conversation_id="conv"),
        LLMMessage(role="user", content="first request", conversation_id="conv",
                   msg_id="m1"),
        LLMMessage(role="assistant", content="answer one", conversation_id="conv"),
        LLMMessage(role="user", content="[Delegate result A]", conversation_id="conv",
                   msg_id="m2"),
        LLMMessage(role="user", content="[Delegate result B]", conversation_id="conv",
                   msg_id="m3"),
        LLMMessage(role="user", content="[Delegate result C]", conversation_id="conv",
                   msg_id="m4"),
    ]


def test_live_text_renders_all_trailing_user_messages():
    client = _client()
    text = client._cci_live_text(_msgs(), state=_state())

    assert "[Delegate result A]" in text
    assert "[Delegate result B]" in text
    assert "[Delegate result C]" in text
    assert text.index("result A") < text.index("result B") < text.index("result C")
    # Messages before the last assistant reply are already in the CLI context.
    assert "first request" not in text
    assert "answer one" not in text
    assert client._cci_pending_live_msg_ids == ["m2", "m3", "m4"]


def test_live_text_skips_already_submitted_msg_ids():
    client = _client()
    text = client._cci_live_text(_msgs(), state=_state(submitted={"m2", "m3"}))

    assert "[Delegate result A]" not in text
    assert "[Delegate result B]" not in text
    assert "[Delegate result C]" in text
    assert client._cci_pending_live_msg_ids == ["m4"]
    assert client._cci_live_all_submitted is False


def test_live_text_all_submitted_returns_empty_and_flags():
    client = _client()
    text = client._cci_live_text(
        _msgs(), state=_state(submitted={"m2", "m3", "m4"}))

    assert text == ""
    assert client._cci_live_all_submitted is True


def test_live_text_skips_display_only_messages():
    client = _client()
    msgs = _msgs()
    msgs[4].display_only = True
    text = client._cci_live_text(msgs, state=_state())

    assert "[Delegate result B]" not in text
    assert client._cci_pending_live_msg_ids == ["m2", "m4"]


def test_prompt_contains_every_drained_message(tmp_path):
    client = _client()
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a", state=_state())

    assert "[Delegate result A]" in prompt
    assert "[Delegate result B]" in prompt
    assert "[Delegate result C]" in prompt


def test_prompt_does_not_repaste_fully_submitted_tail(tmp_path):
    client = _client()
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a",
        state=_state(submitted={"m2", "m3", "m4"}))

    # The old fallback re-pasted the latest user text -> double delivery.
    assert "[Delegate result C]" not in prompt


def test_prompt_without_state_still_renders_tail(tmp_path):
    client = _client()
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a")

    assert "[Delegate result A]" in prompt
    assert "[Delegate result C]" in prompt


def test_interactive_container_has_submitted_msg_ids_set():
    st = _state()
    assert st.submitted_msg_ids == set()
    st.submitted_msg_ids.add("m1")
    assert "m1" in st.submitted_msg_ids
