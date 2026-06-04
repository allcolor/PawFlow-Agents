"""Structural checks for chat SSE reconnect behavior."""

from pathlib import Path


SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
ATTACHMENTS_JS = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
CMD_AGENT_JS = Path("tasks/io/chat_ui/cmd_agent.js").read_text(encoding="utf-8")


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


def test_sse_reconnect_paths_never_poll_render_history():
    assert "_recoverConversation(" not in SSE_JS
    assert "function _forceSSEReconnect" in SSE_JS
    assert "connectSSE(cid);" in SSE_JS
    assert "_forceSSEReconnect(conversationId, { noReplay: true });" in SSE_JS


def test_user_send_reconnects_stale_sse_without_spoofing_activity():
    assert "_ensureSSEBeforeUserAction" in ATTACHMENTS_JS
    assert "_ensureSSEBeforeUserAction" in CMD_AGENT_JS
    assert "await _ensureSSEBeforeUserAction()" in ATTACHMENTS_JS
    assert "function _waitForSSEOpen" in SSE_JS
    ensure_block = SSE_JS[
        SSE_JS.index("function _ensureSSEBeforeUserAction"):
        SSE_JS.index("// SSE liveness watchdog")]
    assert "EventSource.CONNECTING" in ensure_block
    assert "_forceSSEReconnect(conversationId, {});" in ensure_block
    assert "{ noReplay: true }" not in ensure_block
    assert "lastSSEActivity = Date.now();" not in ATTACHMENTS_JS
    assert "lastSSEActivity = Date.now();" not in CMD_AGENT_JS


def test_sse_tools_use_native_ids_without_synthetic_msg_ids():
    assert "data.msg_id + ':thinking'" not in SSE_JS
    assert "data.msg_id + ':tool_call:'" not in SSE_JS
    assert "tc_id: data.tc_id || ''" in SSE_JS
    assert "_seenMsgIds.add(data.msg_id)" in SSE_JS
