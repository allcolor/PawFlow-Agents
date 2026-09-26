"""Tests for the CCI multi-message drain fix.

A retrigger turn can carry several drained user messages (e.g. N delegate
results preempted during the previous turn). The live-session prompt build
must:
1. submit EVERY trailing not-yet-submitted user message, one per paste: the
   first goes in the turn's prompt, each other one right after it;
2. never re-paste a msg_id the session has already submitted (dedup);
3. not fall back to re-pasting the latest user text when the whole tail was
   already submitted.
"""

import pytest

from core.llm_client import LLMClient, LLMClientError, LLMMessage
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


def test_live_text_submits_trailing_user_messages_one_by_one():
    client = _client()
    text = client._cci_live_text(_msgs(), state=_state())

    assert text == "[Delegate result A]"
    assert client._cci_live_followups == [
        ("m3", "[Delegate result B]"), ("m4", "[Delegate result C]")]
    # Messages before the last assistant reply are already in the CLI context.
    assert "first request" not in text
    assert "answer one" not in text
    assert client._cci_pending_live_msg_ids == ["m2"]


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
    assert client._cci_pending_live_msg_ids == ["m2"]
    assert client._cci_live_followups == [("m4", "[Delegate result C]")]


def test_prompt_carries_first_message_and_queues_the_others(tmp_path):
    client = _client()
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a", state=_state())

    assert "[Delegate result A]" in prompt
    assert "[Delegate result B]" not in prompt
    assert [mid for mid, _ in client._cci_live_followups] == ["m3", "m4"]


class _FakePool:
    def __init__(self, fail=()):
        self.sent = []
        self.fail = set(fail)

    def send_queued(self, state, text, *, msg_id=""):
        self.sent.append(text)
        return text not in self.fail


def test_followups_are_submitted_one_paste_each_in_order():
    client = _client()
    state = _state()
    client._cci_live_text(_msgs(), state=state)
    pool = _FakePool()
    client._cci_submit_followups(pool, state)

    assert pool.sent == ["[Delegate result B]", "[Delegate result C]"]
    assert {"m3", "m4"} <= state.submitted_msg_ids
    assert client._had_preempts_this_turn is True
    assert client._cci_live_followups == []


def test_failed_followup_is_not_counted_as_submitted():
    client = _client()
    state = _state()
    client._cci_live_text(_msgs(), state=state)
    # The provider records the whole turn as conveyed after its paste.
    state.submitted_msg_ids.update({"m2", "m3", "m4"})
    client._cci_submit_followups(
        _FakePool(fail={"[Delegate result B]"}), state)

    assert "m3" not in state.submitted_msg_ids
    assert "m4" in state.submitted_msg_ids


def test_prompt_does_not_repaste_fully_submitted_tail(tmp_path):
    client = _client()
    # The old fallback re-pasted the latest user text -> double delivery.
    # Nothing is left to submit, and a blank paste is refused as well.
    with pytest.raises(LLMClientError, match="nothing to submit"):
        client._cci_prompt(
            _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
            initial_context=False, agent_name="a",
            state=_state(submitted={"m2", "m3", "m4"}))


def test_prompt_without_state_still_renders_tail(tmp_path):
    client = _client()
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a")

    assert "[Delegate result A]" in prompt
    assert client._cci_live_followups[-1] == ("m4", "[Delegate result C]")


def test_interactive_container_has_submitted_msg_ids_set():
    st = _state()
    assert st.submitted_msg_ids == set()
    st.submitted_msg_ids.add("m1")
    assert "m1" in st.submitted_msg_ids
