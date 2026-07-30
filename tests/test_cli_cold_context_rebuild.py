"""A CLI session that dies between the liveness check and the turn.

The context phase empties the message list whenever it finds a live session,
because a resume only needs the delta. That check reserves nothing: the idle
sweeper, a cleanup, a crashed process or a stopped container can take the
session away before the provider acquires its turn lock. The provider then
correctly goes cold -- and used to start a fresh CLI with a list that no longer
described the conversation: no transcript, no persona, no skills, no tool
configuration for that turn.
"""

import logging

from core.llm_providers.cli_shared import LLMCliSharedMixin


class _Client(LLMCliSharedMixin):
    pass


def test_an_ordinary_cold_start_is_left_alone():
    """No callback means the context phase never assumed a session."""
    client = _Client()
    messages = ["real", "context"]
    assert client._cli_cold_context(messages) is messages


def test_a_vanished_session_gets_the_full_context_back():
    client = _Client()
    client._pawflow_cold_context_rebuild = lambda: ["system", "history", "user"]
    assert client._cli_cold_context(["delta"]) == ["system", "history", "user"]


def test_the_callback_fires_at_most_once():
    """A stale callback must not rebuild a later turn whose context is fine."""
    client = _Client()
    calls = []

    def _rebuild():
        calls.append(1)
        return ["rebuilt"]

    client._pawflow_cold_context_rebuild = _rebuild
    assert client._cli_cold_context(["delta"]) == ["rebuilt"]
    second = ["delta2"]
    assert client._cli_cold_context(second) is second
    assert calls == [1]


def test_a_failing_rebuild_keeps_the_turn_alive(caplog):
    """Losing context is bad; losing the turn is worse."""
    client = _Client()

    def _boom():
        raise RuntimeError("store unavailable")

    client._pawflow_cold_context_rebuild = _boom
    messages = ["delta"]
    with caplog.at_level(logging.ERROR):
        assert client._cli_cold_context(messages) is messages
    assert "cold context rebuild failed" in caplog.text


def test_an_empty_rebuild_does_not_blank_the_turn():
    client = _Client()
    client._pawflow_cold_context_rebuild = lambda: []
    messages = ["delta"]
    assert client._cli_cold_context(messages) is messages


# ── The context phase arms it ───────────────────────────────────────────────

class _St:
    def __init__(self):
        self.client = _Client()
        self.conversation_id = "conv1234abcd"
        self.messages = []
        self._context_diverged = True
        self._uses_pawflow_initial = False


def _phase():
    from tasks.ai._agentctx_p2 import _PACPhase2Mixin

    class _Phase(_PACPhase2Mixin):
        def __init__(self):
            self.loaded = 0

        def _load_cold_cli_context(self, st):
            self.loaded += 1
            st.messages = ["system", "history"]

    return _Phase()


def test_arming_installs_a_callback_that_reloads_the_cold_context():
    phase, st = _phase(), _St()
    phase._arm_cold_context_rebuild(st)

    rebuilt = st.client._cli_cold_context(["delta"])
    assert rebuilt == ["system", "history"]
    assert phase.loaded == 1


def test_arming_alone_loads_nothing():
    """The happy path -- session really still live -- must stay free."""
    phase, st = _phase(), _St()
    phase._arm_cold_context_rebuild(st)
    assert phase.loaded == 0
    assert st.messages == []


def test_the_rebuild_clears_the_diverged_flag_so_compaction_applies():
    """`_context_diverged` was set to skip compaction for a resume. A rebuilt
    cold context is the full transcript and must be compactable again."""
    phase, st = _phase(), _St()
    phase._arm_cold_context_rebuild(st)
    st.client._cli_cold_context(["delta"])
    assert st._context_diverged is False


def test_a_stateless_state_without_a_client_is_survivable():
    phase, st = _phase(), _St()
    st.client = None
    phase._arm_cold_context_rebuild(st)  # must not raise


# ── Both CLI providers consult it on their cold path ────────────────────────

def test_codex_recovers_the_context_before_building_its_cold_prompt():
    from pathlib import Path
    src = Path("core/llm_providers/_codex_app_stream.py").read_text(encoding="utf-8")
    call = src.index("self._cli_cold_context(messages)")
    build = src.index("self._codex_app_full_initial_text(")
    assert call < build, "the cold prompt is built before the context is back"


def test_gemini_forces_a_real_cold_start_when_it_rebuilds():
    """Loading the stored session on top of a rebuilt context would send the
    transcript twice and leave the gauge on the dead session's numbers."""
    from pathlib import Path
    src = Path("core/llm_providers/_gemini_stream.py").read_text(encoding="utf-8")
    block = src[src.index("_recovered = self._cli_cold_context(messages)"):]
    block = block[:block.index("if session_id:")]
    assert 'session_id = ""' in block
    assert 'prompt_mode = "cold-session-vanished"' in block
    assert "initial_text = _prompt_text_for_mode(prompt_mode)" in block
