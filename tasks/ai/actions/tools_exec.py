"""AgentLoopTask actions — tools exec"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_tools_exec(self, action, body, store, user_id, flowfile):
    """Handle tools exec actions. Returns [flowfile] or None."""

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
            svc = find_fs_service(user_id, service_name)
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
        # Only fall back to broad agent-level cancel if the targeted
        # kill found no matching in-flight. Prevents killing sibling
        # tools when CC runs several in parallel.
        if not _targeted and conv_id:
            ToolRelayService.cancel_agent(conv_id, agent_name="")
        flowfile.set_content(json.dumps({"ok": True, "tc_id": tc_id}).encode())
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
        filename = body.get("filename", "")
        source = body.get("source", "")
        if not source:
            flowfile.set_content(json.dumps({"error": "Missing source code"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.dynamic_tool_store import DynamicToolStore
            result = DynamicToolStore.instance().install(user_id, filename, source)
            # Reset tool registry so new tool is picked up
            self._tool_registry = None
            flowfile.set_content(json.dumps({
                "installed": True, **result,
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
            from core.dynamic_tool_store import DynamicToolStore
            is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
            removed = DynamicToolStore.instance().uninstall(
                user_id, tool_name, is_admin=is_admin,
            )
            # Reset tool registry
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
            from core.dynamic_tool_store import DynamicToolStore
            is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
            tools = DynamicToolStore.instance().list_tools(
                user_id=user_id, is_admin=is_admin,
            )
            flowfile.set_content(json.dumps({
                "tools": tools,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # â”€â”€ User tool call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        # Execute in background thread â€” publish SSE events + persist
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

    # â”€â”€ User services â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    return None
