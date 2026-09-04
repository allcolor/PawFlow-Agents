"""An interactive CLI turn that fails is surfaced, never re-run.

Regression for the rate-limit hang: a Claude Code ``StopFailure`` (an
upstream ``429`` usage limit on a subscription backend) raised a plain
RuntimeError, the generic driver retry loop matched "429" and called the
provider again on the same live session. That either tripped the cold/delta
context guard (``DeltaContextRequired``, fatal for sub-agents) or pasted the
prompt into the tmux a second time and waited for proxy events that never
came -- the webchat showed the agent working for minutes after the CLI had
already printed the error.
"""

import pytest

from core._llm_types import (
    INTERACTIVE_CLI_PROVIDERS,
    NO_REPLAY_PROVIDERS,
    DeltaContextRequired,
    LLMCallError,
)
from core.llm_client import LLMClient, LLMMessage
from core.llm_providers._cci_turn import _CCITurnCoordinator


class _StopFailureService:
    def __init__(self, error):
        self._events = [{
            "type": "hook", "hook_event_name": "StopFailure",
            "input": {"hook_event_name": "StopFailure", "error": error},
        }]

    def wait_event(self, session_token, timeout=None):
        return self._events.pop(0) if self._events else {}


def test_stop_failure_is_a_non_retryable_rate_limit_error():
    error = ("API Error: Request rejected (429) · [1308][Usage limit reached "
             "for 5 hour. Your limit will reset at 2026-08-21 17:47:27]")
    with pytest.raises(LLMCallError) as info:
        _CCITurnCoordinator(_StopFailureService(error), "sess").run()
    assert info.value.retryable is False
    assert info.value.category == "rate_limited"
    assert info.value.provider == "claude-code-interactive"
    # The CLI's own message reaches the user, not a generic placeholder.
    assert error in str(info.value)


def test_stop_failure_without_rate_limit_is_still_terminal():
    with pytest.raises(LLMCallError) as info:
        _CCITurnCoordinator(_StopFailureService("boom"), "sess").run()
    assert info.value.retryable is False
    assert info.value.category == "unknown"
    assert "boom" in str(info.value)


def test_interactive_cli_provider_set_covers_every_tmux_provider():
    assert INTERACTIVE_CLI_PROVIDERS == {
        "claude-code-interactive", "codex-interactive",
        "antigravity-interactive",
        # Managed MCP providers reuse the same tmux pools.
        "cc_mcp", "codex_mcp", "agy_mcp"}
    assert NO_REPLAY_PROVIDERS == INTERACTIVE_CLI_PROVIDERS | {"acp", "antigravity-acp"}


def _failing_client(monkeypatch, provider, exc, calls):
    client = LLMClient(provider)

    def _boom(*_args, **_kwargs):
        calls.append(provider)
        raise exc

    # The driver dispatches on ``_stream_<provider>``; stub exactly that one
    # so the count below proves it was entered once and never again.
    monkeypatch.setattr(client, f"_stream_{provider.replace('-', '_')}",
                        _boom, raising=False)
    # Never sleep in the retry loop even if a regression re-enables it.
    monkeypatch.setattr("core._llm_client_driver.time.sleep", lambda *_: None)
    return client


@pytest.mark.parametrize("provider", sorted(INTERACTIVE_CLI_PROVIDERS))
def test_driver_never_reruns_an_interactive_cli_turn_on_429(monkeypatch, provider):
    calls = []
    exc = RuntimeError("API Error: Request rejected (429) rate_limit")
    client = _failing_client(monkeypatch, provider, exc, calls)
    with pytest.raises(RuntimeError, match="429"):
        client.complete_stream([LLMMessage(role="user", content="hi", conversation_id="conv")])
    assert calls == [provider]


def test_driver_never_reruns_codex_interactive_on_404(monkeypatch):
    calls = []
    client = _failing_client(
        monkeypatch, "codex-interactive",
        RuntimeError("Provider returned HTTP 404: Not Found"), calls)

    with pytest.raises(RuntimeError, match="404"):
        client.complete_stream([
            LLMMessage(role="user", content="hi", conversation_id="conv")])

    assert calls == ["codex-interactive"]


def test_driver_passes_stop_failure_call_error_through_unchanged(monkeypatch):
    calls = []
    exc = LLMCallError("Claude Code interactive turn failed: 429",
                       category="rate_limited", retryable=False,
                       provider="claude-code-interactive")
    client = _failing_client(monkeypatch, "claude-code-interactive", exc, calls)
    with pytest.raises(LLMCallError) as info:
        client.complete_stream([LLMMessage(role="user", content="hi", conversation_id="conv")])
    assert info.value is exc
    assert calls == ["claude-code-interactive"]


def test_driver_complete_never_reruns_an_interactive_cli_turn(monkeypatch):
    calls = []
    client = LLMClient("claude-code-interactive")

    def _boom(*_args, **_kwargs):
        calls.append("complete")
        raise RuntimeError("Internal server error 500")

    # complete() dispatches the interactive provider through its stream path.
    monkeypatch.setattr(client, "_stream_claude_code_interactive", _boom,
                        raising=False)
    monkeypatch.setattr("core._llm_client_driver.time.sleep", lambda *_: None)
    with pytest.raises(RuntimeError, match="500"):
        client.complete([LLMMessage(role="user", content="hi", conversation_id="conv")])
    assert calls == ["complete"]


def test_delta_context_required_still_reaches_the_agent_loop(monkeypatch):
    # Control-flow exceptions keep their type so _alc_llm_turn can rebuild the
    # context; the interactive short-circuit must not wrap them.
    calls = []
    exc = DeltaContextRequired("claude-code-interactive: delta required")
    client = _failing_client(monkeypatch, "claude-code-interactive", exc, calls)
    with pytest.raises(DeltaContextRequired):
        client.complete_stream([LLMMessage(role="user", content="hi", conversation_id="conv")])
    assert calls == ["claude-code-interactive"]


def test_agent_loop_skips_transient_retry_for_interactive_cli_providers():
    # Source-level invariant (same style as test_two_cases_only): the agent
    # level "transient error, retry once" branch must not re-paste a prompt
    # into a live CLI session.
    with open("tasks/ai/_alc_llm_turn.py", encoding="utf-8") as handle:
        src = handle.read()
    assert "NO_REPLAY_PROVIDERS" in src
    assert ("if (st._transient and not st._no_replay_provider\n"
            "                        and not st.ctx.get(\"_agent_transient_retried\")):"
            ) in src
