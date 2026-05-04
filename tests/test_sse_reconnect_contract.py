"""Structural checks for chat SSE reconnect behavior."""

from pathlib import Path


SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
CONVERSATIONS_JS = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")


def test_sse_lifetime_reconnect_is_explicit_server_and_client_contract():
    server = Path("tasks/io/agent_sse_stream.py").read_text(encoding="utf-8")
    assert "event: sse_reconnect" in server
    assert "writer.close()" in server
    assert "sse_reconnect" in SSE_JS
    assert "_scheduleSSEReconnect(cid)" in SSE_JS


def test_sse_onerror_owns_reconnect_instead_of_waiting_for_browser_retry():
    handler_start = SSE_JS.index("eventSource.onerror = (err) => {")
    handler_end = SSE_JS.index("eventSource.onopen = () => {", handler_start)
    handler = SSE_JS[handler_start:handler_end]

    assert "eventSource.close()" in handler
    assert "eventSource = null" in handler
    assert "_scheduleSSEReconnect(cid)" in handler
    assert "readyState === EventSource.CLOSED" not in handler


def test_recover_conversation_returns_promise_for_sse_reconnect_chain():
    start = CONVERSATIONS_JS.index("function _recoverConversation(cid) {")
    end = CONVERSATIONS_JS.index("function deleteConv", start)
    body = CONVERSATIONS_JS[start:end]

    assert "return Promise.resolve()" in body
    assert "return new Promise" in body
    assert "resolve();" in body
    assert "_recoverConversation(cid).then" in SSE_JS


def test_poll_recovery_renders_all_transcript_event_types():
    start = CONVERSATIONS_JS.index("function _recoverConversation(cid) {")
    end = CONVERSATIONS_JS.index("function deleteConv", start)
    body = CONVERSATIONS_JS[start:end]

    assert "mType === 'tool_call'" not in body
    assert "mType === 'tool_result'" not in body
    assert "mType === 'thinking'" not in body
    assert "addMsg(mType, pollContent, m)" in body


def test_sse_tools_use_native_ids_without_synthetic_msg_ids():
    assert "data.msg_id + ':thinking'" not in SSE_JS
    assert "data.msg_id + ':tool_call:'" not in SSE_JS
    assert "tc_id: data.tc_id || ''" in SSE_JS
    assert "_seenMsgIds.add(data.msg_id)" in SSE_JS
