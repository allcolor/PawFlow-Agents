"""The bootstrap read belongs to the transcript and the gauge, never to context.

A cold CLI start writes the serialized history to ``initial_context.md`` and the
agent reads it. That call and its result are persisted like any other work: the
transcript and the UI must show what the agent did, and a suppressed call is
indistinguishable from a lost one.

Two surfaces, two rules, and they are not the same rule:

* the **agent context** must never carry the pair. The result body IS the
  previous bootstrap file, so serializing it into the next one embeds a verbatim
  copy of the file the agent is already reading -- one layer deeper on every
  cold start.
* the **gauge** must count the result, because that body is literally what fills
  the provider's window. The serialized messages before it are what the gauge
  zeroes instead: the provider received a file path, not those messages.

Deduplicating the first must never leak into the second.
"""

import pytest

from core.llm_client import LLMClient, LLMMessage, LLMToolCall

BOOTSTRAP_BODY = "# PawFlow Initial Context\n" + ("serialized history " * 400)

# Every shape the CLI providers use to open their own bootstrap file.
BOOTSTRAP_CALLS = [
    ("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"}),
    ("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cli/initial_context.md"}),
    ("view_file", {"path": "/cc_sessions/c/a/.pawflow_ag/initial_context.md"}),
    ("read_file", {"path": r"C:\cc_sessions\c\a\.pawflow_cli\initial_context.md"}),
    ("codex_native_commandExecution",
     {"command": "sed -n '1,240p' /cc_sessions/c/a/.pawflow_cli/initial_context.md"}),
]


def _pair(tool_name, arguments, *, tc_id="boot-1", origin="native",
          body=BOOTSTRAP_BODY, text=""):
    call = LLMMessage(
        role="assistant",
        content=text,
        conversation_id="conv",
        tool_calls=[LLMToolCall(
            id=tc_id, name=tool_name, arguments=arguments, tool_origin=origin)],
    )
    result = LLMMessage(
        role="tool", content=body, conversation_id="conv", tool_call_id=tc_id)
    return [call, result]


def _client():
    return LLMClient("claude-code-interactive")


@pytest.mark.parametrize(("tool_name", "arguments"), BOOTSTRAP_CALLS)
def test_bootstrap_pair_never_reaches_the_agent_context(tool_name, arguments):
    """Neither the call nor its result is serialized back into the context."""
    messages = (
        [LLMMessage(role="user", content="earlier", conversation_id="conv")]
        + _pair(tool_name, arguments)
        + [LLMMessage(role="assistant", content="an answer", conversation_id="conv"),
           LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "# PawFlow Initial Context" not in body
    assert "serialized history" not in body
    assert "initial_context.md" not in body
    # The real conversation is untouched.
    assert "earlier" in body
    assert "an answer" in body


def test_ordinary_tool_results_still_reach_the_context():
    """The rule targets the bootstrap file, not tool results in general."""
    messages = (
        _pair("read", {"path": "/workspace/core/foo.py"},
              tc_id="ord-1", origin="mcp", body="def foo(): return 1")
        + [LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "def foo(): return 1" in body
    assert "read(" in body


def test_free_text_survives_when_its_call_is_dropped():
    """Only the call synopsis goes; anything the agent said stays."""
    messages = (
        _pair("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"},
              text="I'll read the bootstrap context file first.")
        + [LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "I'll read the bootstrap context file first." in body
    assert "initial_context.md" not in body
    assert "serialized history" not in body


def test_delta_serializer_drops_the_pair_too():
    """The resume path serializes history as well and obeys the same rule."""
    messages = (
        [LLMMessage(role="user", content="earlier", conversation_id="conv"),
         LLMMessage(role="assistant", content="an answer", conversation_id="conv")]
        + _pair("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"})
        + [LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    _system, user_text = _client()._serialize_messages_for_cli(messages, None)

    assert "serialized history" not in user_text
    assert "initial_context.md" not in user_text
    assert "earlier" in user_text


def test_cold_start_context_file_does_not_embed_its_own_previous_copy(tmp_path):
    """End to end: the written file never quotes a bootstrap file."""
    messages = (
        [LLMMessage(role="system", content="system rules", conversation_id="conv")]
        + _pair("Read", {"file_path": "/cc_sessions/u/conv/a/.pawflow_cci/initial_context.md"})
        + [LLMMessage(role="user", content="latest request", conversation_id="conv")]
    )

    _client()._cci_prompt(
        messages, None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=True)
    written = (tmp_path / ".pawflow_cci" / "initial_context.md").read_text()

    # Exactly one header: its own. A nested copy would add more.
    assert written.count("# PawFlow Initial Context") == 1
    assert "serialized history" not in written
    assert "system rules" in written
    assert "latest request" in written


@pytest.mark.parametrize(("tool_name", "arguments"), BOOTSTRAP_CALLS)
def test_gauge_still_counts_the_bootstrap_read_result(tool_name, arguments):
    """The other half of the rule: dropped from context, still counted.

    The result body is what actually fills the provider's window, so removing it
    from serialization must never make the gauge stop charging for it.
    """
    from tasks.ai.context_usage_cache import context_usage_from_cache

    call, result = _pair(tool_name, arguments)
    call.source = {
        "type": "agent",
        "name": "assistant",
        "context_usage_boundary": "cli_bootstrap_read",
    }
    messages = [
        LLMMessage(role="user", content=BOOTSTRAP_BODY, conversation_id="conv"),
        call,
        result,
    ]

    with_result = context_usage_from_cache(messages, 200000, source="cold_cli")
    without_result = context_usage_from_cache(
        messages[:2], 200000, source="cold_cli_no_result")

    # What the body costs on its own, as the reference charge.
    body_alone = context_usage_from_cache(
        [LLMMessage(role="user", content=BOOTSTRAP_BODY, conversation_id="c")],
        200000, source="body_alone")

    # The boundary is found, the serialized prefix is zeroed...
    assert with_result["bootstrap_context_start"] == 1
    # ...and the read result is charged its full weight, not discarded.
    assert with_result["used"] > without_result["used"]
    assert with_result["used"] >= body_alone["used"] * 0.9
