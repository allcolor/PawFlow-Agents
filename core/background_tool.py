"""Background Tool Manager — detach running tools from agent loop.

When the user clicks "→ BG" on a running tool call, the agent loop
stops waiting for that tool and continues with a placeholder result.
The tool keeps running in its thread. When it finishes, the result
is injected as a system message in the conversation.

Usage:
    # In _execute_tool_calls: check if a tool was backgrounded
    if BackgroundToolManager.is_backgrounded(tc_id):
        results[tc_id] = (tc, "[Running in background]")
        BackgroundToolManager.register(tc_id, future, conv_id, agent_name, tool_name)

    # Client action: background a tool
    BackgroundToolManager.background(tc_id)

    # Client action: cancel a background tool
    BackgroundToolManager.cancel(tc_id)
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_backgrounded: Dict[str, dict] = {}  # tc_id → task info
_completed: Dict[str, str] = {}  # "conv_id:tc_id" → result (pending agent pickup)
_pending_bg: set = set()  # tc_ids flagged for backgrounding (before registered)


def background(tc_id: str):
    """Flag a tool_call for backgrounding. The agent loop picks this up."""
    with _lock:
        _pending_bg.add(tc_id)
    logger.info("[bg-tool] flagged %s for background", tc_id)


def is_backgrounded(tc_id: str) -> bool:
    """Check if a tool_call has been flagged for backgrounding."""
    with _lock:
        return tc_id in _pending_bg


def register(tc_id: str, future, conversation_id: str,
             agent_name: str = "", tool_name: str = "",
             is_claude_code: bool = False):
    """Register a backgrounded tool with its running future."""
    with _lock:
        _pending_bg.discard(tc_id)
        _backgrounded[tc_id] = {
            "future": future,
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "tool_name": tool_name,
            "is_claude_code": is_claude_code,
            "started_at": time.time(),
            "status": "running",
            "result": None,
        }
    # Watch the future in a daemon thread
    t = threading.Thread(target=_watch_future, args=(tc_id,), daemon=True)
    t.start()
    logger.info("[bg-tool] registered %s (%s) for conv %s",
                tc_id, tool_name, conversation_id[:8])


def cancel(tc_id: str) -> bool:
    """Cancel a background tool. Injects 'Cancelled' result."""
    with _lock:
        task = _backgrounded.get(tc_id)
        if not task or task["status"] != "running":
            # Maybe it's still pending (not yet registered)
            if tc_id in _pending_bg:
                _pending_bg.discard(tc_id)
                return True
            return False
        task["status"] = "cancelled"
        future = task.get("future")

    # Try to cancel the future (may not work if already running)
    if future and hasattr(future, 'cancel'):
        future.cancel()

    # Inject cancellation result
    _inject_result(tc_id, "[Cancelled by user]", is_cancel=True)
    logger.info("[bg-tool] cancelled %s", tc_id)
    return True


def has_pending(conversation_id: str) -> bool:
    """Check if any bg tasks are still running for this conversation."""
    with _lock:
        return any(
            t["conversation_id"] == conversation_id and t["status"] == "running"
            for t in _backgrounded.values()
        )


def wait_pending(conversation_id: str, timeout: float = 120,
                 cancel_check=None) -> int:
    """Wait for all pending bg tasks. Returns count completed.

    cancel_check: callable that raises if the agent was preempted
    (e.g., user sent a new message). Called every second.
    """
    deadline = time.time() + timeout
    completed = 0
    while time.time() < deadline:
        with _lock:
            pending = [
                tc_id for tc_id, t in _backgrounded.items()
                if t["conversation_id"] == conversation_id and t["status"] == "running"
            ]
        if not pending:
            break
        time.sleep(1)
        if cancel_check:
            cancel_check()  # raises AgentCancelled if preempted
        for tc_id in pending:
            with _lock:
                t = _backgrounded.get(tc_id)
                if t and t["status"] != "running":
                    completed += 1
    return completed


def list_tasks(conversation_id: str = "") -> List[dict]:
    """List background tasks, optionally filtered by conversation."""
    with _lock:
        tasks = []
        for tc_id, task in _backgrounded.items():
            if conversation_id and task["conversation_id"] != conversation_id:
                continue
            tasks.append({
                "tc_id": tc_id,
                "tool_name": task["tool_name"],
                "status": task["status"],
                "started_at": task["started_at"],
                "duration": time.time() - task["started_at"],
                "agent_name": task["agent_name"],
            })
        return tasks


def pop_completed(conversation_id: str, tc_id: str) -> Optional[str]:
    """Get and remove a completed bg result. Returns None if not ready."""
    key = f"{conversation_id}:{tc_id}"
    with _lock:
        return _completed.pop(key, None)


def cleanup_done(max_age: float = 300):
    """Remove completed/cancelled tasks older than max_age seconds."""
    with _lock:
        now = time.time()
        to_remove = [
            tc_id for tc_id, task in _backgrounded.items()
            if task["status"] in ("done", "cancelled")
            and now - task["started_at"] > max_age
        ]
        for tc_id in to_remove:
            _backgrounded.pop(tc_id, None)


def _periodic_cleanup():
    """Daemon timer: purge old completed tasks every 60s."""
    cleanup_done(max_age=300)
    t = threading.Timer(60, _periodic_cleanup)
    t.daemon = True
    t.start()


# Start periodic cleanup on module import
threading.Timer(60, _periodic_cleanup).start()


def _watch_future(tc_id: str):
    """Wait for a backgrounded tool's future to complete."""
    with _lock:
        task = _backgrounded.get(tc_id)
    if not task:
        return

    future = task.get("future")
    if not future:
        return

    try:
        # Wait for the future (no timeout — it runs until done)
        tc, result_text = future.result()
        with _lock:
            task = _backgrounded.get(tc_id)
            if not task or task["status"] != "running":
                return  # cancelled while waiting
            task["status"] = "done"
            task["result"] = result_text

        _inject_result(tc_id, result_text)
        logger.info("[bg-tool] %s completed: %d chars", tc_id, len(result_text or ""))

    except Exception as e:
        with _lock:
            task = _backgrounded.get(tc_id)
            if task and task["status"] == "running":
                task["status"] = "error"
                task["result"] = str(e)
        _inject_result(tc_id, f"Error: {e}")
        logger.error("[bg-tool] %s failed: %s", tc_id, e)


