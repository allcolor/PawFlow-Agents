"""Server side of the relay tool bridge (``tool.*`` relay requests).

A script running on a relay (``execute_script``, a PFP package runner) calls
PawFlow tools through ``pawflow_relay/tool_bridge.py``: a loopback endpoint in
the relay worker that forwards each call as a ``relay_request`` over the
relay's own authenticated WebSocket. This module answers those requests.

The request carries an opaque capability (``core/script_capability.py``),
never an identity. The capability must have been minted for the relay
connection that carried it and for its owner; the user, conversation, agent,
run handle and fence all come from it. The call then runs through
``ToolRelayService._handle_execute``, the same path as an MCP tool call from a
CLI agent: workflow scope, tool exposure, hooks, approval, fence, cancel,
secret redaction.

Each call is also written to the transcript as a display-only tool row of the
agent (``tool_origin: "script"``), for every provider: the user sees what the
script did, while the agent's context only receives what the script prints.

Methods:
  tool.call           {cap, tool_name, arguments} -> {data: <tool result>}
  tool.schema         {cap, tool_name}            -> {data: {name,
                                                     description, parameters}}
  tool.pfp_host_call  {cap, invocation, host_call} -> {data: <PFP result
                                                     envelope>}
"""

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Tools a script may not call: they own the agent's turn (asking the user,
# planning, scheduling the next turn) or would recurse into another script.
DENIED_TOOLS = frozenset({
    "execute_script",
    "ask_user",
    "request_confirmation",
    "schedule_continuation",
    "EnterPlanMode",
    "ExitPlanMode",
    "compact_result",
    "get_tool_schema",
    "use_tool",
})


def _error(code: str, errno: int, message: str) -> dict[str, Any]:
    return {"error": code, "errno": errno, "message": message}


def _live_tool_relay():
    """The connected ToolRelayService instance, or None."""
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    for sdef in reg.resolve_by_type("toolRelay"):
        svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
        if svc is not None:
            return svc
    return None


def handle(method: str, args: dict[str, Any], *, relay_service_id: str,
           user_id: str) -> dict[str, Any]:
    """Answer one ``tool.*`` relay request from relay ``relay_service_id``."""
    from core import script_capability
    if not user_id:
        return _error("EACCES", 13, "relay has no owner user_id")
    try:
        cap = script_capability.resolve(
            str(args.get("cap") or ""),
            relay_service_id=relay_service_id, user_id=user_id)
    except PermissionError as exc:
        return _error("EACCES", 13, str(exc))
    relay = _live_tool_relay()
    if relay is None:
        return _error("EIO", 5, "no tool relay service is running")
    request_id = f"script-{uuid.uuid4().hex[:12]}"
    if method == "tool.pfp_host_call":
        invocation = args.get("invocation")
        host_call = args.get("host_call")
        if not isinstance(invocation, dict) or not isinstance(host_call, dict):
            return _error("EINVAL", 22,
                          "invocation and host_call must be objects")
        # _handle_pfp_host_call checks the invocation context against the
        # identity, which here comes from the capability.
        reply = relay._handle_pfp_host_call(
            request_id, invocation, host_call, cap.user_id,
            cap.conversation_id, cap.agent_name)
        return _data(reply)
    tool_name = str(args.get("tool_name") or "").strip()
    if not tool_name:
        return _error("EINVAL", 22, "tool_name is required")
    if tool_name in DENIED_TOOLS:
        return _error("EPERM", 1,
                      f"tool '{tool_name}' cannot be called from a script")
    if method == "tool.schema":
        reply = relay._handle_get_schema(
            request_id, tool_name, user_id=cap.user_id,
            conversation_id=cap.conversation_id, agent_name=cap.agent_name)
    elif method == "tool.call":
        arguments = args.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error("EINVAL", 22, "arguments must be an object")
        from services._tool_relay_base import (
            _set_current_run_identity,
            current_run_identity,
        )
        call_id = f"script-{uuid.uuid4().hex[:12]}"
        _publish_script_call(cap, call_id, tool_name, arguments)
        previous = current_run_identity()
        # The nested call inherits the run of the script's parent call, so
        # a force stop or a successor run fences it out like any other.
        _set_current_run_identity(cap.run_identity)
        try:
            logger.info(
                "[script-tools] %s/%s calls %s from relay %s",
                cap.conversation_id[:8], cap.agent_name, tool_name,
                relay_service_id)
            reply = relay._handle_execute(
                call_id, tool_name, arguments, cap.user_id,
                cap.conversation_id, cap.agent_name, publish_code_mode=False)
        finally:
            _set_current_run_identity(previous)
        _publish_script_result(cap, call_id, tool_name, reply)
    else:
        return _error("ENOSYS", 38, f"unknown tool bridge method: {method!r}")
    return _data(reply)


