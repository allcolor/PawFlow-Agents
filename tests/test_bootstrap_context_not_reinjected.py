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

import json

import pytest

from core.llm_client import LLMClient, LLMMessage, LLMToolCall

BOOTSTRAP_BODY = "# PawFlow Initial Context\n" + ("serialized history " * 400)
OPAQUE_EXEC_RESULT = (
    '<tool_output tool="exec">\n'
    "Script completed\n"
    "Output:\n"
    f"{BOOTSTRAP_BODY}\n"
    "</tool_output>"
)

# Every shape the CLI providers use to open their own bootstrap file.
BOOTSTRAP_CALLS = [
    ("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"}, "native"),
    ("Read", {"file_path": "/cc_sessions/c/a/.pawflow_cli/initial_context.md"}, "native"),
    ("view_file", {"path": "/cc_sessions/c/a/.pawflow_ag/initial_context.md"}, "native"),
    ("read_file", {"path": r"C:\cc_sessions\c\a\.pawflow_cli\initial_context.md"}, "native"),
    ("codex_native_commandExecution",
     {"command": "sed -n '1,240p' /cc_sessions/c/a/.pawflow_cli/initial_context.md"},
     "native"),
    ("mcp__pawflow__use_tool", {
        "tool_name": "read",
        "arguments_json": json.dumps({
            "path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md",
        }),
     }, "mcp"),
    ("mcp__pawflow__use_tool", {
        "tool_name": "bash",
        "arguments_json": json.dumps({
            "command": "sed -n '1,240p' /cc_sessions/c/a/.pawflow_cci/initial_context.md",
        }),
     }, "mcp"),
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


@pytest.mark.parametrize(("tool_name", "arguments", "origin"), BOOTSTRAP_CALLS)
def test_bootstrap_pair_never_reaches_the_agent_context(
        tool_name, arguments, origin):
    """Neither the call nor its result is serialized back into the context."""
    messages = (
        [LLMMessage(role="user", content="earlier", conversation_id="conv")]
        + _pair(tool_name, arguments, origin=origin)
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


def test_opaque_codex_code_mode_bootstrap_result_is_not_reinjected():
    """The persisted exec body has lost the path, but its result is conclusive."""
    messages = (
        [LLMMessage(role="user", content="earlier", conversation_id="conv")]
        + _pair(
            "exec",
            {"input": "<code-mode script, 249 chars>"},
            origin="mcp",
            body=OPAQUE_EXEC_RESULT,
        )
        + [LLMMessage(role="assistant", content="an answer",
                      conversation_id="conv"),
           LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "# PawFlow Initial Context" not in body
    assert "serialized history" not in body
    assert "code-mode script" not in body
    assert "earlier" in body
    assert "an answer" in body


def test_marked_paginated_bootstrap_page_needs_no_header():
    """Every page is excluded after the original script body is elided."""
    messages = (
        [LLMMessage(role="user", content="earlier", conversation_id="conv")]
        + _pair(
            "exec",
            {
                "input": "<code-mode script, 249 chars>",
                "_pawflow_bootstrap_read": True,
            },
            origin="native",
            body="middle page of serialized history without its first header",
        )
        + [LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "middle page" not in body
    assert "code-mode script" not in body
    assert "earlier" in body


def test_a_quoted_bootstrap_title_is_not_mistaken_for_the_file():
    """A search hit mentioning the title is context, not a bootstrap read."""
    quoted = 'Search result:\n> 1  "# PawFlow Initial Context"'
    messages = (
        _pair("exec", {"input": "<code-mode script, 80 chars>"}, body=quoted)
        + [LLMMessage(role="user", content="latest", conversation_id="conv")]
    )

    body = _client()._cli_context_before_latest_text(messages)

    assert "# PawFlow Initial Context" in body
    assert "code-mode script" in body


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
        + _pair(
            "exec",
            {"input": "<code-mode script, 249 chars>"},
            body=OPAQUE_EXEC_RESULT,
        )
        + [LLMMessage(role="user", content="latest request", conversation_id="conv")]
    )

    _client()._cci_prompt(
        messages, None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=True, agent_name="a")
    written = (tmp_path / ".pawflow_cci" / "initial_context.md").read_text()

    # Exactly one header: its own. A nested copy would add more.
    assert written.count("# PawFlow Initial Context") == 1
    assert "serialized history" not in written
    assert "system rules" in written
    assert "latest request" in written


@pytest.mark.parametrize(("tool_name", "arguments", "origin"), BOOTSTRAP_CALLS)
def test_gauge_never_double_counts_the_bootstrap_read_result(
        tool_name, arguments, origin):
    """The local gauge counts the stored messages, not their bootstrap copy."""
    from tasks.ai.context_usage_cache import context_usage_from_cache

    call, result = _pair(tool_name, arguments, origin=origin)
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

    assert with_result["used"] == without_result["used"]


def test_gauge_drops_an_opaque_mcp_shell_bootstrap_result():
    """A hidden shell path is recovered from the exact bootstrap header."""
    from tasks.ai.context_usage_cache import context_usage_from_cache

    call, result = _pair(
        "exec", {"input": "<code-mode script, 249 chars>"},
        origin="mcp", body=OPAQUE_EXEC_RESULT)
    messages = [
        LLMMessage(role="user", content="real context", conversation_id="conv"),
        call,
        result,
    ]

    with_result = context_usage_from_cache(messages, 200000, source="opaque")
    without_result = context_usage_from_cache(
        messages[:2], 200000, source="opaque_no_result")

    assert with_result["used"] == without_result["used"]