def _inject_result(tc_id: str, result_text: str, is_cancel: bool = False):
    """Inject the background tool result into the conversation."""
    with _lock:
        task = _backgrounded.get(tc_id)
    if not task:
        return

    conv_id = task["conversation_id"]
    tool_name = task["tool_name"]
    agent_name = task["agent_name"]

    # Publish SSE event
    try:
        from core.conversation_event_bus import ConversationEventBus
        status = "cancelled" if is_cancel else "done"
        ConversationEventBus.instance().publish_event(conv_id, "bg_task_update", {
            "tc_id": tc_id,
            "tool_name": tool_name,
            "status": status,
            "result": result_text[:500],
            "agent_name": agent_name,
        })
    except Exception:
        pass

    # Inject result into the conversation.
    # Strategy depends on provider:
    # - LLM API: replace the placeholder tool_result in the messages array (direct access)
    # - Claude Code: inject as system message (no direct context access)
    _result_content = "[Cancelled by user]" if is_cancel else result_text
    _is_cc = task.get("is_claude_code", False)

    # 1. Write tool_result to transcript (always — visible at reload)
    try:
        from core.conversation_writer import ConversationWriter
        import uuid as _bg_uuid
        tool_msg = {
            "role": "tool",
            "content": _result_content[:500],
            "tool_call_id": tc_id,
            "msg_id": _bg_uuid.uuid4().hex[:12],
            "ts": time.time(),
            "source": {"type": "system", "name": "background"},
        }
        ConversationWriter.for_conversation(conv_id).enqueue([tool_msg])
    except Exception as e:
        logger.error("[bg-tool] failed to write tool_result to transcript: %s", e)

    # 2. Store result for agent pickup (agent applies it to in-memory messages
    #    between iterations — no race condition with agent's context writes)
    with _lock:
        _completed[f"{conv_id}:{tc_id}"] = _result_content
    logger.info("[bg-tool] stored result for agent pickup: %s (%d chars)", tc_id, len(_result_content))

    # 3. Claude Code only: also inject system message
    # (CC has its own session — context replacement alone won't reach it,
    # context is only sent on new session which can't happen with bg tools)
    if _is_cc:
        try:
            from core.conversation_writer import ConversationWriter
            if is_cancel:
                content = (
                    f"[System: Background task {tool_name} (tool_call_id={tc_id}) was cancelled by user. "
                    f"The tool_call returned a placeholder — ignore its result.]"
                )
            else:
                content = (
                    f"[System: Background task {tool_name} (tool_call_id={tc_id}) has completed. "
                    f"The earlier tool_call returned '[Running in background]' as placeholder. "
                    f"Here is the actual result:\n\n{_result_content}]"
                )
            msg = {
                "role": "user",
                "content": content,
                "msg_id": _bg_uuid.uuid4().hex[:12],
                "ts": time.time(),
                "source": {"type": "system", "name": "background"},
            }
            ConversationWriter.for_conversation(conv_id).enqueue([msg])
        except Exception as e:
            logger.error("[bg-tool] failed to inject CC system message: %s", e)

    # Cleanup old tasks periodically
    cleanup_done(max_age=300)
