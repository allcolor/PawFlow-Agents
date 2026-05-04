"""Structural checks for chat SSE reconnect behavior."""

from pathlib import Path


SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")


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
