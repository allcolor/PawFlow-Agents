from pawflow_cli.stream_events import translate_sse_event


def test_stream_json_emits_live_thinking_delta():
    events, acc = translate_sse_event(
        "thinking_delta", {"text": "plan"}, "sess", "")

    assert acc == ""
    assert events[0]["message"]["content"] == [
        {"type": "thinking", "thinking": "plan"}
    ]


def test_stream_json_suppresses_persisted_cci_thinking_after_live_delta():
    events, acc = translate_sse_event(
        "thinking_content",
        {
            "text": "plan",
            "msg_id": "m1",
            "source": {"provider": "claude-code-interactive"},
        },
        "sess",
        "",
    )

    assert events == []
    assert acc == ""