def _data(reply) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return _error("EIO", 5, "tool relay returned no reply")
    if reply.get("type") == "error":
        return _error("EIO", 5, str(reply.get("error") or "tool error"))
    data = reply.get("data")
    if data is None and "result" in reply:
        data = reply.get("result")
    return {"data": data if data is not None else ""}


_SCRIPT_ORIGIN = "script"
_RESULT_PREVIEW_CHARS = 2000


def _agent_service(cap) -> str:
    try:
        from core.conv_agent_config import get_agent_config
        return str((get_agent_config(cap.conversation_id, cap.agent_name)
                    or {}).get("llm_service") or "")
    except Exception:  # noqa: BLE001 - a missing label never blocks a call
        logger.debug("[script-tools] no llm_service label", exc_info=True)
        return ""


def _write_row(cap, msg: dict, sse: dict) -> None:
    """Persist one display-only row, then publish its event."""
    try:
        from core.conversation_writer import ConversationWriter
        ConversationWriter.for_conversation(cap.conversation_id).enqueue_message(
            msg, agent_name=cap.agent_name, user_id=cap.user_id,
            sse_events=[sse])
    except Exception:  # noqa: BLE001 - visibility never blocks the call
        logger.warning("[script-tools] could not record a script call row",
                       exc_info=True)


def _publish_script_call(cap, call_id: str, tool_name: str,
                         arguments: dict) -> None:
    from core.llm_client import stamp_message
    source = {"type": "agent", "name": cap.agent_name}
    # A canonical tool_call row, written as such: the store keeps every field
    # of a non-assistant row (display_only, tool_origin), where expanding an
    # assistant row's tool_calls would drop them.
    msg = stamp_message({
        "role": "tool_call", "content": "", "source": source,
        "display_only": True, "tool_call_id": call_id,
        "tool_name": tool_name, "name": tool_name,
        "arguments": arguments, "tool_origin": _SCRIPT_ORIGIN,
    }, cap.conversation_id)
    data = {
        "tool": tool_name, "arguments": arguments, "tc_id": call_id,
        "msg_id": msg["msg_id"], "ts": msg.get("ts"),
        "agent_name": cap.agent_name, "llm_service": _agent_service(cap),
        "source": source, "tool_origin": _SCRIPT_ORIGIN,
    }
    if cap.parent_call_id:
        # The live view draws the call under the script's own tool row.
        data["parent_tc_id"] = cap.parent_call_id
    _write_row(cap, msg, {"type": "tool_call", "data": data})


def _publish_script_result(cap, call_id: str, tool_name: str, reply) -> None:
    import json
    from core.llm_client import stamp_message
    data = reply.get("data") if isinstance(reply, dict) else reply
    if reply is None:
        data = "Error: tool relay returned no reply"
    elif isinstance(reply, dict) and reply.get("type") == "error":
        data = f"Error: {reply.get('error') or 'tool error'}"
    text = data if isinstance(data, str) else json.dumps(
        data, default=str, ensure_ascii=False)
    source = {"type": "agent", "name": cap.agent_name}
    msg = stamp_message({
        "role": "tool", "content": text, "tool_call_id": call_id,
        "display_only": True, "source": source,
    }, cap.conversation_id)
    _write_row(cap, msg, {"type": "tool_result", "data": {
        "tool": tool_name, "result": text[:_RESULT_PREVIEW_CHARS],
        "tc_id": call_id, "msg_id": msg["msg_id"], "ts": msg.get("ts"),
        "agent_name": cap.agent_name, "llm_service": _agent_service(cap),
        "source": source, "tool_origin": _SCRIPT_ORIGIN,
    }})


class DirectClient:
    """The server-local surface's stand-in for the relay's WebSocket client.

    A ``local=true`` command of a managed relay with server-local execution
    runs inside the server container; its bridge answers here directly, with
    the same capability check as a request arriving from that relay.
    """

    def __init__(self, relay_service_id: str, user_id: str):
        self._relay_service_id = relay_service_id
        self._user_id = user_id

    def request(self, method: str, args: dict[str, Any], timeout=None):
        return handle(method, args, relay_service_id=self._relay_service_id,
                      user_id=self._user_id)


def server_local_handler(relay_service_id: str, user_id: str):
    """Request handler for a server-local command's exec bridge."""
    from pawflow_relay.tool_bridge import forward
    client = DirectClient(relay_service_id, user_id)
    return lambda request: forward(client, request)
