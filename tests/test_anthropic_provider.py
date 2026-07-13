import json

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


def test_anthropic_image_ref_payload_includes_context_and_image(monkeypatch, caplog):
    class _Store:
        def get_required(self, file_id, user_id="", conversation_id=""):
            assert file_id == "img_1"
            assert user_id == "u"
            assert conversation_id == "conv-a"
            return "screen.png", b"image-bytes", "image/png"

    import core.file_store as file_store
    monkeypatch.setattr(file_store.FileStore, "instance", staticmethod(lambda: _Store()))

    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    messages = [
        LLMMessage(
            role="user",
            content=[{"type": "image_ref", "file_id": "img_1"}],
            conversation_id="conv-a",
        ),
    ]

    with caplog.at_level("INFO", logger="core.llm_providers.anthropic"):
        _system, api_messages = client._build_anthropic_messages(
            messages, user_id="u", conversation_id="conv-a")

    content = api_messages[0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "Attached image: fs://filestore/img_1/screen.png",
    }
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"
    assert client._count_anthropic_image_blocks(api_messages) == 1
    assert "Anthropic payload includes image blocks: count=1" in caplog.text

    tool_messages = [
        LLMMessage(role="user", content="look", conversation_id="conv-a"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[LLMToolCall(id="call_1", name="see", arguments={})],
            conversation_id="conv-a",
        ),
        LLMMessage(
            role="tool",
            content=[{"type": "image_ref", "file_id": "img_1"}],
            tool_call_id="call_1",
            conversation_id="conv-a",
        ),
    ]

    _system, tool_api_messages = client._build_anthropic_messages(
        tool_messages, user_id="u", conversation_id="conv-a")
    tool_content = tool_api_messages[2]["content"][0]["content"]
    assert tool_content[0]["text"] == "Attached image: fs://filestore/img_1/screen.png"
    assert tool_content[1]["type"] == "image"
    assert client._count_anthropic_image_blocks(tool_api_messages) == 1


def test_anthropic_omits_image_blocks_when_service_vision_disabled(caplog):
    client = LLMClient(provider="anthropic", config={
        "api_key": "test",
        "supports_vision": False,
    })
    messages = [LLMMessage(
        role="user",
        content=[
            {"type": "text", "text": "describe"},
            {"type": "image_ref", "file_id": "img_1", "filename": "screen.png"},
        ],
        conversation_id="conv-a",
    )]

    with caplog.at_level("INFO", logger="core.llm_providers.anthropic"):
        _system, api_messages = client._build_anthropic_messages(
            messages, user_id="u", conversation_id="conv-a")

    content = api_messages[0]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1] == {
        "type": "text",
        "text": "Attached image: fs://filestore/img_1/screen.png",
    }
    assert client._count_anthropic_image_blocks(api_messages) == 0
    assert "Anthropic payload includes image blocks" not in caplog.text


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


def test_anthropic_cache_log_does_not_report_negative_input(caplog):
    client = LLMClient(provider="anthropic", config={"api_key": "test"})

    with caplog.at_level("INFO", logger="core.llm_providers.anthropic"):
        client._log_anthropic_cache_usage(
            tokens_in=1508,
            cache_creation_tokens=0,
            cache_read_tokens=164352,
        )

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "-" not in logged
    assert "1508 input tokens" in logged
    assert "164352 read" in logged



def test_anthropic_stream_accepts_compatible_text_delta_without_type(monkeypatch):
    client = LLMClient(provider="anthropic", config={
        "api_key": "test",
        "default_model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.example/anthropic",
    })
    events = [
        {"type": "message_start", "message": {
            "model": "deepseek-v4-pro",
            "usage": {"input_tokens": 10},
        }},
        {"type": "content_block_start", "content_block": {"type": "text"}},
        {"type": "content_block_delta", "delta": {"text": "hello"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    payload = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    class _Resp:
        status = 200

        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _n=-1):
            return self._chunks.pop(0)

    class _Conn:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    import core.llm_providers.anthropic as anthropic_mod
    monkeypatch.setattr(anthropic_mod.http.client, "HTTPSConnection", _Conn)

    seen = []
    resp = client.complete_stream(
        [LLMMessage(role="user", content="hi", conversation_id="conv-a")],
        callback=seen.append,
    )

    assert resp.content == "hello world"
    assert seen == ["hello world"]
    assert resp.tokens_in == 10
    assert resp.tokens_out == 2


def test_anthropic_stream_flushes_interleaved_callbacks_by_block_index(monkeypatch):
    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    events = [
        {"type": "message_start", "message": {
            "model": "claude-test",
            "usage": {"input_tokens": 10},
        }},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "plan first"}},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "Visible "}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "answer"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    payload = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    class _Resp:
        status = 200

        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _n=-1):
            return self._chunks.pop(0)

    class _Conn:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    import core.llm_providers.anthropic as anthropic_mod
    monkeypatch.setattr(anthropic_mod.http.client, "HTTPSConnection", _Conn)

    text_seen = []
    thinking_seen = []
    resp = client.complete_stream(
        [LLMMessage(role="user", content="hi", conversation_id="conv-a")],
        callback=text_seen.append,
        thinking_callback=thinking_seen.append,
        thinking_budget=1024,
    )

    assert resp.content == "Visible answer"
    assert resp.thinking == "plan first"
    assert thinking_seen == ["plan first"]
    assert text_seen == ["Visible answer"]


