"""SSE-to-stream-json event translator.

Translates PawFlow SSE events into Claude Code stream-json NDJSON format.
"""

import json
from uuid import uuid4


def _uuid():
    return uuid4().hex


def translate_sse_event(event_type, data, session_id, accumulated_text="",
                        stream_state=None):
    """Translate a PawFlow SSE event to Claude Code stream-json format.

    Returns a list of events to emit (may be empty), plus updated accumulated_text.
    """
    events = []
    if stream_state is None:
        stream_state = {}

    source = data.get("source") or {}
    is_cci = source.get("provider") == "claude-code-interactive"
    is_cci_thinking_delta = event_type == "thinking_delta" and is_cci
    is_cci_persisted_thinking = (
        event_type == "thinking_content" and data.get("msg_id") and is_cci
    )
    if (stream_state.get("cci_thinking_delta_seen")
            and not is_cci_thinking_delta
            and not is_cci_persisted_thinking):
        stream_state["cci_thinking_delta_seen"] = False

    if event_type in ("thinking", "thinking_content", "thinking_delta"):
        if is_cci_persisted_thinking:
            if stream_state.get("cci_thinking_delta_seen"):
                stream_state["cci_thinking_delta_seen"] = False
                return events, accumulated_text
        if is_cci_thinking_delta:
            stream_state["cci_thinking_delta_seen"] = True
        text = data.get("text", "")
        if text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })

    elif event_type == "token":
        text = data.get("text", "")
        accumulated_text += text
        # Emit incremental content_block_delta style event
        events.append({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
            "session_id": session_id,
            "uuid": _uuid(),
        })

    elif event_type == "turn_complete":
        # Flush accumulated text as a complete assistant message
        if accumulated_text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": accumulated_text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })
            accumulated_text = ""

    elif event_type == "tool_call":
        # Flush accumulated text first
        if accumulated_text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": accumulated_text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })
            accumulated_text = ""

        tool_name = data.get("tool", "unknown")
        arguments = data.get("arguments", {})
        tc_id = data.get("tc_id", _uuid())
        events.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": tc_id,
                    "name": tool_name,
                    "input": arguments if isinstance(arguments, dict) else {},
                }],
            },
            "session_id": session_id,
            "uuid": _uuid(),
        })

    elif event_type == "tool_result":
        tc_id = data.get("tc_id", "")
        result = data.get("result", "")
        if isinstance(result, dict):
            result_content = json.dumps(result, ensure_ascii=False)
        else:
            result_content = str(result)
        events.append({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tc_id,
                    "content": result_content,
                }],
            },
            "session_id": session_id,
            "uuid": _uuid(),
        })

    elif event_type == "done":
        response = data.get("response", "")
        # Flush accumulated text as final assistant message
        final_text = accumulated_text or response
        if final_text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": final_text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })
            accumulated_text = ""

        tokens_in = data.get("tokens_in", 0)
        tokens_out = data.get("tokens_out", 0)
        duration_ms = data.get("duration_ms", 0)
        model = data.get("model", "")
        events.append({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": response or final_text,
            "duration_ms": duration_ms,
            "duration_api_ms": duration_ms,
            "num_turns": 1,
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "model": model,
            "cost_usd": 0,
            "session_id": session_id,
            "uuid": _uuid(),
        })

    elif event_type == "error_event":
        message = data.get("message", "Unknown error")
        # Flush any accumulated text
        if accumulated_text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": accumulated_text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })
            accumulated_text = ""

        events.append({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": message,
            "errors": [message],
            "duration_ms": 0,
            "duration_api_ms": 0,
            "num_turns": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "model": "",
            "cost_usd": 0,
            "session_id": session_id,
            "uuid": _uuid(),
        })

    elif event_type == "cancelled":
        if accumulated_text:
            events.append({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": accumulated_text}],
                },
                "session_id": session_id,
                "uuid": _uuid(),
            })
            accumulated_text = ""

        events.append({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "Cancelled",
            "errors": ["Cancelled"],
            "duration_ms": 0,
            "duration_api_ms": 0,
            "num_turns": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "model": "",
            "cost_usd": 0,
            "session_id": session_id,
            "uuid": _uuid(),
        })

    # Events we silently ignore (no stream-json equivalent)
    #: _sse_connected, _sse_error, iteration_status, exec_approval_request,
    #   tool_approval_request, ask_user, btw_*, sub_agent_*, exec_output,
    #   notification, compact_progress, task_progress, etc.

    return events, accumulated_text
