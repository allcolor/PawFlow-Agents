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


def _execute_fs(relay_svc, action: str, arguments: dict) -> str:
    """Execute a filesystem action via the relay."""
    path = arguments.get("path", ".")
    try:
        result = relay_svc._request(action, path, **{
            k: v for k, v in arguments.items()
            if k not in ("action", "path")
        })
        if isinstance(result, dict):
            return json.dumps(result)
        return str(result) if result else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def _execute_tool(registry, name: str, arguments: dict) -> str:
    """Execute a PawFlow tool via the registry."""
    try:
        result = registry.execute(name, arguments)
        return str(result) if result else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def _respond(req_id, result=None, error=None):
    """Send a JSON-RPC 2.0 response."""
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def main():
    relay_svc = _get_relay_service()
    registry = _get_tool_registry()
    tools = _build_tools(registry, relay_svc)
    tool_names = {t["name"] for t in tools}

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

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pawflow-mcp-bridge", "version": "1.0"},
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _respond(req_id, {"tools": tools})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            if name == "filesystem" and relay_svc:
                action = args.get("action", "")
                result = _execute_fs(relay_svc, action, args)
            elif name in tool_names and registry:
                result = _execute_tool(registry, name, args)
            else:
                result = f"Error: unknown tool '{name}'"
            _respond(req_id, {
                "content": [{"type": "text", "text": result}],
                "isError": result.startswith("Error:"),
            })
        else:
            _respond(req_id, None,
                     error={"code": -32601, "message": f"Unknown method: {method}"})


if __name__ == "__main__":
    main()
