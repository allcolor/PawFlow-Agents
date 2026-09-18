"""A circuit breaker must report the failure that opened it, then wait it out.

Regression: an ollama/zai 429 ("you have reached your session usage limit")
opened the circuit after the driver's own retries, and the agent-level retry was
then rejected instantly with "LLM circuit open ... retry in 55s". The user only
ever saw the circuit message, never the provider 429 that explained it.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core._llm_types import LLMClientError
from core.llm_client import LLMClient, LLMMessage, LLMResponse


PROVIDER_429 = ('LLM API error 429: {"error":{"message":'
                '"you (bob) have reached your session usage limit"}}')


def _client(**overrides):
    config = {
        "api_key": "test-key",
        "default_model": "test-model",
        "max_retries": 2,
        "circuit_breaker_failures": 1,
        "circuit_breaker_cooldown": 60,
    }
    config.update(overrides)
    LLMClient._circuit_state.clear()
    return LLMClient("openai", config=config)


@pytest.fixture(params=["complete", "complete_stream"])
def case(monkeypatch, request):
    method = request.param
    client = _client()
    dispatch = Mock()
    monkeypatch.setattr(
        client, "_complete_openai" if method == "complete" else "_stream_openai", dispatch)
    monkeypatch.setattr(client, "_report_tokens", Mock())
    messages = [LLMMessage("user", "hello", conversation_id="circuit-test")]
    response = LLMResponse(content="ok", model="test-model", tokens_in=1, tokens_out=1)
    return SimpleNamespace(
        client=client, dispatch=dispatch, response=response, messages=messages,
        call=lambda: getattr(client, method)(messages))


def _expire_circuit_on_wait(monkeypatch, client):
    """Make the abort-aware cooldown wait look like real elapsed time."""
    def _advance(_delay):
        for state in LLMClient._circuit_state.values():
            state["open_until"] = 0.0
        return False

    wait = Mock(side_effect=_advance)
    monkeypatch.setattr(client._abort, "wait", wait)
    return wait


def test_open_circuit_reports_the_provider_failure_that_opened_it():
    client = _client()
    client._circuit_after_failure("test-model", PROVIDER_429)

    with pytest.raises(LLMClientError) as exc:
        client._circuit_before_call("test-model")

    message = str(exc.value)
    assert "circuit open" in message
    assert "session usage limit" in message
    assert "retry in" in message


def test_circuit_cause_is_sanitized_and_bounded():
    client = _client()
    client._circuit_after_failure(
        "test-model",
        "HTTP 429 Authorization: Bearer sk-secret-value https://ollama.com/v1/chat "
        + "x" * 5000)

    with pytest.raises(LLMClientError) as exc:
        client._circuit_before_call("test-model")

    message = str(exc.value)
    assert "sk-secret-value" not in message
    assert len(message) < 500


def test_circuit_rejection_is_not_counted_as_a_provider_failure():
    client = _client()
    key = client._circuit_key("test-model")
    client._circuit_after_failure("test-model", "LLM API error 429: rate_limit reached")
    assert LLMClient._circuit_state[key]["failures"] == 1

    client._circuit_after_failure(
        "test-model",
        "LLM circuit open for openai/test-model; retry in 55s; last error: "
        "LLM API error 429: rate_limit reached")

    state = LLMClient._circuit_state[key]
    assert state["failures"] == 1
    assert "429" in state["last_error"]


def test_turn_waits_out_the_circuit_cooldown_then_succeeds(monkeypatch, case):
    case.dispatch.return_value = case.response
    case.client._circuit_after_failure("test-model", PROVIDER_429)
    wait = _expire_circuit_on_wait(monkeypatch, case.client)

    assert case.call().content == "ok"

    assert case.dispatch.call_count == 1
    assert wait.call_count == 1
    assert 60.0 <= wait.call_args[0][0] <= 63.0
    assert case.client._circuit_state == {}


def test_turn_without_retry_budget_still_reports_the_provider_cause(case):
    case.client._config_ref["max_retries"] = 1
    case.client._circuit_after_failure("test-model", PROVIDER_429)

    with pytest.raises(LLMClientError) as exc:
        case.call()

    assert "session usage limit" in str(exc.value)


@pytest.mark.parametrize(("text", "expected"), [
    ("LLM circuit open for openai/m; retry in 55s", 55.5),
    ("LLM circuit open for openai/m; retry in 55s; last error: nope", 55.5),
    ("LLM circuit open for openai/m; retry in 4000s", 300.0),
    ("HTTP 429 rate limited", 0.0),
    ("", 0.0),
])
def test_circuit_cooldown_seconds_parses_and_caps(text, expected):
    assert LLMClient._circuit_cooldown_seconds(text) == pytest.approx(expected)
