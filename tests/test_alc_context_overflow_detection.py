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


def test_provider_overflow_forces_real_compaction_below_auto_threshold(
        monkeypatch, tmp_path):
    """A native refusal overrides our estimate and the proactive threshold."""
    from types import SimpleNamespace
    from unittest.mock import Mock

    from core.llm_client import LLMMessage, LLMResponse
    from tasks.ai.agent_loop import AgentLoopTask

    store = Mock()
    store._conv_dir.return_value = str(tmp_path)
    store.get_extra.return_value = {}
    store.message_count.return_value = 40
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance",
                        lambda: store)
    monkeypatch.setattr("core.bucket_store.BucketStore.get", lambda _path:
                        SimpleNamespace(assemble_summary_header=lambda: "",
                                        last_seq=0))
    monkeypatch.setattr("core.agent_hooks.AgentHookRunner.run",
                        lambda *_args, **_kwargs: {})
    loop = AgentLoopTask({"api_key": "test"})
    monkeypatch.setattr(loop, "_cleanup_orphan_files", Mock())
    cfg = {"max_context_size": 800_000, "compact_target_tokens": 25_000,
           "compact_threshold_pct": 95}
    messages = [
        LLMMessage(role="system", content="Keep the required instructions.",
                   conversation_id="overflow-conv"),
        *[LLMMessage(role="user", content="history details " * 1000,
                     conversation_id="overflow-conv") for _ in range(40)],
    ]
    before = loop._estimate_tokens(messages)
    assert 25_000 < before < 640_000
    error = LLMCallError(
        "maximum context length is 1048576 tokens; requested 393216 output "
        "tokens and prompt contains at least 655361 input tokens",
        category="context_overflow", provider_status=400, retryable=False)
    response = LLMResponse(content="continued")
    sent_sizes = []

    def provider_call(prompt):
        sent_sizes.append(loop._estimate_tokens(prompt))
        if len(sent_sizes) == 1 or sent_sizes[-1] > 25_000:
            raise error
        return response

    st = SimpleNamespace(
        ctx={"active_agent_name": "GameDev", "max_context_size": 800_000,
             "resolved_svc": SimpleNamespace(config=cfg)},
        messages=messages, llm_context=list(messages),
        _call_context=list(messages), _llm_call=provider_call,
        _budget_precheck_done=True, _is_claude_code=False,
        client=SimpleNamespace(provider="openai"),
        compact_client=SimpleNamespace(config=cfg),
        conversation_id="overflow-conv", user_id="test", iteration=1,
        total_tokens_in=0, total_tokens_out=0,
        total_cache_read=0, total_cache_write=0,
        emitter=Mock(), _fatal_error=False, _fatal_error_msg="",
    )

    def adopt(compacted, **_kwargs):
        st.messages[:] = compacted

    st._adopt_compacted_context = adopt

    result = loop._alc_llm_turn(st)

    assert result is None
    assert st.response is response
    assert not st._fatal_error
    assert len(sent_sizes) == 2
    assert sent_sizes[0] == before
    assert sent_sizes[1] <= 25_000
    assert st.messages[0].content == "Keep the required instructions."
    store.save_agent_context.assert_called_once()
    st.emitter.on_fatal_error.assert_not_called()
