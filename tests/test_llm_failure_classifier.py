from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from core._llm_types import LLMCallError
from core.llm_failure_classifier import (
    classify_cli_error,
    classify_http_error,
    parse_retry_after,
    safe_error_message,
)
from tasks.ai.agent_exceptions import AgentCancelled


@pytest.mark.parametrize("status,body,category,retryable", [
    (401, "invalid", "auth_invalid", False),
    (403, "forbidden", "auth_invalid", False),
    (402, "balance", "billing_exhausted", False),
    (404, "model", "model_unavailable", False),
    (408, "timeout", "upstream_timeout", True),
    (429, "rate", "rate_limited", True),
    (429, "quota exhausted", "quota_exhausted", True),
    (503, "down", "provider_unavailable", True),
    (400, "context length exceeded", "context_overflow", False),
    (400, "bad input", "caller_invalid", False),
])
def test_http_status_classification(status, body, category, retryable):
    error = classify_http_error(
        status, body=body, provider="openai", model="m")
    assert isinstance(error, LLMCallError)
    assert error.category == category
    assert error.retryable is retryable
    assert error.provider_status == status
    assert error.provider == "openai"


def test_retry_after_supports_seconds_and_http_date():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    future = datetime.fromtimestamp(now + 60, tz=timezone.utc)
    assert parse_retry_after("12.5", now=now) == 12.5
    assert parse_retry_after(format_datetime(future), now=now) == 60
    assert parse_retry_after("not-a-date", now=now) == 0
    error = classify_http_error(
        429, headers={"Retry-After": "8"}, body="slow")
    assert error.retry_after_seconds == 8


def test_local_timeout_is_not_provider_health():
    error = classify_http_error(408, body="deadline", local_timeout=True)
    assert error.category == "local_timeout"
    assert error.origin == "local"
    assert error.caused_by_local_timeout is True


def test_cli_contract_never_fabricates_http_metadata_or_locks_ambiguous_text():
    known = classify_cli_error(
        "codex-interactive", RuntimeError("rate_limit_exceeded"))
    ambiguous = classify_cli_error(
        "codex-interactive", RuntimeError("something mentioned 429123"))
    transport = classify_cli_error(
        "claude-code-interactive", BrokenPipeError("pipe"), signal="pipe")
    assert known.category == "rate_limited"
    assert ambiguous.category == "unknown"
    assert transport.category == "network"
    for error in (known, ambiguous, transport):
        assert error.provider_status == 0
        assert error.retry_after_seconds == 0
        assert error.category != "locked"


def test_cli_control_flow_is_re_raised():
    cancelled = AgentCancelled()
    with pytest.raises(AgentCancelled):
        classify_cli_error("gemini", cancelled)


def test_safe_message_redacts_secrets_urls_and_bounds_length():
    value = safe_error_message(
        "Authorization: Bearer abc API_KEY=xyz "
        "https://user:pass@example.test/path?token=secret " + "x" * 500)
    assert "abc" not in value
    assert "xyz" not in value
    assert "pass" not in value
    assert "token=secret" not in value
    assert len(value) <= 300
