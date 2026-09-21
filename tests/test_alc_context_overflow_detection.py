"""An oversized prompt must reach the compact-and-retry path, not a fatal error.

Observed 2026-09-20 on OpenCode Go serving deepseek-flash: once the endpoint
required reasoning_content back, the replayed reasoning took the prompt to
1,434,594 tokens against a 1,048,576 window. The 400 said "maximum context
length ... you requested N tokens", which classify_http_error types as
context_overflow but which none of the literal markers matched, so every later
turn of the agent failed fatally instead of compacting.
"""

from core._llm_types import LLMCallError
from core.llm_failure_classifier import classify_http_error
from tasks.ai._alc_llm_turn import is_context_overflow_error

CONSOLE_GO_OVERFLOW = (
    '{"error":{"param":null,"type":"invalid_request_error",'
    '"code":"invalid_request_error","message":"Error from provider '
    '(Console Go): Upstream request failed: [invalid_request_error] This '
    "model's maximum context length is 1048576 tokens. However, you "
    'requested 1444806 tokens (1444806 in the messages, 0 in the '
    'completion)."}}'
)


def test_typed_overflow_from_the_real_body_is_detected():
    err = classify_http_error(400, body=CONSOLE_GO_OVERFLOW,
                              provider="openai", model="deepseek-flash")

    assert err.category == "context_overflow"
    assert is_context_overflow_error(err)


def test_typed_category_wins_over_the_wording():
    err = LLMCallError("window exhausted", category="context_overflow")

    assert is_context_overflow_error(err)


def test_untyped_provider_wordings_are_still_detected():
    for text in ("exceed_context_size", "n_prompt_tokens=9",
                 "Prompt is too long: 210000 tokens > 200000 maximum",
                 "prompt_too_long"):
        assert is_context_overflow_error(RuntimeError(text))


def test_other_invalid_requests_are_not_overflows():
    err = classify_http_error(
        400, body='{"error":{"message":"invalid api key"}}',
        provider="openai", model="deepseek-flash")

    assert not is_context_overflow_error(err)
    assert not is_context_overflow_error(RuntimeError("LLM API error 500"))
