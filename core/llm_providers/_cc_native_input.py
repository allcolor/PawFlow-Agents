"""Claude stream-json control requests using the documented SDK wire format."""

import json
import logging
import threading

from core.native_hook_input import answer_claude_hook


_stdin_lock_init = threading.Lock()


def stdin_lock(proc):
    """Share one write/close lock across all users of a pooled Claude process."""
    with _stdin_lock_init:
        lock = vars(proc).get("_pawflow_cc_stdin_lock")
        if lock is None:
            lock = threading.Lock()
            proc._pawflow_cc_stdin_lock = lock
        return lock


def write_message(proc, message, cancel_event=None):
    """Check cancellation before writing one complete JSON line."""
    lock = stdin_lock(proc)
    while not lock.acquire(timeout=0.2):
        if cancel_event is not None and cancel_event.is_set():
            return
    try:
        if cancel_event is not None and cancel_event.is_set():
            return
        if proc.stdin is None or (cancel_event is not None
                                  and (proc.poll() is not None or proc.stdin.closed)):
            raise BrokenPipeError("Claude input pipe is closed")
        try:
            proc.stdin.write(message + "\n")
            proc.stdin.flush()
        except ValueError as exc:
            raise BrokenPipeError("Claude input pipe is closed") from exc
    finally:
        lock.release()


def handle_control(st, event):
    request_id = event.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Claude control request requires request_id")
    if event["type"] == "control_cancel_request":
        st._native_inputs.cancel(request_id)
        return
    if event["type"] != "control_request":
        raise ValueError("Unsupported Claude control event")

    cancel_event = None

    def collect(cancel):
        nonlocal cancel_event
        cancel_event = cancel
        try:
            request = event.get("request")
            if not isinstance(request, dict) or request.get("subtype") != "can_use_tool":
                raise ValueError("Unsupported Claude control request")
            tool = request.get("tool_name")
            inputs = request.get("input")
            tool_use_id = request.get("tool_use_id")
            if (not isinstance(tool, str) or not tool.strip()
                    or not isinstance(inputs, dict)
                    or not isinstance(tool_use_id, str) or not tool_use_id
                    or len(json.dumps(inputs)) > 64000):
                raise ValueError("Invalid Claude tool permission request")
            output = answer_claude_hook({
                "hook_event_name": "PreToolUse" if tool == "AskUserQuestion" else "PermissionRequest",
                "tool_name": tool, "tool_input": inputs,
                "tool_use_id": tool_use_id,
            }, provider="claude-code", user_id=st.user_id,
                conversation_id=st.conv_id, agent_name=st.agent_name,
                cancel_event=cancel)["hookSpecificOutput"]
            if tool == "AskUserQuestion":
                decision = {"behavior": output["permissionDecision"]}
                if decision["behavior"] == "allow":
                    decision["updatedInput"] = output["updatedInput"]
                else:
                    decision["message"] = output.get("permissionDecisionReason", "Cancelled")
            else:
                decision = output["decision"]
                if decision["behavior"] == "allow":
                    # The SDK control response includes the original input
                    # even when the permission hook did not modify it.
                    decision = {**decision, "updatedInput": decision.get("updatedInput", inputs)}
            return {"subtype": "success", "request_id": request_id, "response": decision}
        except Exception:
            logging.getLogger(__name__).debug("Claude interaction failed", exc_info=True)
            return {"subtype": "error", "request_id": request_id,
                    "error": "Invalid or unsupported Claude interaction request"}

    def reply(response):
        try:
            write_message(st.proc, json.dumps({"type": "control_response", "response": response}),
                          cancel_event)
        except (OSError, ValueError):
            # Process exit can race the poll/closed checks.
            logging.getLogger(__name__).debug("Claude input pipe closed", exc_info=True)
    st._native_inputs.submit(request_id, collect, reply)
