"""AgentLoopTask actions — tools exec"""

import json
import logging
import time
import threading

from core.tool_registry import ToolRegistry
from tasks.ai.actions._conv_base import (_deny_conversation,
                                         _gate_conversation_action)

logger = logging.getLogger(__name__)


# What each action needs on the conversation it names.
#
# This cluster does not read a conversation, it *drives* it: it approves the
# tools the agent is waiting on, kills them, detaches them, and runs new ones
# on the conversation's relay. None of that was gated. The SSE stream hands
# approval requests to anyone with read access (agent_sse_stream requires only
# read), so a read collaborator could answer them -- approve with
# `always_allow`, persist the permission, then interrupt or detach whatever
# ran next. Driving is a write.
_ACTION_ROLES = {
    "exec_inline": "write",        # a shell command on the conversation's relay
    "call_tool": "write",          # runs a tool and writes both messages
    "create_dynamic_tool": "write",
    "tool_approval_result": "write",
    "kill_tool": "write",
    "background_tool": "write",
    "cancel_bg_tool": "write",
    "list_bg_tools": "read",
    "list_tools": "read",
}

# Handled here but not scoped to a conversation. Listed so the completeness
# test can tell "reviewed, needs no conversation check" from "forgotten".
_UNSCOPED_ACTIONS = frozenset({
    "install_tool",      # writes the requester's own user-scoped tool
    "uninstall_tool",    # deletes the requester's own user-scoped tool
    "tool_metrics",      # process-wide counters, no conversation content
    "get_tool_schemas",  # static tool definitions
})


# Actions whose real target is a tc_id, not the conversation_id beside it.
_TC_TARGETED_ACTIONS = frozenset({
    "kill_tool", "background_tool", "cancel_bg_tool",
})


def _tc_conversation(tc_id: str, claimed_cid: str = "") -> str:
    """The conversation that actually owns `tc_id`.

    Both registries are global and keyed by tc_id alone, so checking the
    conversation_id in the body would authorize the wrong thing: a caller can
    pair a conversation they may write to with someone else's tc_id and kill
    that. Resolve the owner and check against it.

    An unknown tc_id resolves to nothing. The conversation beside it is only a
    caller claim and must never authorize a process-control action.
    """
    try:
        from services.tool_relay_service import ToolRelayService
        owner = ToolRelayService.conversation_for_request(tc_id)
        if owner:
            return owner
    except Exception:
        logger.debug("relay owner lookup failed for tc %s", tc_id[:8],
                     exc_info=True)
    try:
        import core.background_tool as _bg
        owner = _bg.conversation_for(tc_id)
        if owner:
            return owner
    except Exception:
        logger.debug("background owner lookup failed for tc %s", tc_id[:8],
                     exc_info=True)
    return ""