def test_anthropic_stream_does_not_replace_malformed_tool_json_with_empty_args(monkeypatch):
    from core.tool_json import PARSE_ERROR_KEY

    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    events = [
        {"type": "message_start", "message": {
            "model": "claude-test",
            "usage": {"input_tokens": 10},
        }},
        {"type": "content_block_start", "content_block": {
            "type": "tool_use", "id": "call_1", "name": "read",
        }},
        {"type": "content_block_delta", "delta": {
            "type": "input_json_delta",
            "partial_json": '{"path": }',
        }},
        {"type": "content_block_stop"},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    payload = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    class _Resp:
        status = 200

        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _n=-1):
            return self._chunks.pop(0)

    class _Conn:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    import core.llm_providers.anthropic as anthropic_mod
    monkeypatch.setattr(anthropic_mod.http.client, "HTTPSConnection", _Conn)

    resp = client.complete_stream(
        [LLMMessage(role="user", content="read", conversation_id="conv-a")],
        tools=[LLMToolDefinition(name="read", description="read", parameters={})],
    )

    assert resp.tool_calls[0].name == "read"
    assert PARSE_ERROR_KEY in resp.tool_calls[0].arguments
    assert resp.tool_calls[0].arguments != {}


def test_anthropic_stream_appends_input_json_delta_after_empty_start_input(monkeypatch):
    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    events = [
        {"type": "message_start", "message": {
            "model": "claude-test",
            "usage": {"input_tokens": 10},
        }},
        {"type": "content_block_start", "content_block": {
            "type": "tool_use", "id": "call_1", "name": "use_tool", "input": {},
        }},
        {"type": "content_block_delta", "delta": {
            "type": "input_json_delta",
            "partial_json": '{"tool_name":"read","arguments":{"path":"/workspace/README.md"}}',
        }},
        {"type": "content_block_stop"},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    payload = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    class _Resp:
        status = 200

        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _n=-1):
            return self._chunks.pop(0)

    class _Conn:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return _Resp()

        def close(self):
            pass

    import core.llm_providers.anthropic as anthropic_mod
    monkeypatch.setattr(anthropic_mod.http.client, "HTTPSConnection", _Conn)

    resp = client.complete_stream(
        [LLMMessage(role="user", content="read", conversation_id="conv-a")],
        tools=[LLMToolDefinition(name="use_tool", description="use", parameters={})],
    )

    assert resp.tool_calls[0].name == "use_tool"
    assert resp.tool_calls[0].arguments == {
        "tool_name": "read",
        "arguments": {"path": "/workspace/README.md"},
    }


def test_anthropic_response_accepts_compatible_reasoning_content(monkeypatch):
    client = LLMClient(provider="anthropic", config={"api_key": "test"})

    def fake_post(path, body, headers=None):
        return {
            "content": [
                {"type": "thinking", "reasoning_content": "deepseek reasoning"},
                {"type": "text", "text": "final"},
            ],
            "model": "deepseek-v4-pro",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(client, "_http_post", fake_post)
    resp = client.complete([
        LLMMessage(role="user", content="summarize", conversation_id="conv-a")
    ])

    assert resp.content == "final"
    assert resp.thinking == "deepseek reasoning"


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


def test_anthropic_concatenates_multiple_system_messages():
    """A later system message (e.g. injected context) must never replace
    the agent's system prompt -- Anthropic takes one system body, so the
    builder concatenates them in order."""
    client = LLMClient(provider="anthropic", config={"api_key": "test"})
    messages = [
        LLMMessage(role="system", content="Agent identity and rules.",
                   conversation_id="conv-s"),
        LLMMessage(role="user", content="Do the thing",
                   conversation_id="conv-s"),
        LLMMessage(role="system", content="[Injected advisor reports]",
                   conversation_id="conv-s"),
    ]

    system_text, api_messages = client._build_anthropic_messages(
        messages, user_id="u", conversation_id="conv-s")

    assert system_text == (
        "Agent identity and rules.\n\n[Injected advisor reports]")
    assert [m["role"] for m in api_messages] == ["user"]
