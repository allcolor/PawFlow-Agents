"""Live CLI prompts carry all trailing user messages in one paste."""

import pytest

from core.llm_client import LLMClient, LLMMessage


@pytest.fixture(params=["claude-code-interactive", "codex-interactive",
                        "cc_mcp", "codex_mcp"])
def client(request, monkeypatch):
    client = LLMClient(request.param)
    monkeypatch.setattr(client, "_build_catchup_context", lambda cid, agent: "")
    return client


def _msgs():
    return [
        LLMMessage(role="system", content="rules", conversation_id="conv"),
        LLMMessage(role="user", content="first request", conversation_id="conv"),
        LLMMessage(role="assistant", content="answer one", conversation_id="conv"),
        LLMMessage(role="user", content="check the tests", conversation_id="conv"),
        LLMMessage(role="user", content="include the CLI providers", conversation_id="conv"),
        LLMMessage(role="user", content="report the results", conversation_id="conv"),
    ]


def test_live_text_joins_all_trailing_user_messages_in_order(client):
    assert client._cci_live_text(_msgs()) == (
        "check the tests\n\ninclude the CLI providers\n\nreport the results")


def test_live_text_skips_display_only_messages(client):
    messages = _msgs()
    messages[4].display_only = True

    assert client._cci_live_text(messages) == (
        "check the tests\n\nreport the results")


def test_live_text_skips_tool_rows_and_blank_user_text(client):
    messages = _msgs()
    messages.insert(4, LLMMessage(
        role="tool", content="tool output", conversation_id="conv"))
    messages[5].content = "   "

    assert client._cci_live_text(messages) == (
        "check the tests\n\nreport the results")


def test_live_text_has_no_tail_after_an_assistant_reply(client):
    assert client._cci_live_text(_msgs()[:3]) == ""


def test_prompt_contains_the_whole_user_tail(client, tmp_path):
    prompt = client._cci_prompt(
        _msgs(), None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False, agent_name="a")

    assert prompt == (
        "check the tests\n\ninclude the CLI providers\n\nreport the results\n")
