"""The API-provider context gauge counts the full PawFlow provider context.

Regression: the gauge and the injected "Context: ~x/y" note disagreed with
each other and with the persisted snapshot (1% vs 8% vs 12% for the same
conversation). For API providers the provider context PawFlow sends is
messages + provider system prompt + tool definitions; every consumer must
derive its number from the same counter so the UI, the LLM-visible note and
the compactor can never disagree.
"""

from core.llm_client import LLMMessage
from core.token_counter import count_context_tokens
from tasks.ai.context_usage import context_usage_for_messages


def _msg(role, text, mid):
    return LLMMessage(role=role, content=text, conversation_id="c1",
                      msg_id=mid)


def _tool_def(name, desc, params):
    return type("TD", (), {"name": name, "description": desc,
                            "parameters": params})()


def test_context_usage_for_messages_includes_api_overhead():
    """used = messages + system prompt + tool defs, not messages alone."""
    messages = [_msg("user", "hello world", "m1")]
    usage = context_usage_for_messages(
        "c1", "assistant", messages,
        svc_cfg={"max_context_size": 10000},
        api_overhead=500, source="test")
    assert usage["overhead_tokens"] == 500
    assert usage["used"] > 500
    assert usage["pct"] > 0.05


def test_api_overhead_matches_the_injected_context_note():
    """The gauge and the "Context: ~x/y" note must be the same number.

    The note counts provider_context (system prompt in head position) +
    tool defs. The gauge counts the same messages + the system prompt as
    overhead + the same tool defs. The only allowed difference is the
    per-message overhead (4 tokens) of the system message itself.
    """
    system = "You are a helpful agent." * 20
    messages = [
        _msg("system", system, "sys1"),
        _msg("user", "Tell me about the gauge bug.", "m1"),
        _msg("assistant", "Here is a long explanation of the context gauge "
             "and how tokens are counted for API providers.", "m2"),
    ]
    tool_defs = [_tool_def(
        "read", "Read a file from the filesystem",
        {"type": "object", "properties": {"path": {"type": "string"}}})]

    note_estimate = count_context_tokens(
        messages, tool_defs=tool_defs, multiplier=1.0)
    gauge_used = context_usage_for_messages(
        "c1", "assistant", messages[1:],
        svc_cfg={"max_context_size": 100000},
        api_overhead=count_context_tokens(
            [], system_prompt=system, tool_defs=tool_defs,
            multiplier=1.0),
        source="test")["used"]

    assert abs(note_estimate - gauge_used) <= 8


def test_count_context_tokens_is_the_single_counter():
    """Messages + system prompt + tool defs, scaled by the multiplier."""
    messages = [_msg("user", "hi there", "m1")]
    plain = count_context_tokens(messages, multiplier=1.0)
    with_system = count_context_tokens(
        messages, system_prompt="sys prompt", multiplier=1.0)
    with_tools = count_context_tokens(
        messages, tool_defs=[_tool_def("t", "d", {})], multiplier=1.0)
    assert with_system > plain
    assert with_tools > plain
    doubled = count_context_tokens(
        messages, system_prompt="sys prompt", multiplier=2.0)
    assert doubled >= with_system * 2
