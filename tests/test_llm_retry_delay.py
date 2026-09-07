"""LLM retries retain their attempt budget with bounded, cancellable waits."""

import time
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from core._llm_types import LLMCallError, LLMClientError
from core.llm_client import LLMClient, LLMMessage, LLMResponse
from core.llm_failure_classifier import classify_http_error
from tasks.ai.agent_exceptions import AgentCancelled


@pytest.fixture(params=["complete", "complete_stream"])
def case(monkeypatch, request):
    method = request.param
    client = LLMClient("openai", config={
        "api_key": "test-key", "default_model": "test-model",
        "max_retries": 3, "fallback_model": "fallback-model",
    })
    dispatch = Mock()
    monkeypatch.setattr(
        client, "_complete_openai" if method == "complete" else "_stream_openai",
        dispatch)
    monkeypatch.setattr(client, "_circuit_before_call", Mock())
    monkeypatch.setattr(client, "_circuit_after_failure", Mock())
    monkeypatch.setattr(client, "_circuit_after_success", Mock())
    monkeypatch.setattr(client, "_report_tokens", Mock())
    wait = Mock(return_value=False)
    real_wait = client._abort.wait
    monkeypatch.setattr(client._abort, "wait", wait)
    # Isolate the old sleeper too so the regression fails without real delays.
    monkeypatch.setattr(
        "core._llm_client_driver.time", SimpleNamespace(time=time.time, sleep=wait))
    monkeypatch.setattr(
        "core._llm_client_driver.random", SimpleNamespace(random=lambda: 0.5))
    messages = [LLMMessage("user", "hello", conversation_id="retry-test")]
    response = LLMResponse(content="ok", model="test-model", tokens_in=1, tokens_out=1)
    return SimpleNamespace(
        client=client, dispatch=dispatch, wait=wait, real_wait=real_wait,
        response=response, messages=messages,
        call=lambda: getattr(client, method)(messages))


@pytest.mark.parametrize("delay", [2, 59.9, 60, 60.001, 180, 299.9, 300, 301, 10312, 14400])
def test_retry_after_is_honored_up_to_five_minutes(case, delay):
    error = classify_http_error(
        429, headers={"Retry-After": str(delay)}, body="Too Many Requests",
        provider="openai", model="test-model")
    case.dispatch.side_effect = [error, case.response]

    assert case.call().content == "ok"

    case.wait.assert_called_once_with(min(delay, 300))
    assert case.dispatch.call_count == 2
    assert all(item.args[1] == "test-model" for item in case.dispatch.call_args_list)
    case.client._circuit_after_failure.assert_not_called()
    assert error.retryable is True
    assert error.retry_after_seconds == delay


@pytest.mark.parametrize("message", [
    "HTTP 429: Retry-After: 10312",
    "HTTP 429: Please try again in 10312s.",
])
def test_long_text_delay_is_capped_and_retried(case, message):
    case.dispatch.side_effect = [LLMClientError(message), case.response]

    assert case.call().content == "ok"

    case.wait.assert_called_once_with(300)
    assert case.dispatch.call_count == 2


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("max_retries", [1, 3])
def test_long_delay_exhausts_primary_budget_then_uses_fallback(case, status, max_retries):
    case.client._config_ref["max_retries"] = max_retries
    error = classify_http_error(status, headers={"Retry-After": "14400"})
    case.dispatch.side_effect = [error] * max_retries + [case.response]

    assert case.call().content == "ok"

    assert [item.args[1] for item in case.dispatch.call_args_list] == (
        ["test-model"] * max_retries + ["fallback-model"])
    assert case.wait.call_args_list == [call(300)] * (max_retries - 1)
    case.client._circuit_after_failure.assert_called_once()


def test_final_attempt_does_not_calculate_or_wait_for_a_retry(case, monkeypatch):
    case.client._config_ref["max_retries"] = 1
    delay = Mock(side_effect=AssertionError("No retry remains"))
    monkeypatch.setattr(case.client, "_retry_delay", delay)
    case.dispatch.side_effect = [
        classify_http_error(429, headers={"Retry-After": "14400"}),
        case.response,
    ]

    assert case.call().content == "ok"

    delay.assert_not_called()
    case.wait.assert_not_called()
    assert [item.args[1] for item in case.dispatch.call_args_list] == [
        "test-model", "fallback-model"]


def test_long_delay_can_recover_on_last_primary_attempt(case):
    error = classify_http_error(429, headers={"Retry-After": "14400"})
    case.dispatch.side_effect = [error, error, case.response]

    assert case.call().content == "ok"

    assert case.wait.call_args_list == [call(300), call(300)]
    assert [item.args[1] for item in case.dispatch.call_args_list] == ["test-model"] * 3
    case.client._circuit_after_failure.assert_not_called()


def test_long_delay_without_fallback_raises_only_after_all_attempts(case):
    case.client._config_ref["fallback_model"] = ""
    error = classify_http_error(429, headers={"Retry-After": "14400"})
    case.dispatch.side_effect = error

    with pytest.raises(LLMClientError):
        case.call()

    assert case.dispatch.call_count == 3
    assert case.wait.call_args_list == [call(300), call(300)]
    case.client._circuit_after_failure.assert_called_once()
    assert error.retryable is True
    assert error.retry_after_seconds == 14400


def test_missing_retry_after_uses_short_backoff(case):
    case.dispatch.side_effect = [
        classify_http_error(429, body="Too Many Requests"),
        case.response,
    ]

    assert case.call().content == "ok"

    case.wait.assert_called_once_with(2.0)


def test_exponential_backoff_is_capped_without_losing_attempts(case):
    case.client._config_ref["max_retries"] = 12
    case.dispatch.side_effect = [classify_http_error(429)] * 11 + [case.response]

    assert case.call().content == "ok"

    assert [item.args[0] for item in case.wait.call_args_list] == [
        2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
    assert case.dispatch.call_count == 12
    case.client._circuit_after_failure.assert_not_called()


def test_non_retryable_error_is_not_retried_even_with_long_delay(case):
    case.dispatch.side_effect = LLMCallError(
        "Rejected request", category="invalid_request", retryable=False,
        retry_after_seconds=14400)

    with pytest.raises(LLMCallError):
        case.call()

    case.wait.assert_not_called()
    assert case.dispatch.call_count == 1


def test_abort_interrupts_wait_without_retry_or_fallback(case, monkeypatch):
    entered_wait = Event()
    errors = []

    def wait_for_abort(timeout):
        assert timeout == 300
        entered_wait.set()
        return case.real_wait(timeout)

    monkeypatch.setattr(case.client._abort, "wait", wait_for_abort)
    case.dispatch.side_effect = classify_http_error(
        429, headers={"Retry-After": "14400"})

    def run():
        try:
            case.call()
        except Exception as exc:
            errors.append(exc)

    worker = Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered_wait.wait(timeout=1), "Retry did not enter its cancellable wait"
        # This is the signal set by LLMClient.abort().
        case.client._abort.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], AgentCancelled)
        assert case.dispatch.call_count == 1
        case.client._circuit_after_failure.assert_not_called()
    finally:
        case.client._abort.set()
        worker.join(timeout=1)

    case.client.reset_abort()
    case.dispatch.side_effect = [case.response]
    assert case.call().content == "ok"


def test_retry_wait_mock_does_not_capture_other_threads(case):
    worker = Thread(target=time.sleep, args=(0.05,), daemon=True)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    case.wait.assert_not_called()

    case.dispatch.side_effect = [classify_http_error(429), case.response]
    assert case.call().content == "ok"
    case.wait.assert_called_once_with(2.0)
