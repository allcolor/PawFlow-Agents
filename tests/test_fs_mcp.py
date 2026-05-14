"""Relay MCP proxy timeout invariants."""

import inspect

from tools import fs_mcp


def test_mcp_send_rpc_has_no_default_timeout():
    sig = inspect.signature(fs_mcp._mcp_send_rpc)

    assert sig.parameters["timeout"].default is None


def test_mcp_tool_call_does_not_add_implicit_timeout(monkeypatch):
    calls = []

    def _fake_send_rpc(server_id, method, params=None, timeout=None):
        calls.append({
            "server_id": server_id,
            "method": method,
            "params": params,
            "timeout": timeout,
        })
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(fs_mcp, "_mcp_send_rpc", _fake_send_rpc)

    result = fs_mcp.action_mcp_call("/tmp", "/tmp", {
        "server_id": "srv",
        "tool_name": "slow_tool",
        "arguments": {"x": 1},
    })

    assert result["result"] == "ok"
    assert calls == [{
        "server_id": "srv",
        "method": "tools/call",
        "params": {"name": "slow_tool", "arguments": {"x": 1}},
        "timeout": None,
    }]


def test_mcp_tool_call_preserves_explicit_timeout(monkeypatch):
    calls = []

    def _fake_send_rpc(server_id, method, params=None, timeout=None):
        calls.append(timeout)
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(fs_mcp, "_mcp_send_rpc", _fake_send_rpc)

    fs_mcp.action_mcp_call("/tmp", "/tmp", {
        "server_id": "srv",
        "tool_name": "slow_tool",
        "arguments": {},
        "timeout": 600,
    })

    assert calls == [600]


def test_mcp_start_does_not_add_implicit_initialize_timeout(monkeypatch):
    calls = []

    class _Pipe:
        def write(self, _data):
            return None

        def flush(self):
            return None

    class _Proc:
        stdin = _Pipe()
        stdout = _Pipe()
        stderr = _Pipe()

        def poll(self):
            return None

        def kill(self):
            return None

    def _fake_popen(*_args, **_kwargs):
        return _Proc()

    def _fake_send_rpc(server_id, method, params=None, timeout=None):
        calls.append({
            "server_id": server_id,
            "method": method,
            "timeout": timeout,
        })
        return {"serverInfo": {}, "capabilities": {}}

    monkeypatch.setattr(fs_mcp.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(fs_mcp, "_mcp_send_rpc", _fake_send_rpc)
    fs_mcp._mcp_servers.clear()

    try:
        result = fs_mcp.action_mcp_start("/tmp", "/tmp", {
            "server_id": "srv",
            "command": "server",
        })

        assert result["status"] == "started"
        assert calls == [{
            "server_id": "srv",
            "method": "initialize",
            "timeout": None,
        }]
    finally:
        fs_mcp._mcp_servers.clear()
