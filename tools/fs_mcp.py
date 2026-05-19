"""MCP stdio proxy actions for the filesystem relay.

Split from fs_actions.py — manages MCP servers as local subprocesses,
proxies JSON-RPC calls.
"""

import json
import os
import subprocess  # nosec B404
import threading
import uuid as _uuid
from typing import Any, Dict, Optional

# Active MCP server processes: {server_id: {"process", "stdin_lock", "pending"}}
_mcp_servers: Dict[str, Any] = {}
_mcp_lock = threading.Lock()


def _mcp_send_rpc(server_id: str, method: str, params: dict = None,
                  timeout: Optional[float] = None) -> dict:
    """Send a JSON-RPC 2.0 request to an MCP stdio server and wait for response."""
    with _mcp_lock:
        srv = _mcp_servers.get(server_id)
    if not srv or srv["process"].poll() is not None:
        raise RuntimeError(f"MCP server '{server_id}' not running")

    request_id = _uuid.uuid4().hex[:12]
    rpc = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        rpc["params"] = params

    proc = srv["process"]
    line = json.dumps(rpc) + "\n"

    with srv["stdin_lock"]:
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()

    # Read response lines until we get our request_id
    # MCP servers send one JSON-RPC response per line on stdout
    import select as _sel
    import time as _t
    deadline = _t.time() + timeout if timeout is not None else None
    stdout_fd = proc.stdout.fileno()
    while True:
        if proc.poll() is not None:
            # Read any remaining output before reporting death
            remaining = proc.stdout.read()
            stderr_out = proc.stderr.read() if proc.stderr else b""
            raise RuntimeError(
                f"MCP server '{server_id}' exited (code={proc.returncode}). "
                f"stderr: {stderr_out[:500]}")
        # Wait for data with timeout (cross-platform: use thread for Windows)
        select_timeout = 1.0
        if deadline is not None:
            remaining = deadline - _t.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"MCP server '{server_id}' did not respond within {timeout}s")
            select_timeout = min(remaining, 1.0)
        try:
            ready, _, _ = _sel.select([stdout_fd], [], [], select_timeout)
        except (ValueError, OSError):
            # On Windows, select doesn't work on pipes — use blocking read in thread
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(1) as pool:
                future = pool.submit(proc.stdout.readline)
                try:
                    resp_line = future.result(timeout=1.0)
                except _cf.TimeoutError:
                    continue
            if resp_line:
                resp_line = resp_line.strip()
                if resp_line:
                    try:
                        resp = json.loads(resp_line)
                        if resp.get("id") == request_id:
                            if "error" in resp:
                                err = resp["error"]
                                raise RuntimeError(f"MCP error: {err.get('message', err)}")
                            return resp.get("result", {})
                    except json.JSONDecodeError:
                        pass
            continue
        if not ready:
            continue
        resp_line = proc.stdout.readline()
        if not resp_line:
            continue
        resp_line = resp_line.strip()
        if not resp_line:
            continue
        try:
            resp = json.loads(resp_line)
        except json.JSONDecodeError:
            continue
        if resp.get("id") == request_id:
            if "error" in resp:
                err = resp["error"]
                raise RuntimeError(f"MCP error: {err.get('message', err)}")
            return resp.get("result", {})
        # Not our response — could be a notification, skip

def action_mcp_start(root_dir, abs_path, req, **kwargs):
    """Start an MCP stdio server subprocess.

    req: {server_id, command, args?, env?, timeout?}
    """
    server_id = req.get("server_id", "")
    command = req.get("command", "")
    args = req.get("args", [])
    env_extra = req.get("env", {})
    if not server_id or not command:
        raise ValueError("server_id and command are required")

    with _mcp_lock:
        if server_id in _mcp_servers:
            p = _mcp_servers[server_id]["process"]
            if p.poll() is None:
                return {"status": "already_running", "server_id": server_id}
            # Dead — clean up
            _mcp_servers.pop(server_id, None)

    # Build environment
    env = os.environ.copy()
    env.update(env_extra)

    # Launch subprocess
    cmd = [command] + (args if isinstance(args, list) else [args])
    proc = subprocess.Popen(  # nosec B603
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=root_dir,
    )

    srv = {
        "process": proc,
        "stdin_lock": threading.Lock(),
        "command": command,
        "args": args,
    }
    with _mcp_lock:
        _mcp_servers[server_id] = srv

    # Initialize: send initialize request
    try:
        init_result = _mcp_send_rpc(server_id, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pawflow-relay", "version": "1.0"},
        }, timeout=req.get("timeout"))

        # Send initialized notification (no response expected)
        notif = json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }) + "\n"
        with srv["stdin_lock"]:
            proc.stdin.write(notif.encode("utf-8"))
            proc.stdin.flush()

        return {
            "status": "started",
            "server_id": server_id,
            "server_info": init_result.get("serverInfo", {}),
            "capabilities": init_result.get("capabilities", {}),
        }
    except Exception as e:
        # Startup failed — kill
        proc.kill()
        with _mcp_lock:
            _mcp_servers.pop(server_id, None)
        raise RuntimeError(f"MCP server init failed: {e}")


def action_mcp_discover(root_dir, abs_path, req, **kwargs):
    """Discover tools from a running MCP stdio server.

    req: {server_id}
    Returns: list of tools with name, description, inputSchema
    """
    server_id = req.get("server_id", "")
    if not server_id:
        raise ValueError("server_id is required")

    result = _mcp_send_rpc(server_id, "tools/list", {})
    tools = result.get("tools", [])
    return {
        "tools": [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {}),
            }
            for t in tools
        ],
        "server_id": server_id,
    }


def action_mcp_call(root_dir, abs_path, req, **kwargs):
    """Call a tool on a running MCP stdio server.

    req: {server_id, tool_name, arguments, timeout?}
    """
    server_id = req.get("server_id", "")
    tool_name = req.get("tool_name", "")
    arguments = req.get("arguments", {})
    if not server_id or not tool_name:
        raise ValueError("server_id and tool_name are required")

    result = _mcp_send_rpc(server_id, "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    }, timeout=req.get("timeout"))
    # MCP returns content array
    content = result.get("content", [])
    # Flatten text content
    text_parts = []
    for item in content:
        if item.get("type") == "text":
            text_parts.append(item.get("text", ""))
        elif item.get("type") == "image":
            text_parts.append(f"[image: {item.get('mimeType', 'image/*')}]")
        else:
            text_parts.append(json.dumps(item))
    return {
        "result": "\n".join(text_parts),
        "content": content,
        "isError": result.get("isError", False),
    }


def action_mcp_stop(root_dir, abs_path, req, **kwargs):
    """Stop a running MCP stdio server.

    req: {server_id}
    """
    server_id = req.get("server_id", "")
    if not server_id:
        raise ValueError("server_id is required")

    with _mcp_lock:
        srv = _mcp_servers.pop(server_id, None)
    if not srv:
        return {"status": "not_running", "server_id": server_id}

    proc = srv["process"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"status": "stopped", "server_id": server_id}


def action_mcp_list(root_dir, abs_path, req, **kwargs):
    """List all running MCP stdio servers."""
    with _mcp_lock:
        result = []
        dead = []
        for sid, srv in _mcp_servers.items():
            alive = srv["process"].poll() is None
            if not alive:
                dead.append(sid)
            result.append({
                "server_id": sid,
                "command": srv.get("command", ""),
                "alive": alive,
            })
        for sid in dead:
            _mcp_servers.pop(sid, None)
    return {"servers": result}
