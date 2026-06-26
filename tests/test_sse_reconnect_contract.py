"""Structural checks for chat SSE reconnect behavior."""

from pathlib import Path


# sse.js was split into <=800-line files (state + handler wires + connectSSE
# shell); introspection here needs the combined source in load order.
SSE_JS = "".join(
    Path(f"tasks/io/chat_ui/{_m}").read_text(encoding="utf-8")
    for _m in ("sse_state.js", "sse_handlers_a.js", "sse_handlers_b.js", "sse.js"))
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


def test_sse_onerror_classifies_expired_session_for_reauth():
    # An expired session yields an opaque EventSource error; the handler must
    # probe the endpoint to distinguish 401 (→ re-auth) from a network blip
    # (→ silent backoff) instead of looping forever behind a blank screen.
    handler_start = SSE_JS.index("eventSource.onerror = (err) => {")
    handler_end = SSE_JS.index("eventSource.onopen = () => {", handler_start)
    handler = SSE_JS[handler_start:handler_end]
    assert "_probeSSEAuth(cid)" in handler

    probe = SSE_JS[
        SSE_JS.index("function _probeSSEAuth"):
        SSE_JS.index("function _scheduleSSEReconnect")]
    # Re-hit the events endpoint with fetch (which exposes the status) and
    # only a definitive 401/403 triggers re-auth.
    assert "resp.status === 401" in probe
    assert "resp.status === 403" in probe
    assert "_handleSessionExpired()" in probe

    expired = SSE_JS[
        SSE_JS.index("function _handleSessionExpired"):
        SSE_JS.index("function _probeSSEAuth")]
    assert "t('sessionExpired')" in expired
    assert "LOGIN_URL" in expired
    # A confirmed expiry stops the backoff loop.
    assert "_sseSessionExpired" in SSE_JS[
        SSE_JS.index("function _scheduleSSEReconnect"):
        SSE_JS.index("function _scheduleSSEReconnect") + 400]


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
