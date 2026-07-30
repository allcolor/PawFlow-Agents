"""Launching a process is a cold start, and a cold start gets the full context.

Two cases, no third one:

    1. no process running -> we launch -> cold start -> FULL context
    2. a process is running -> delta

The context phase decides which one applies, but the PROVIDER is what actually
launches, and only it can find the process gone -- it crashed, its container
was stopped -- after the context was already built as a delta. Sending that
delta to a fresh process is case 1 carrying case 2's context: a process that
knows nothing, handed a bare question with no transcript, no persona, no
skills and no tool configuration.

The provider therefore refuses to launch. The turn goes back to the context
phase with force_cold=True, which is case 1 built by the ordinary cold path --
not reassembled by hand, which is what the previous mechanism did and why it
only ever restored the transcript.
"""
import pytest

from core._llm_types import ColdStartRequired
from core.llm_providers.cli_shared import LLMCliSharedMixin


class _Client(LLMCliSharedMixin):
    pass


# -- the refusal itself ------------------------------------------------------

def test_an_ordinary_cold_start_launches_without_complaint():
    """No marker means the context phase built this turn as a cold start."""
    assert _Client()._cli_require_cold_context("codex-app") is None


def test_launching_with_a_resume_delta_is_refused():
    client = _Client()
    client._pawflow_context_is_delta = True

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("codex-app")


def test_the_refusal_fires_at_most_once():
    """The rebuilt context is a real cold context; a stale marker must not
    bounce a turn that is already correct."""
    client = _Client()
    client._pawflow_context_is_delta = True

    with pytest.raises(ColdStartRequired):
        client._cli_require_cold_context("codex-app")
    assert client._cli_require_cold_context("codex-app") is None


# -- the marker has to reach the provider -----------------------------------

def test_the_context_phase_marks_a_resume_context():
    from tasks.ai._agentctx_p2 import _PACPhase2Mixin

    class _Phase(_PACPhase2Mixin):
        pass

    class _St:
        client = _Client()

    st = _St()
    _Phase()._mark_context_as_delta(st)

    assert st.client._pawflow_context_is_delta is True


def test_the_marker_survives_clone_for_call():
    """The loop clones the client after the context phase (`_alc_setup`), and
    clone_for_call copies an explicit whitelist. Anything left off that list
    is a no-op in production while every same-instance unit test passes --
    which is exactly what happened to the mechanism this one replaces.
    """
    from core.llm_client import LLMClient

    client = LLMClient(provider="codex-app-server", config={})
    client._pawflow_context_is_delta = True

    assert getattr(client.clone_for_call(),
                   "_pawflow_context_is_delta", False) is True


def test_a_cold_context_is_not_marked():
    from core.llm_client import LLMClient

    client = LLMClient(provider="codex-app-server", config={})

    assert getattr(client.clone_for_call(),
                   "_pawflow_context_is_delta", False) is False


# -- the refusal must not be swallowed by the retry driver ------------------

def test_the_stream_driver_does_not_retry_a_cold_start():
    """The driver retries almost everything. Retrying here would re-send the
    same delta to the same launch, max_retries times, and then wrap it in an
    LLMClientError the loop cannot recognise."""
    from pathlib import Path

    src = Path("core/_llm_client_driver.py").read_text(encoding="utf-8")
    assert "isinstance(e, (_AC, CCCompactDetected, ColdStartRequired))" in src


# -- both launch sites ask ---------------------------------------------------

@pytest.mark.parametrize("path", [
    "core/llm_providers/_codex_app_stream.py",
    "core/llm_providers/_gemini_stream.py",
])
def test_every_cli_provider_asks_before_launching(path):
    from pathlib import Path

    assert "_cli_require_cold_context(" in Path(path).read_text(encoding="utf-8")


# -- the rebuild is the ordinary cold path ----------------------------------

def test_force_cold_skips_the_live_probe():
    """force_cold is a third CALLER, not a third state: the turn already knows
    it is going to launch, so asking again could only answer 'warm' and strip
    the context that launch needs."""
    from pathlib import Path

    src = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
    assert 'and not getattr(st, "force_cold", False)' in src


def test_the_marker_never_outlives_its_turn():
    """The base client comes from the service registry and can outlive the
    turn. A marker left set by a resume would bounce the next cold turn."""
    from pathlib import Path

    src = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
    assert "st.client._pawflow_context_is_delta = False" in src


def test_the_turn_rebuilds_at_most_once():
    """Twice means the process dies as fast as we start it; a third attempt
    would only spin."""
    from pathlib import Path

    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("except ColdStartRequired:")[1].split("except Exception")[0]
    assert '_cold_restart_done' in body
    assert 'force_cold=True' in body
    assert 'raise' in body


def test_the_rebuild_carries_the_cancel_checkpoint():
    """The checkpoint is consumed on injection and is NOT cold-gated, so the
    first pass eats it. Without carrying it, a rebuilt turn silently loses
    its 'continue where you left off' instruction."""
    from pathlib import Path

    p2 = Path("tasks/ai/_agentctx_p2.py").read_text(encoding="utf-8")
    turn = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    assert 'getattr(st, "resume_checkpoint", None)' in p2
    assert "if not st._cp_carried:" in p2
    assert 'resume_checkpoint=st.ctx.get("_consumed_cancel_checkpoint")' in turn
