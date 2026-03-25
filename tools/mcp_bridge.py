#!/usr/bin/env python3
"""MCP Bridge — exposes PawFlow tools to Claude Code via MCP stdio protocol.

Runs on the PawFlow SERVER (not the relay). Routes tool calls:
- Filesystem tools → relay service (remote machine via WebSocket)
- PawFlow tools → tool registry (local, same process)

Launched as a subprocess by the claude-code LLM provider with env vars:
  PAWFLOW_RELAY_SERVICE: filesystem service ID to use for fs ops
  PAWFLOW_USER_ID: user ID for tool execution context
  PAWFLOW_CONVERSATION_ID: conversation ID for context
  PAWFLOW_AGENT_NAME: agent name

Uses JSON-RPC 2.0 over stdio (MCP standard).
"""

import json
import os
import sys


def _get_relay_service():
    """Get the filesystem relay service instance."""
    svc_id = os.environ.get("PAWFLOW_RELAY_SERVICE", "")
    if not svc_id:
        return None
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        svc = GlobalServiceRegistry.get_instance().get_live_instance(svc_id)
        if svc:
            return svc
        from gui.services.user_service_registry import UserServiceRegistry
        uid = os.environ.get("PAWFLOW_USER_ID", "")
        if uid:
            svc = UserServiceRegistry.get_instance().get_live_instance(uid, svc_id)
        return svc
    except Exception:
        return None


def _get_tool_registry():
    """Get the PawFlow tool registry."""
    try:
        from core.tool_registry import create_default_registry
        return create_default_registry()
    except Exception:
        return None


# Filesystem actions that go through the relay
FS_ACTIONS = {
    "list_dir", "read_file", "read_pdf", "read_notebook", "edit_notebook",
    "write_file", "edit", "batch_edit", "apply_patch",
    "delete_file", "mkdir", "stat", "exists", "search", "grep",
    "find_replace", "exec", "read_file_chunked", "read_chunk",
    "write_file_chunked",
    "git_status", "git_log", "git_diff", "git_commit", "git_pull",
    "git_push", "git_checkout", "git_add", "git_reset", "git_stash",
    "git_branch", "git_merge", "git_rebase", "git_cherry_pick",
    "git_tag", "git_blame", "project_init",
}


def _build_tools(registry, relay_svc) -> list:
    """Build MCP tools list from PawFlow registry + filesystem."""
    tools = []

    # Filesystem tool (compound — all fs actions via one tool)
    if relay_svc:
        tools.append({
            "name": "filesystem",
            "description": (
                "Access files and run commands on the user's filesystem. "
                "Actions: list_dir, read_file, write_file, edit, exec, "
                "delete_file, mkdir, stat, search, grep, git_status, git_diff, "
                "git_commit, and more. Use action parameter to specify the operation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "The operation to perform"},
                    "path": {"type": "string", "description": "File or directory path"},
                    "content": {"type": "string", "description": "Content for write operations"},
                    "command": {"type": "string", "description": "Shell command for exec"},
                    "old_string": {"type": "string", "description": "String to replace (edit)"},
                    "new_string": {"type": "string", "description": "Replacement string (edit)"},
                    "pattern": {"type": "string", "description": "Search/grep pattern"},
                    "offset": {"type": "integer", "description": "Line offset for pagination"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
                },
                "required": ["action"],
            },
        })

    # PawFlow tools from registry (excluding filesystem which we handle above)
    if registry:
        for handler in registry.list_tools():
            name = handler.name
            if name == "filesystem":
                continue  # handled above via relay
            tools.append({
                "name": name,
                "description": handler.description or "",
                "inputSchema": handler.parameters_schema or {
                    "type": "object", "properties": {}},
            })

    return tools


def _publish_event(event_type: str, data: dict):
    """Publish SSE event to the conversation (if context available)."""
    conv_id = os.environ.get("PAWFLOW_CONVERSATION_ID", "")
    if not conv_id:
        return
    try:
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(conv_id, event_type, data)
    except Exception:
        pass


def _execute_fs(relay_svc, action: str, arguments: dict) -> str:
    """Execute a filesystem action via the relay."""
    agent_name = os.environ.get("PAWFLOW_AGENT_NAME", "")
    path = arguments.get("path", ".")

    # Publish tool_call event
    _publish_event("tool_call", {
        "tool": f"filesystem.{action}",
        "arguments": arguments,
        "agent_name": agent_name,
        "via": "claude-code-mcp",
    })

    try:
        result = relay_svc._request(action, path, **{
            k: v for k, v in arguments.items()
            if k not in ("action", "path")
        })
        result_str = json.dumps(result) if isinstance(result, dict) else (str(result) if result else "(no output)")
    except Exception as e:
        result_str = f"Error: {e}"

    # Publish tool_result event
    _publish_event("tool_result", {
        "tool": f"filesystem.{action}",
        "result": result_str[:500],
        "agent_name": agent_name,
        "via": "claude-code-mcp",
    })
    return result_str


def _execute_tool(registry, name: str, arguments: dict) -> str:
    """Execute a PawFlow tool via the registry."""
    agent_name = os.environ.get("PAWFLOW_AGENT_NAME", "")

    _publish_event("tool_call", {
        "tool": name,
        "arguments": arguments,
        "agent_name": agent_name,
        "via": "claude-code-mcp",
    })

    try:
        result = registry.execute(name, arguments)
        result_str = str(result) if result else "(no output)"
    except Exception as e:
        result_str = f"Error: {e}"

    _publish_event("tool_result", {
        "tool": name,
        "result": result_str[:500],
        "agent_name": agent_name,
        "via": "claude-code-mcp",
    })
    return result_str


def _respond(req_id, result=None, error=None):
    """Send a JSON-RPC 2.0 response."""
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _log(msg):
    """Log to stderr (stdout is for MCP JSON-RPC)."""
    sys.stderr.write(f"[mcp-bridge] {msg}\n")
    sys.stderr.flush()


def main():
    relay_svc = _get_relay_service()
    registry = _get_tool_registry()
    tools = _build_tools(registry, relay_svc)
    tool_names = {t["name"] for t in tools}
    _log(f"Started: {len(tools)} tools, relay={'yes' if relay_svc else 'no'}, "
         f"registry={'yes' if registry else 'no'}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        _log(f"<< {method} (id={req_id})")

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pawflow-mcp-bridge", "version": "1.0"},
            })
        elif method == "notifications/initialized":
            _log("MCP initialized")
        elif method == "tools/list":
            _log(f"Returning {len(tools)} tools")
            _respond(req_id, {"tools": tools})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            _log(f"CALL {name}({json.dumps(args)[:200]})")
            if name == "filesystem" and relay_svc:
                action = args.get("action", "")
                result = _execute_fs(relay_svc, action, args)
            elif name in tool_names and registry:
                result = _execute_tool(registry, name, args)
            else:
                result = f"Error: unknown tool '{name}'"
            _log(f"RESULT {name}: {result[:100]}")
            _respond(req_id, {
                "content": [{"type": "text", "text": result}],
                "isError": result.startswith("Error:"),
            })
        else:
            _log(f"Unknown method: {method}")
            _respond(req_id, None,
                     error={"code": -32601, "message": f"Unknown method: {method}"})


if __name__ == "__main__":
    main()