def _handle_tools_exec(self, action, body, store, user_id, flowfile):
    """Handle tools exec actions. Returns [flowfile] or None."""
    _gate_body = body
    if action in _TC_TARGETED_ACTIONS:
        _tc_id = str(body.get("tc_id", "") or "")
        _owner_cid = _tc_conversation(_tc_id)
        if _tc_id and not _owner_cid:
            return _deny_conversation(flowfile)
        _gate_body = {**body, "conversation_id": _owner_cid}
    elif action == "tool_approval_result":
        # Same reasoning: the request_id names the conversation whose agent is
        # blocked, and that is the one the answer has to be authorized on.
        from core.tool_approval import ToolApprovalGate as _TAG
        _owner_cid = (_TAG.conversation_for_request(
            str(body.get("request_id", "") or ""))
            or str(body.get("conversation_id", "") or ""))
        _gate_body = {**body, "conversation_id": _owner_cid}
    _denied = _gate_conversation_action(_ACTION_ROLES, action, _gate_body,
                                        store, user_id, flowfile)
    if _denied is not None:
        return _denied

    if action == "exec_inline":
        # !cmd — execute shell command on relay, return output to client
        command = body.get("command", "")
        if not command:
            flowfile.set_content(json.dumps({"error": "Missing command"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.handlers._fs_base import find_fs_service
            service_name = body.get("service", "")
            svc = find_fs_service(user_id, service_name,
                                  body.get("conversation_id", ""))
            if not svc:
                flowfile.set_content(json.dumps({"error": "No relay connected"}).encode())
                return [flowfile]
            _exec_kwargs = {}
            if "timeout" in body:
                _exec_kwargs["timeout"] = body["timeout"]
            result = svc.exec(".", command, **_exec_kwargs)
            output = result.get("stdout", "")
            if result.get("stderr"):
                output += ("\nSTDERR:\n" if output else "STDERR:\n") + result["stderr"]
            if result.get("returncode", 0) != 0:
                output += f"\n(exit code: {result['returncode']})"
            flowfile.set_content(json.dumps({"output": output or "(no output)"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "background_tool":
        tc_id = body.get("tc_id", "")
        if not tc_id:
            flowfile.set_content(json.dumps({"error": "Missing tc_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        import core.background_tool as _bg
        _bg.background(tc_id)
        flowfile.set_content(json.dumps({"ok": True, "tc_id": tc_id}).encode())
        return [flowfile]

    if action == "kill_tool":
        tc_id = body.get("tc_id", "")
        conv_id = body.get("conversation_id", "")
        if not tc_id:
            flowfile.set_content(json.dumps({"error": "Missing tc_id"}).encode())
            return [flowfile]
        # Try targeted kill first — cancel_request matches on the CC
        # tc_id (via cc_tc_id fallback inside) so a single tool in a
        # parallel batch gets killed, not all of them.
        from services.tool_relay_service import ToolRelayService
        _targeted = ToolRelayService.cancel_request(tc_id)
        # Always run background cancel (no-op if tc_id isn't backgrounded).
        import core.background_tool as _bg
        _bg.cancel(tc_id)
        # Fall back to the broad agent-level cancel ONLY when live work could
        # actually be this call: a request in flight before its tool_call id
        # was published carries no cc_tc_id, so a targeted cancel misses it.
        # A miss with every live request already bound to another id means
        # this call has FINISHED — widening then kills a bystander, which is
        # exactly what happened when a stale-looking `edit` was killed and
        # took the running `bash` with it.
        _widened = False
        if not _targeted and conv_id:
            _widened = ToolRelayService.has_unbound_inflight(conv_id)
            if _widened:
                ToolRelayService.cancel_agent(conv_id, agent_name="")
        _killed = bool(_targeted) or _widened
        flowfile.set_content(json.dumps({
            "ok": _killed, "tc_id": tc_id,
            "reason": "" if _killed else "not_in_flight",
        }).encode())
        return [flowfile]

    if action == "cancel_bg_tool":
        tc_id = body.get("tc_id", "")
        if not tc_id:
            flowfile.set_content(json.dumps({"error": "Missing tc_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        import core.background_tool as _bg
        ok = _bg.cancel(tc_id)
        flowfile.set_content(json.dumps({"ok": ok, "tc_id": tc_id}).encode())
        return [flowfile]

    if action == "list_bg_tools":
        conv_id = body.get("conversation_id", "")
        import core.background_tool as _bg
        tasks = _bg.list_tasks(conv_id)
        flowfile.set_content(json.dumps({"tasks": tasks}).encode())
        return [flowfile]

    if action == "tool_approval_result":
        # Plan A: User responding to a universal tool approval dialog
        request_id = body.get("request_id", "")
        result = body.get("result", {})
        if not request_id:
            flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.tool_approval import ToolApprovalGate
        ToolApprovalGate.resolve_request(request_id, result)
        flowfile.set_content(json.dumps({"status": "ok"}).encode())
        return [flowfile]

    if action == "install_tool":
        body.get("filename", "")
        source = body.get("source", "")
        if not source:
            flowfile.set_content(json.dumps({"error": "Missing source code"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.tool_validation import validate_and_load
            handler, tool_name = validate_and_load(source)
            from core.resource_store import ResourceStore
            data = {
                "source": source,
                "description": handler.description,
                "parameters": handler.parameters_schema.get("properties", {}),
            }
            try:
                ResourceStore.instance().create(
                    "tool", tool_name, user_id, data)
            except ValueError:
                ResourceStore.instance().update(
                    "tool", tool_name, user_id, data)
            self._tool_registry = None
            flowfile.set_content(json.dumps({
                "installed": True,
                "tool_name": tool_name,
                "description": handler.description,
            }).encode())
        except (ValueError, PermissionError) as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "uninstall_tool":
        tool_name = body.get("tool_name", "")
        if not tool_name:
            flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.resource_store import ResourceStore
            removed = ResourceStore.instance().delete(
                "tool", tool_name, user_id)
            self._tool_registry = None
            flowfile.set_content(json.dumps({
                "uninstalled": removed, "tool_name": tool_name,
            }).encode())
        except PermissionError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "403")
        return [flowfile]

    if action == "create_dynamic_tool":
        tool_name = body.get("tool_name", "").strip()
        tool_description = body.get("tool_description", "")
        parameters = body.get("parameters", {})
        code = body.get("code", "")
        if not tool_name or not code:
            flowfile.set_content(json.dumps({"error": "tool_name and code are required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.handlers.dynamic_tool import CreateToolHandler
            handler = CreateToolHandler()
            handler.set_user_id(user_id)
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("conversation_id") or ""
            handler.set_conversation_id(conv_id)
            result = handler.execute({
                "tool_name": tool_name,
                "tool_description": tool_description,
                "parameters": parameters,
                "code": code,
            })
            flowfile.set_content(json.dumps({"ok": True, "result": result}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_tools":
        try:
            from core.resource_store import ResourceStore
            conv_id = (body.get("conversation_id", "") or
                       flowfile.get_attribute("conversation_id") or "")
            entries = ResourceStore.instance().list_all(
                "tool", user_id, conversation_id=conv_id) or []
            tools = [{
                "tool_name": e.get("name", ""),
                "description": e.get("description", ""),
                "owner": user_id,
                "installed_at": e.get("created_at", 0),
                "scope": e.get("_scope", "user"),
                "source": "dynamic",
            } for e in entries]
            flowfile.set_content(json.dumps({
                "tools": tools,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "tool_metrics":
        metrics = ToolRegistry.get_metrics()
        rows = []
        for name, stats in sorted(metrics.items()):
            calls = int(stats.get("calls", 0) or 0)
            errors = int(stats.get("errors", 0) or 0)
            successes = int(stats.get("successes", 0) or 0)
            avg_ms = float(stats.get("avg_duration_ms", 0.0) or 0.0)
            max_ms = float(stats.get("max_duration_ms", 0.0) or 0.0)
            last_ms = float(stats.get("last_duration_ms", 0.0) or 0.0)
            rows.append(
                f"{name}: calls={calls} ok={successes} errors={errors} "
                f"avg={avg_ms:.1f}ms max={max_ms:.1f}ms last={last_ms:.1f}ms"
            )
            last_error = str(stats.get("last_error", "") or "")
            if last_error:
                rows.append(f"  last_error={last_error[:220]}")
        output = "Tool metrics\n" + ("\n".join(rows) if rows else "No tool calls recorded yet.")
        flowfile.set_content(json.dumps({"output": output, "metrics": metrics}, ensure_ascii=False).encode())
        return [flowfile]

    # User tool call

    if action == "get_tool_schemas":
        # Return all builtin tool definitions for /call help
        registry = self.get_tool_registry()
        tools = [{
            "name": h.name,
            "description": h.description,
            "parameters": h.parameters_schema,
        } for h in registry.list_tools()]
        flowfile.set_content(json.dumps({"tools": tools}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "call_tool":
        tool_name = body.get("tool_name", "")
        tool_args = body.get("arguments", {})
        positional = body.get("positional_args", [])
        conv_id = body.get("conversation_id", "")
        if not tool_name:
            flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
            return [flowfile]
        registry = self.get_tool_registry()
        if conv_id or user_id:
            self._configure_tool_handlers(
                registry, conversation_id=conv_id, user_id=user_id,
            )
        # Find handler
        handler = None
        for h in registry.list_tools():
            if h.name == tool_name:
                handler = h
                break
        if not handler:
            flowfile.set_content(json.dumps({
                "error": f"Tool '{tool_name}' not found",
            }).encode())
            return [flowfile]
        # Map positional args to named params using schema
        if positional:
            schema = handler.parameters_schema or {}
            param_names = list((schema.get("properties") or {}).keys())
            for i, val in enumerate(positional):
                if i < len(param_names):
                    key = param_names[i]
                    if key not in tool_args:
                        tool_args[key] = val
                else:
                    flowfile.set_content(json.dumps({
                        "error": (
                            f"Too many positional arguments ({len(positional)}) "
                            f"for tool '{tool_name}' which has "
                            f"{len(param_names)} parameters: {param_names}"
                        ),
                    }).encode())
                    return [flowfile]
        # Execute in background thread - publish SSE events + persist
        # exactly like the agent streaming loop does
        _call_registry = registry
        _call_tool_name = tool_name
        _call_tool_args = tool_args
        _call_conv_id = conv_id
        _call_user_id = user_id

        def _run_user_tool_call():
            source = {"type": "user", "name": _call_user_id}
            import uuid as _uuid
            from core.llm_client import stamp_message
            from core.conversation_writer import ConversationWriter
            tc_id = _uuid.uuid4().hex[:12]
            # Build tool_call message now (SSE fires AFTER persist)
            _tc_msg = stamp_message({
                "role": "assistant", "content": "",
                "source": source,
                "tool_calls": [{
                    "id": tc_id,
                    "name": _call_tool_name,
                    "arguments": _call_tool_args,
                }],
            }, _call_conv_id)
            _tc_sse = {
                "type": "tool_call",
                "data": {
                    "tool": _call_tool_name,
                    "arguments": _call_tool_args,
                    "agent_name": "user",
                    "llm_service": "",
                    "ts": time.time(),
                },
            }
            _call_writer = ConversationWriter.for_conversation(_call_conv_id) if _call_conv_id else None
            if _call_writer:
                _call_writer.enqueue_message(
                    _tc_msg, user_id=_call_user_id,
                    sse_events=[_tc_sse])
            else:
                # No conv: fire SSE directly (nothing to persist)
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    _call_conv_id, "tool_call", _tc_sse["data"])
            # Execute
            try:
                result_text = _call_registry.execute(
                    _call_tool_name, _call_tool_args,
                ) or ""
            except Exception as _te:
                result_text = f"Error: {_te}"
                logger.error("User /call tool '%s' failed: %s",
                             _call_tool_name, _te)
            _result_preview = (result_text or "")[:2000]
            _tr_msg = stamp_message({
                "role": "tool",
                "content": result_text,
                "tool_call_id": tc_id,
            }, _call_conv_id)
            _tr_sse = {
                "type": "tool_result",
                "data": {
                    "tool": _call_tool_name,
                    "result": _result_preview,
                    "agent_name": "user",
                    "llm_service": "",
                },
            }
            if _call_writer:
                _call_writer.enqueue_message(
                    _tr_msg, user_id=_call_user_id,
                    sse_events=[_tr_sse])
            else:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    _call_conv_id, "tool_result", _tr_sse["data"])

        thread = threading.Thread(
            target=_run_user_tool_call, daemon=True,
            name=f"user-call-{tool_name}",
        )
        thread.start()
        # Return ack immediately
        flowfile.set_content(json.dumps({
            "status": "accepted", "tool": tool_name,
        }).encode())
        return [flowfile]

    # User services

    return None
