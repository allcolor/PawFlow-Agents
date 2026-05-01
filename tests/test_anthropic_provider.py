from core.llm_client import LLMClient, LLMMessage, LLMToolCall, LLMToolDefinition


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


def test_anthropic_preserves_signed_thinking_before_tool_results():
    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    messages = [
        LLMMessage(role="user", content="summarize", conversation_id="conv-a"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[LLMToolCall(id="call_1", name="read", arguments={"path": "f"})],
            thinking="I should read the file first.",
            thinking_signature="sig_123",
            conversation_id="conv-a",
        ),
        LLMMessage(role="tool", content="file text", tool_call_id="call_1", conversation_id="conv-a"),
    ]

    _system, api_messages = client._build_anthropic_messages(
        messages, user_id="u", conversation_id="conv-a")

    thinking_block = api_messages[1]["content"][0]
    assert thinking_block == {
        "type": "thinking",
        "thinking": "I should read the file first.",
        "signature": "sig_123",
    }
    assert api_messages[1]["content"][1]["type"] == "tool_use"


def test_anthropic_response_preserves_thinking_signature(monkeypatch):
    client = LLMClient(provider="anthropic", config={"api_key": "test"})

    def fake_post(path, body, headers=None):
        return {
            "content": [
                {"type": "thinking", "thinking": "need file", "signature": "sig_abc"},
                {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "f"}},
            ],
            "model": "claude-test",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(client, "_http_post", fake_post)
    resp = client.complete(
        [LLMMessage(role="user", content="summarize", conversation_id="conv-a")],
        tools=[LLMToolDefinition(name="read", description="read", parameters={})],
        thinking_budget=1000,
    )

    assert resp.thinking == "need file"
    assert resp.thinking_signature == "sig_abc"
    assert resp.tool_calls[0].name == "read"
