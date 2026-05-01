from core.llm_client import LLMClient, LLMMessage, LLMToolCall


def test_anthropic_groups_consecutive_tool_results_after_multi_tool_use():
    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    messages = [
        LLMMessage(role="user", content="inspect project", conversation_id="conv-a"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(id="call_1", name="glob", arguments={"path": "/workspace"}),
                LLMToolCall(id="call_2", name="glob", arguments={"path": "/workspace/docs"}),
                LLMToolCall(id="call_3", name="glob", arguments={"path": "/workspace/pawflow_relay"}),
            ],
            conversation_id="conv-a",
        ),
        LLMMessage(role="tool", content="a.py", tool_call_id="call_1", conversation_id="conv-a"),
        LLMMessage(role="tool", content="relay.md", tool_call_id="call_2", conversation_id="conv-a"),
        LLMMessage(role="tool", content="thread.py", tool_call_id="call_3", conversation_id="conv-a"),
    ]

    _system, api_messages = client._build_anthropic_messages(
        messages, user_id="u", conversation_id="conv-a")

    assert api_messages[1]["role"] == "assistant"
    assert [b["id"] for b in api_messages[1]["content"] if b["type"] == "tool_use"] == [
        "call_1", "call_2", "call_3",
    ]
    assert api_messages[2]["role"] == "user"
    assert [b["tool_use_id"] for b in api_messages[2]["content"]] == [
        "call_1", "call_2", "call_3",
    ]
    assert len(api_messages) == 3
