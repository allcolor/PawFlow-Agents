"""Execute relay filesystem actions inside the PawFlow server container.

This surface is reserved for managed server relays whose administrator has
explicitly enabled ``server_local_exec``.  It intentionally reuses the relay
filesystem action implementations without opening a host-helper socket.
"""

import json
import logging
import threading
import uuid
from typing import Any, Dict


SERVER_LOCAL_ROOT = "/"
logger = logging.getLogger(__name__)
_INTERACTIVE_ACTIONS = frozenset({
    "open_terminal", "close_terminal", "write_terminal", "resize_terminal",
    "list_terminals", "start_desktop", "stop_desktop", "desktop_status",
})
_interactive_init_lock = threading.Lock()
_interactive_context = None


def _dispatch_terminal_frame(frame: bytes) -> None:
    """Forward server-local PTY output through the normal browser proxy."""
    try:
        payload = json.loads(frame.decode("utf-8"))
        session_id = str(payload.get("session_id") or "")
        if payload.get("type") == "terminal_data":
            from services.terminal_proxy import dispatch_terminal_data
            dispatch_terminal_data(session_id, str(payload.get("data") or ""))
        elif payload.get("type") == "terminal_exit":
            from services.terminal_proxy import dispatch_terminal_exit
            dispatch_terminal_exit(session_id)
    except Exception:
        logger.debug("Server-local terminal frame dispatch failed", exc_info=True)


def _get_interactive_context():
    """Create the process-local terminal/desktop runtime once."""
    global _interactive_context
    if _interactive_context is not None:
        return _interactive_context
    with _interactive_init_lock:
        if _interactive_context is not None:
            return _interactive_context
        from pathlib import Path
        from pawflow_relay._relay_dispatch import DispatchCtx
        from pawflow_relay._relay_state import RelayWorkerState
        from pawflow_relay._relay_terminal import TerminalManager

        def _resolve(path):
            return str(Path(path or SERVER_LOCAL_ROOT).resolve())

        _interactive_context = DispatchCtx(
            state=RelayWorkerState(),
            term_mgr=TerminalManager(SERVER_LOCAL_ROOT, _dispatch_terminal_frame),
            send_lock=threading.Lock(), ws_sock_ref=[None],
            ws_frame_send=lambda *_args, **_kwargs: None,
            resolve=_resolve,
            forward_to_host_helper=lambda *_args, **_kwargs: {
                "ok": False, "error": "Host helper unavailable"},
            root_dir=SERVER_LOCAL_ROOT, readonly=False, allow_exec=True,
            allow_local=False, allow_local_screen=False, allow_automation=False,
        )
    return _interactive_context


def _execute_server_interactive(action: str, arguments: Dict[str, Any]) -> Any:
    """Execute terminal/desktop actions inside the PawFlow server container."""
    from pawflow_relay._relay_dispatch import execute_command

    result = execute_command(
        _get_interactive_context(), {"action": action, **arguments})
    if not isinstance(result, dict) or not result.get("ok"):
        detail = result.get("error", "interactive action failed") if isinstance(result, dict) else str(result)
        raise RuntimeError(detail)
    return result.get("data", {})


def execute_server_local(action: str, path: str, arguments: Dict[str, Any],
                         on_output=None) -> Any:
    """Run one relay action in the PawFlow server container."""
    from tools._fs_paths import _resolve_tool_path
    from tools.fs_actions import ACTIONS

    request = dict(arguments)
    request.pop("local", None)
    request_id = str(request.get("request_id") or uuid.uuid4().hex[:12])
    request["request_id"] = request_id

    handler = ACTIONS.get(action)
    if handler is None and action in _INTERACTIVE_ACTIONS:
        return _execute_server_interactive(action, request)
    if handler is None:
        raise ValueError(f"Action '{action}' is unavailable on the server-local surface")

    abs_path = str(_resolve_tool_path(
        SERVER_LOCAL_ROOT, path, allow_host_absolute=True))

    try:
        from services.tool_relay_service import register_kill_hook
        from pawflow_relay.proc_registry import kill_inflight_proc
        register_kill_hook(lambda rid=request_id: kill_inflight_proc(rid))
    except Exception:
        logger.debug("Server-local kill hook registration failed", exc_info=True)

    if action in ("exec", "exec_stream"):
        extra = {"on_output": on_output} if action == "exec_stream" and on_output else {}
        return handler(
            abs_path, abs_path, request, allow_exec=True, **extra)
    if action == "http_fetch" and on_output:
        return handler(
            SERVER_LOCAL_ROOT, abs_path, request,
            on_chunk=lambda kind, data: on_output(kind, data))
    return handler(SERVER_LOCAL_ROOT, abs_path, request)
