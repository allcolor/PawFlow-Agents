"""A retrigger turn with nothing new to paste must not fail the agent.

2026-10-28 10:26:24: GameDev8 failed with
``no new message for the live CLI session: this turn has nothing to submit``,
surfaced to the user as "Claude Code session lost" and losing the delegate
reply.

The log shows how it happened: the loop finished, ``post-loop retrigger-check``
found ``flag=True`` and re-triggered the loop for queued messages. That turn
built a prompt whose only tail messages were already in
``state.submitted_msg_ids`` -- the pending queue had been drained by the live
submit path -- so ``_cci_live_text`` returned nothing and the guard raised.

Nothing is wrong in that situation: every message reached the CLI during the
previous turn. Raising turned a normal no-op into a hard failure.
"""

from types import SimpleNamespace

import pytest

from core.llm_providers.claude_code_interactive import LLMClaudeCodeInteractiveMixin


class _Client(LLMClaudeCodeInteractiveMixin):
    def __init__(self):
        self._cci_active_agent_name = "GameDev8"
        self._cci_live_followups = []
        self._cci_live_all_submitted = False
        self._cci_pending_live_msg_ids = []

    def _build_catchup_context(self, *_a, **_k):
        return ""

    def _cci_catchup_context(self, *_a, **_k):
        return ""

    def _serialize_messages_for_cli(self, _messages, _tools):
        return "system", ""

    def _cci_materialize_images(self, *_a, **_k):
        return []


def _msg(role, msg_id, content):
    return SimpleNamespace(role=role, msg_id=msg_id, content=content,
                           display_only=False)


def _state(submitted):
    return SimpleNamespace(submitted_msg_ids=set(submitted), workdir="/w",
                           container_workdir="/w", initial_context_loaded=True,
                           name="c")


def test_an_already_submitted_tail_reports_nothing_to_paste():
    """The tail existed, everything in it was already pasted."""
    client = _Client()
    messages = [_msg("user", "u1", "already sent")]

    text = client._cci_live_text(messages, state=_state(["u1"]))

    assert text == ""
    assert client._cci_live_all_submitted is True


def test_an_empty_tail_does_not_claim_everything_was_submitted():
    """No user message at all is a different state from 'all submitted'."""
    client = _Client()
    messages = [_msg("assistant", "a1", "done")]

    text = client._cci_live_text(messages, state=_state([]))

    assert text == ""
    assert client._cci_live_all_submitted is False


def test_the_prompt_becomes_none_when_everything_was_already_pasted():
    """The caller must be able to tell 'nothing to do' from 'a real bug'."""
    client = _Client()
    messages = [_msg("user", "u1", "already sent")]

    prompt = client._cci_prompt(
        messages, None, "/w", "/w", "user", "conv",
        initial_context=False, agent_name="GameDev8",
        state=_state(["u1"]))

    assert prompt is None


def test_the_prompt_guard_stays_for_a_really_blank_turn():
    """A blank paste is still refused: that one IS a bug."""
    client = _Client()
    messages = [_msg("assistant", "a1", "done")]

    with pytest.raises(Exception) as exc:
        client._cci_prompt(messages, None, "/w", "/w", "user", "conv",
                           initial_context=False, agent_name="GameDev8",
                           state=_state([]))

    assert "nothing to submit" in str(exc.value)
