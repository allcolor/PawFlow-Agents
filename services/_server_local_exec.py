"""Execute relay filesystem actions inside the PawFlow server container.

This surface is reserved for managed server relays whose administrator has
explicitly enabled ``server_local_exec``.  It intentionally reuses the relay
filesystem action implementations without opening a host-helper socket.
"""

import logging
import uuid
from typing import Any, Dict


SERVER_LOCAL_ROOT = "/"
logger = logging.getLogger(__name__)


def execute_server_local(action: str, path: str, arguments: Dict[str, Any],
                         on_output=None) -> Any:
    """Run one relay action in the PawFlow server container."""
    from tools._fs_paths import _resolve_tool_path
    from tools.fs_actions import ACTIONS

    handler = ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"Action '{action}' is unavailable on the server-local surface")

    request = dict(arguments)
    request.pop("local", None)
    request_id = str(request.get("request_id") or uuid.uuid4().hex[:12])
    request["request_id"] = request_id
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
