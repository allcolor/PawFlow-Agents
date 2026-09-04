"""The LLM-visible "Context: ~x/y" note must be the gauge's number.

Regression, observed live on conversation 80c37670429e4f56 running
claude-code-interactive: the note announced ``~864/800000`` while the gauge
and the auto-compact check measured ``77965``. One restart earlier the same
conversation announced ``~602307``. Nothing about the context had changed by
a factor of 90 -- the note had silently switched quantity.

The cause is structural and applies to every CLI provider.
``_alc_with_provider_system_prompt`` returns the stored messages untouched
when ``_is_cli_provider and _cli_has_session``: on a warm session the CLI
already holds the system prompt and the history, so ``provider_context`` is
only the turn's delta. Counting it measures what PawFlow hands over this turn,
not how full the window is.

API providers are the opposite case and must keep counting provider_context:
PawFlow resends the whole context on every call, so it *is* the occupancy.
"""

import re

import pytest

from core.llm_client import LLMMessage


NOTE_RE = re.compile(r"Context: ~(\d+)/(\d+) tokens \(~(\d+) remaining\)")


class _State:
    def __init__(self, *, is_cli, max_ctx=800000, conversation_id="conv-1"):
        self.ctx = {
            "_datetime_str": "2026-08-07 22:00:00",
            "_dynamic_blocks": [],
            "chars_per_token": 4,
            "active_agent_name": "claude",
        }
        if is_cli:
            self.ctx["_is_cli_provider"] = True
        self._max_ctx = max_ctx
        self.tool_defs = []
        self.conversation_id = conversation_id
        self.user_id = "u1"


def _inject(st, messages):
    from tasks.ai.agent_loop import AgentLoopTask
    task = AgentLoopTask.__new__(AgentLoopTask)
    task._estimate_tokens = lambda *a, **k: 0
    return task._alc_inject_dynamic_metadata(st, list(messages))


def _note(messages):
    text = messages[-1].content
    match = NOTE_RE.search(text if isinstance(text, str) else str(text))
    assert match, f"no context note injected in {text!r}"
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _turn_delta():
    """What a warm CLI session actually hands over: one short user message."""
    return [LLMMessage(role="user", content="ok go", conversation_id="conv-1")]


@pytest.fixture
def gauge(monkeypatch):
    """Install a stub gauge and report what the note was asked for."""
    calls = []

    def _install(used, max_ctx=800000):
        import tasks.ai.context_usage as cu

        def _fake(conversation_id, agent_name, **kwargs):
            calls.append((conversation_id, agent_name, kwargs.get("source")))
            return {"used": used, "max": max_ctx,
                    "pct": (used / max_ctx) if max_ctx else 0.0}

        monkeypatch.setattr(cu, "compute_context_usage", _fake)
        return calls

    return _install


def test_warm_cli_session_reports_the_gauge_not_the_turn_delta(gauge):
    """The exact live failure: delta ~864, real occupancy 77965."""
    calls = gauge(77965)
    st = _State(is_cli=True)

    used, max_ctx, remaining = _note(_inject(st, _turn_delta()))

    assert used == 77965
    assert max_ctx == 800000
    assert remaining == 800000 - 77965
    assert calls and calls[0][2] == "context_note"


def test_the_note_divides_by_the_window_the_gauge_measured_against(gauge):
    """A provider that reports its own window overrides max_context_size.

    Codex reports a native window; dividing the measured prompt by the
    configured guess would put a correct numerator over a wrong denominator.
    """
    gauge(136000, max_ctx=272000)
    st = _State(is_cli=True, max_ctx=800000)

    used, max_ctx, remaining = _note(_inject(st, _turn_delta()))

    assert (used, max_ctx, remaining) == (136000, 272000, 136000)


def test_st_max_ctx_is_not_mutated_by_the_note(gauge):
    """The note's denominator must stay local: st._max_ctx budgets compaction."""
    gauge(136000, max_ctx=272000)
    st = _State(is_cli=True, max_ctx=800000)

    _inject(st, _turn_delta())

    assert st._max_ctx == 800000


def test_cold_cli_session_falls_back_to_counting_the_context(gauge):
    """No measurement yet: provider_context then carries the whole context.

    A cold start hands the CLI everything, so counting it is honest -- and it
    is the one case where the old code was right. Falling back rather than
    reporting the gauge's 0 keeps the note from announcing an empty window.
    """
    gauge(0)
    st = _State(is_cli=True)
    messages = [LLMMessage(role="user", content="x" * 40000,
                           conversation_id="conv-1")]

    used, max_ctx, _ = _note(_inject(st, messages))

    assert used > 1000
    assert max_ctx == 800000


def test_api_provider_still_counts_the_provider_context(gauge):
    """API path unchanged: PawFlow resends everything, so it is the occupancy."""
    calls = gauge(77965)
    st = _State(is_cli=False)
    messages = [LLMMessage(role="user", content="y" * 40000,
                           conversation_id="conv-1")]

    used, max_ctx, _ = _note(_inject(st, messages))

    assert not calls, "API providers must not consult the CLI gauge"
    assert used > 1000
    assert max_ctx == 800000


def test_a_failing_gauge_does_not_lose_the_note(gauge, monkeypatch):
    """The note is load-bearing: a gauge error must degrade, not remove it."""
    import tasks.ai.context_usage as cu

    def _boom(*a, **k):
        raise RuntimeError("gauge unavailable")

    monkeypatch.setattr(cu, "compute_context_usage", _boom)
    st = _State(is_cli=True)
    messages = [LLMMessage(role="user", content="z" * 40000,
                           conversation_id="conv-1")]

    used, _, _ = _note(_inject(st, messages))

    assert used > 1000


def test_every_cli_provider_records_a_measure_for_the_gauge():
    """The note is only as good as the measure behind it.

    Each of the six CLI providers must feed
    ``_cli_observed_context_tokens_by_stream``, directly or through
    ``record_observed_wire_usage``. A provider that records nothing sends the
    note back to the cold-start fallback on every turn.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "core" / "llm_providers"
    recorders = ("record_observed_cli_context", "record_observed_wire_usage")
    by_provider = {
        "claude-code": "_cc_stream_result.py",
        "claude-code-interactive": "claude_code_interactive.py",
        "antigravity-interactive": "antigravity_interactive.py",
        "codex-app-server": "_codex_app_stream.py",
        "codex-interactive": "codex_interactive.py",
        "gemini": "_gemini_stream.py",
    }
    for provider, filename in by_provider.items():
        src = (root / filename).read_text(encoding="utf-8")
        assert any(f"self.{name}(" in src for name in recorders), (
            f"{provider} ({filename}) records no measured context")


def test_the_cli_providers_checked_here_are_the_gauge_s_own_list():
    """Pin the list to context_usage, so a new provider cannot slip past."""
    from core.managed_mcp_spec import managed_mcp_capability_matrix
    from tasks.ai.context_usage import _CLI_CONTEXT_PROVIDERS

    managed = managed_mcp_capability_matrix()
    assert set(_CLI_CONTEXT_PROVIDERS) == {
        "acp", "antigravity-acp", "claude-code", "claude-code-interactive",
        "antigravity-interactive", "codex-app-server", "codex-interactive",
        "gemini", "cc_mcp", "codex_mcp", "agy_mcp"}
    assert managed["cc_mcp"]["context_source"] == "unavailable"
    assert managed["codex_mcp"]["context_source"] == "codex_rollout_token_count"
    assert managed["agy_mcp"]["context_source"] == "unavailable"
