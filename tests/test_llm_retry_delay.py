"""LLM retries must not keep a foreground agent asleep for hours."""

import time
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core._llm_types import LLMCallError, LLMClientError
from core.llm_client import LLMClient, LLMMessage, LLMResponse
from core.llm_failure_classifier import classify_http_error


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
    sleep = Mock()
    # Replace driver bindings, not stdlib modules shared by background threads.
    monkeypatch.setattr(
        "core._llm_client_driver.time", SimpleNamespace(time=time.time, sleep=sleep))
    monkeypatch.setattr(
        "core._llm_client_driver.random", SimpleNamespace(random=lambda: 0.5))
    messages = [LLMMessage("user", "hello", conversation_id="retry-test")]
    response = LLMResponse(content="ok", model="test-model", tokens_in=1, tokens_out=1)
    return SimpleNamespace(
        client=client, dispatch=dispatch, sleep=sleep, response=response,
        messages=messages, call=lambda: getattr(client, method)(messages))


@pytest.mark.parametrize("delay", [60.001, 10312])
@pytest.mark.parametrize("max_retries", [1, 3])
def test_long_retry_after_fails_without_sleep_or_fallback(case, delay, max_retries):
    case.client._config_ref["max_retries"] = max_retries
    error = classify_http_error(
        429, headers={"Retry-After": str(delay)}, body="Too Many Requests",
        provider="openai", model="test-model")
    case.dispatch.side_effect = [error, case.response]

    with pytest.raises(LLMCallError, match="60") as caught:
        case.call()

    assert caught.value.retryable is False
    assert caught.value.category == "rate_limited"
    assert caught.value.provider_status == 429
    assert caught.value.retry_after_seconds == delay
    assert caught.value.provider == "openai"
    assert caught.value.model == "test-model"
    assert str(delay) in str(caught.value)
    case.sleep.assert_not_called()
    assert case.dispatch.call_count == 1
    case.client._circuit_after_failure.assert_called_once()


@pytest.mark.parametrize("message", [
    "HTTP 429: Retry-After: 10312",
    "HTTP 429: Please try again in 10312s.",
])
def test_long_text_delay_also_stops_retries(case, message):
    case.dispatch.side_effect = [LLMClientError(message), case.response]

    with pytest.raises(LLMCallError, match="60") as caught:
        case.call()

    assert caught.value.retryable is False
    assert caught.value.retry_after_seconds > 10312
    case.sleep.assert_not_called()
    assert case.dispatch.call_count == 1


@pytest.mark.parametrize("delay", [2, 59.9, 60])
def test_retry_after_up_to_one_minute_is_honored(case, delay):
    error = classify_http_error(429, headers={"Retry-After": str(delay)})
    case.dispatch.side_effect = [error, case.response]

    assert case.call().content == "ok"

    case.sleep.assert_called_once_with(delay)
    assert case.dispatch.call_count == 2


def test_retry_sleep_does_not_capture_other_threads(case):
    worker = Thread(target=time.sleep, args=(0.05,), daemon=True)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()

    case.dispatch.side_effect = [classify_http_error(429), case.response]

    assert case.call().content == "ok"
    case.sleep.assert_called_once_with(2.0)
    assert case.dispatch.call_count == 2


def test_missing_retry_after_uses_short_backoff(case):
    case.dispatch.side_effect = [
        classify_http_error(429, body="Too Many Requests"),
        case.response,
    ]

    assert case.call().content == "ok"

    case.sleep.assert_called_once_with(2.0)
    assert case.dispatch.call_count == 2


def test_exponential_backoff_cannot_exceed_one_minute(case):
    case.client._config_ref["max_retries"] = 10
    case.dispatch.side_effect = classify_http_error(429)

    with pytest.raises(LLMCallError, match="60"):
        case.call()

    assert [call.args[0] for call in case.sleep.call_args_list] == [
        2, 4, 8, 16, 32]
    assert case.dispatch.call_count == 6


def test_provider_unavailable_cannot_request_hours_of_sleep(case):
    case.dispatch.side_effect = [
        classify_http_error(503, headers={"Retry-After": "10312"}),
        case.response,
    ]

    with pytest.raises(LLMCallError) as caught:
        case.call()

    assert caught.value.provider_status == 503
    assert caught.value.category == "provider_unavailable"
    assert caught.value.retryable is False
    case.sleep.assert_not_called()
    assert case.dispatch.call_count == 1


@pytest.mark.parametrize("max_retries", [1, 3])
def test_agent_surfaces_long_delay_without_outer_retry_and_releases_context(
        case, monkeypatch, max_retries):
    from tasks.ai._alc_base import _ALC_BREAK
    from tasks.ai.agent_loop import AgentLoopTask

    case.client._config_ref["max_retries"] = max_retries
    task = AgentLoopTask({"api_key": "test-key"})
    context = {"conversation_id": "retry-test", "active_agent_name": "assistant"}
    emitter = Mock()
    emitter.check_interrupt.return_value = False
    state = SimpleNamespace(
        ctx=context, emitter=emitter, client=case.client,
        conversation_id="retry-test", _budget_precheck_done=True,
        total_tokens_in=0, total_tokens_out=0, total_cache_read=0,
        total_cache_write=0, _call_context=case.messages,
        llm_context=case.messages, _llm_call=lambda _: case.call(),
        _is_claude_code=False, _fatal_error=False, _fatal_error_msg="",
        iteration=1)
    case.dispatch.side_effect = [
        classify_http_error(429, headers={"Retry-After": "10312"}),
        case.response,
    ]
    monkeypatch.setattr("tasks.ai._alc_llm_turn._check_budget", lambda *_: None)

    def run_inner(ctx, actual_emitter):
        assert task._active_contexts["retry-test:assistant"] is ctx
        return task._alc_llm_turn(state)

    monkeypatch.setattr(task, "_run_agent_loop_inner", run_inner)

    assert task._run_agent_loop(context, emitter) is _ALC_BREAK
    assert state._fatal_error is True
    assert "10312" in state._fatal_error_msg and "60" in state._fatal_error_msg
    emitter.on_fatal_error.assert_called_once()
    assert "_agent_transient_retried" not in context
    case.sleep.assert_not_called()
    assert case.dispatch.call_count == 1
    assert "retry-test:assistant" not in task._active_contexts
