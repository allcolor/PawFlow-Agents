"""Published-conversation MCP server tests."""

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from core.mcp_server_store import MCPServerStore
from services import mcp_server_endpoint as endpoint


class _Request:
    def __init__(self, body=None, server_id="srv_test", headers=None):
        self.body = json.dumps(body).encode("utf-8") if body is not None else b""
        self.path_params = {"server_id": server_id}
        self.headers = headers or {}
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


def _decoded(request):
    status, headers, body = request.completed
    return status, headers, json.loads(body.decode("utf-8")) if body else None


def test_store_hashes_keys_and_supports_revocation(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    assert server["image_output"] == "native"
    server = store.configure(
        "alice", "conv-1", "agent-a", image_output="describe")
    assert server["image_output"] == "describe"
    with pytest.raises(ValueError, match="image_output"):
        store.configure("alice", "conv-1", "agent-a", image_output="automatic")
    raw, key = store.create_key(server["server_id"], "Codex")

    assert raw.startswith("pfmcp_")
    assert store.validate_key(server["server_id"], raw)["key_id"] == key["key_id"]
    assert store.validate_key(server["server_id"], "wrong") is None

    with sqlite3.connect(store.database_path) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM mcp_api_keys WHERE key_id = ?", (key["key_id"],)
        ).fetchone()[0]
    assert stored == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert raw not in stored

    assert store.revoke_key(server["server_id"], key["key_id"])
    assert store.validate_key(server["server_id"], raw) is None


def test_store_enforces_one_fresh_cli_and_expires_stale_lease(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    server_id = server["server_id"]

    store.claim_client(server_id, "cli-a", "Codex", "relay-a")
    store.claim_client(server_id, "cli-a", "Codex", "relay-a")
    with pytest.raises(RuntimeError, match="active CLI"):
        store.claim_client(server_id, "cli-b", "Claude", "relay-a")

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE mcp_servers SET client_heartbeat_at = 1 WHERE server_id = ?",
            (server_id,),
        )
        connection.commit()

    expired = store.expire_stale_clients()
    assert [row["server_id"] for row in expired] == [server_id]
    current = store.get(server_id)
    assert current["active_client_id"] == ""
    assert not current["client_active"]


def test_store_migrates_existing_publications_to_native_image_output(tmp_path):
    database = tmp_path / "published.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE mcp_servers (
                   server_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                   conversation_id TEXT NOT NULL UNIQUE, agent_name TEXT NOT NULL,
                   label TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                   created_at REAL NOT NULL, updated_at REAL NOT NULL,
                   active_client_id TEXT NOT NULL DEFAULT '',
                   active_client_name TEXT NOT NULL DEFAULT '',
                   active_relay_id TEXT NOT NULL DEFAULT '',
                   client_heartbeat_at REAL NOT NULL DEFAULT 0
               )"""
        )
        connection.execute(
            """INSERT INTO mcp_servers (
                   server_id, owner_user_id, conversation_id, agent_name,
                   label, enabled, created_at, updated_at)
               VALUES ('srv-old', 'alice', 'conv-old', 'agent-a',
                       'Old server', 1, 1, 1)"""
        )

    store = MCPServerStore(database)

    assert store.get("srv-old")["image_output"] == "native"


def test_link_relay_can_skip_automatic_default(monkeypatch):
    from core import relay_bindings

    class Store:
        def __init__(self):
            self.extra = {}

        def get_extra_cached(self, _cid, _key, default=None):
            return self.extra or default

        def set_extra(self, _cid, _key, value):
            self.extra = value

    store = Store()
    monkeypatch.setattr(relay_bindings, "_get_store", lambda: store)
    monkeypatch.setattr(
        relay_bindings, "_invalidate_cli_after_mount_change", lambda *_args: None
    )

    assert relay_bindings.link_relay(
        "conv-1", "mcp-cli", agent="agent-a", auto_default=False
    )
    assert relay_bindings.get_linked("conv-1", "agent-a") == ["mcp-cli"]
    assert relay_bindings.get_default("conv-1", "agent-a") is None


def test_mcp_initialize_creates_scoped_session(monkeypatch):
    server = {
        "server_id": "srv_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    key = {"key_id": "key-1", "label": "Codex"}
    monkeypatch.setattr(endpoint, "_authenticate", lambda _req, _sid: (server, key))
    endpoint._sessions.clear()

    request = _Request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    endpoint.handle_mcp_post(request)
    status, headers, payload = _decoded(request)

    assert status == 200
    assert payload["result"]["capabilities"] == {"tools": {}}
    assert payload["result"]["protocolVersion"] == "2025-03-26"
    assert headers["Mcp-Session-Id"] in endpoint._sessions


def test_mcp_origin_must_match_forwarded_authority():
    allowed = _Request(headers={
        "Origin": "https://pawflow.example",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "pawflow.example",
    })
    rejected = _Request(headers={
        "Origin": "https://attacker.example",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "pawflow.example",
    })

    assert endpoint._origin_allowed(allowed)
    assert not endpoint._origin_allowed(rejected)


def test_mcp_missing_session_is_400_and_batch_returns_only_request_results(monkeypatch):
    server = {
        "server_id": "srv_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    key = {"key_id": "key-1", "label": "Codex"}
    monkeypatch.setattr(endpoint, "_authenticate", lambda _req, _sid: (server, key))
    endpoint._sessions.clear()

    missing = _Request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    endpoint.handle_mcp_post(missing)
    assert _decoded(missing)[0] == 400

    session_id = endpoint._new_session(server, key, endpoint._PROTOCOL_VERSION)
    batch = _Request([
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ], headers={"Mcp-Session-Id": session_id})
    endpoint.handle_mcp_post(batch)
    status, headers, payload = _decoded(batch)

    assert status == 200
    assert headers["Mcp-Session-Id"] == session_id
    assert [item["id"] for item in payload] == [2, 3]
    assert payload[1]["result"]["tools"] == endpoint._MCP_TOOLS


def test_use_tool_runs_through_canonical_owner_agent_runtime(monkeypatch):
    calls = {}

    class Registry:
        def get_tool_definitions(self):
            return [{"name": "read", "description": "Read", "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, request_id, name, arguments, user_id, conv_id, agent):
            calls.update({
                "request_id": request_id,
                "name": name,
                "arguments": arguments,
                "user_id": user_id,
                "conversation_id": conv_id,
                "agent_name": agent,
            })
            return {"type": "result", "request_id": request_id, "data": "ok"}

    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)

    result = endpoint._call_tool(server, {"key_id": "key-1"}, "use_tool", {
        "tool_name": "read",
        "arguments_json": json.dumps({"path": "README.md"}),
    })

    assert result == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert calls["user_id"] == "alice"
    assert calls["conversation_id"] == "conv-1"
    assert calls["agent_name"] == "agent-a"
    assert calls["name"] == "read"


@pytest.mark.parametrize("tool_name", ["delegate", "flash_delegate"])
def test_async_mcp_tool_returns_final_result_to_external_caller(
        monkeypatch, tool_name):
    from core import external_call_router

    external_call_router.reset_for_tests()
    seen = {}

    class Registry:
        def get_tool_definitions(self):
            return [{"name": tool_name, "description": tool_name,
                     "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, _request_id, name, arguments,
                        _user_id, _conv_id, agent):
            owner = external_call_router.current_owner()
            task_id = arguments["tasks"][0]["id"]
            seen.update(owner=owner, arguments=arguments, agent=agent)
            assert external_call_router.complete_task(task_id, {
                "task_id": task_id,
                "agent": agent,
                "status": "completed",
                "response": "final answer for Claude Code",
                "error": "",
            })
            if name == "flash_delegate":
                return {"data": json.dumps({
                    "status": "spawned",
                    "flash_agents": [{"task_id": task_id}],
                })}
            return {"data": json.dumps([{
                "task_id": task_id, "status": "delivered",
            }])}

    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
    }
    task = (
        {"name": "audit", "prompt": "Audit.", "message": "Check it"}
        if tool_name == "flash_delegate"
        else {"agent": "agent-a", "message": "Check it"}
    )
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(endpoint, "_persist_tool_call_start", lambda *_args: None)
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args: {"llm_service": "configured-service"})

    result = endpoint._call_tool(
        server, {"key_id": "key-1", "label": "Claude Code"},
        "use_tool", {
            "tool_name": tool_name,
            "arguments_json": json.dumps({"tasks": [task]}),
        },
        session_id="session-1", mcp_request_id=7,
    )

    payload = json.loads(result["content"][0]["text"])
    assert payload["task_results"][0]["response"] == (
        "final answer for Claude Code")
    assert seen["agent"] == "agent-a"
    assert seen["owner"]["transport"] == "published_mcp"
    assert seen["owner"]["source_id"].startswith("published_mcp_")
    assert seen["owner"]["source_id"] != "agent-a"
    assert seen["owner"]["llm_service"] == "configured-service"
    assert seen["arguments"]["tasks"][0]["id"].startswith("pmcp_")


def test_backgrounded_published_mcp_tool_waits_for_late_result(monkeypatch):
    from core import external_call_router

    external_call_router.reset_for_tests()

    class Registry:
        def get_tool_definitions(self):
            return [{"name": "slow", "description": "Slow",
                     "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _handle_execute(self, request_id, name, arguments,
                            user_id, conversation_id, agent_name, **kwargs):
            assert request_id.startswith("pmcp_")
            assert name == "slow"
            assert arguments == {"value": 1}
            assert (user_id, conversation_id, agent_name) == (
                "alice", "conv-1", "agent-a")
            kwargs["late_result_callback"]("late result for Claude Code", False)
            return {"data": (
                f"[Running in background (tc_id={request_id})]\n"
                "Continue your work.")}

    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(endpoint, "_persist_tool_call_start", lambda *_args: None)
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args: {"llm_service": "configured-service"})

    result = endpoint._call_tool(
        server, {"key_id": "key-1", "label": "Claude Code"},
        "use_tool", {
            "tool_name": "slow",
            "arguments_json": json.dumps({"value": 1}),
        },
        session_id="session-1", mcp_request_id=8,
    )

    assert result == {
        "content": [{"type": "text",
                     "text": "late result for Claude Code"}],
        "isError": False,
    }


def test_external_call_retry_reuses_retained_background_result():
    from core import external_call_router

    external_call_router.reset_for_tests()
    external_call_router.register_call(
        "pmcp_retry", "conv-1", "published_mcp_client",
        "Claude Code", "configured-service")
    assert external_call_router.complete_call(
        "pmcp_retry", "completed before retry")

    external_call_router.register_call(
        "pmcp_retry", "conv-1", "published_mcp_client",
        "Claude Code", "configured-service")

    assert external_call_router.wait_for_call_result(
        "pmcp_retry", timeout=0) == "completed before retry"


def test_native_image_stays_in_mcp_response_but_base64_is_not_persisted(monkeypatch):
    image_data = "c2Vuc2l0aXZlLWltYWdlLWJ5dGVz"
    persisted = {}

    class ImageHandler:
        _returns_images = True

    class Registry:
        def get_tool_definitions(self):
            return [{"name": "see", "description": "See", "parameters": {}}]

        def get(self, name):
            return ImageHandler() if name == "see" else None

    class Runtime:
        def _do_execute(self, request_id, name, arguments, user_id, conv_id, agent):
            return {"type": "result", "request_id": request_id, "data": [
                {"type": "text", "text": "Image: screen.png"},
                {"type": "image", "mimeType": "image/png", "data": image_data},
            ]}

    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "image_output": "native",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(
        endpoint, "_persist_tool_call",
        lambda _server, _key, _name, _args, text, _call_id:
            persisted.update(text=text),
    )

    result = endpoint._call_tool(server, {"key_id": "key-1"}, "use_tool", {
        "tool_name": "see", "arguments_json": json.dumps({"path": "screen.png"}),
    })

    assert result["content"][1]["data"] == image_data
    assert image_data not in persisted["text"]
    assert persisted["text"] == (
        "Image: screen.png\n[Image returned to MCP client: image/png]")


def test_describe_image_output_uses_published_agent_vision_and_persists_text(
        monkeypatch):
    image_data = "aW1hZ2UtYnl0ZXM="
    described_call = {}
    persisted = {}

    class ImageHandler:
        _returns_images = True

    class Registry:
        def get_tool_definitions(self):
            return []

        def get(self, name):
            return ImageHandler() if name == "see" else None

    class Runtime:
        def _do_execute(self, *args):
            return {"data": [
                {"type": "text", "text": "Image: screen.png"},
                {"type": "image", "mimeType": "image/png", "data": image_data},
            ]}

    def describe(result, **kwargs):
        described_call.update(result=result, **kwargs)
        return "Image: screen.png\n\nA terminal showing passing tests."

    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "image_output": "describe",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda _cid, _agent: {"llm_service": "agent-vision"},
    )
    monkeypatch.setattr("core.vision_describe.describe_tool_result_images", describe)
    monkeypatch.setattr(
        endpoint, "_persist_tool_call",
        lambda _server, _key, _name, _args, text, _call_id:
            persisted.update(text=text),
    )

    result = endpoint._call_tool(server, {"key_id": "key-1"}, "use_tool", {
        "tool_name": "see", "arguments_json": "{}",
    })

    assert result == {"content": [{"type": "text", "text":
        "Image: screen.png\n\nA terminal showing passing tests."}], "isError": False}
    assert described_call["agent_svc"] == "agent-vision"
    assert described_call["force"] is True
    assert described_call["user_id"] == "alice"
    assert image_data in described_call["result"]
    assert persisted["text"] == result["content"][0]["text"]
    assert image_data not in persisted["text"]


def test_remove_mcp_relay_unlinks_agent_binding_and_uninstalls(monkeypatch):
    from core import relay_bindings
    from core.service_registry import ServiceRegistry

    calls = []
    monkeypatch.setattr(
        relay_bindings, "unlink_relay",
        lambda cid, rid, agent="": calls.append(("unlink", cid, rid, agent)),
    )

    class Registry:
        def uninstall(self, scope, scope_id, relay_id):
            calls.append(("uninstall", scope, scope_id, relay_id))

    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: Registry())
    endpoint.remove_mcp_relay({
        "server_id": "srv-1",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
        "active_relay_id": "relay-1",
    })

    assert calls == [
        ("unlink", "conv-1", "relay-1", "agent-a"),
        ("uninstall", "conv", "conv-1", "relay-1"),
    ]


def test_stdio_bridge_exposes_local_relay_tools_and_keeps_secrets_out_of_argv(
        monkeypatch, tmp_path):
    from pawflow_relay import mcp_stdio

    assert {tool["name"] for tool in mcp_stdio._LOCAL_TOOLS} == {
        "pawflow_relay_connect",
        "pawflow_relay_disconnect",
        "pawflow_relay_status",
        "pawflow_relay_reconnect",
    }
    calls = []

    class Bridge:
        gateway_key = "gateway-secret"

        def control(self, action, payload):
            calls.append((action, payload))
            if action == "connect":
                return {
                    "relay_id": "relay-1",
                    "ws_url": "wss://pawflow.example/ws/relay/relay-1",
                    "relay_token": "relay-secret",
                }
            if action == "status":
                return {"connected": True, "relay_id": "relay-1"}
            return {"ok": True}

    class Process:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.running = False

    launched = {}

    def popen(argv, **kwargs):
        launched.update({"argv": argv, **kwargs})
        return Process()

    monkeypatch.setattr(mcp_stdio.subprocess, "Popen", popen)
    controller = mcp_stdio.RelayController(
        Bridge(), tmp_path, "Codex", readonly=True, allow_exec=False)

    connected = controller.connect()
    assert connected["relay_id"] == "relay-1"
    assert "relay-secret" not in launched["argv"]
    assert str(tmp_path) not in launched["argv"]
    assert launched["env"]["PAWFLOW_RELAY_TOKEN"] == "relay-secret"
    assert launched["env"]["PAWFLOW_RELAY_DIR"] == str(tmp_path)
    assert controller.status()["connected"]
    assert controller.disconnect()["connected"] is False
    assert calls[-1] == ("disconnect", {
        "client_id": controller.client_id,
        "release_client": False,
    })
    controller.close()
    assert calls[-1] == ("disconnect", {
        "client_id": controller.client_id,
        "release_client": True,
    })


def test_stdio_bridge_rolls_back_client_lease_when_relay_process_fails(
        monkeypatch, tmp_path):
    from pawflow_relay import mcp_stdio

    calls = []

    class Bridge:
        gateway_key = ""

        def control(self, action, payload):
            calls.append((action, payload))
            if action == "connect":
                return {
                    "relay_id": "relay-1",
                    "ws_url": "wss://pawflow.example/ws/relay/relay-1",
                    "relay_token": "relay-secret",
                }
            return {"ok": True}

    monkeypatch.setattr(
        mcp_stdio.subprocess, "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    controller = mcp_stdio.RelayController(Bridge(), tmp_path, "Codex")

    with pytest.raises(OSError, match="spawn failed"):
        controller.connect()
    assert calls[-1] == ("disconnect", {
        "client_id": controller.client_id,
        "release_client": True,
    })


def test_stdio_bridge_accepts_json_rpc_batches(monkeypatch, tmp_path):
    from pawflow_relay import mcp_stdio

    class Bridge:
        def rpc(self, message):
            if message["method"] == "tools/list":
                return 200, {"jsonrpc": "2.0", "id": message["id"],
                             "result": {"tools": [{"name": "remote"}]}}
            return 200, {"jsonrpc": "2.0", "id": message["id"], "result": {}}

    class Relay:
        root = tmp_path

        def connect(self):
            return {"relay_id": "relay-1"}

        def close(self):
            return {"connected": False}

    monkeypatch.setenv("PAWFLOW_MCP_API_KEY", "pfmcp_test")
    monkeypatch.setattr(mcp_stdio, "HTTPBridge", lambda *_args: Bridge())
    monkeypatch.setattr(mcp_stdio, "RelayController", lambda *_args, **_kwargs: Relay())
    monkeypatch.setattr(mcp_stdio.sys, "stdin", io.StringIO(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]) + "\n"))
    stdout = io.StringIO()
    monkeypatch.setattr(mcp_stdio.sys, "stdout", stdout)
    monkeypatch.setattr(mcp_stdio.sys, "stderr", io.StringIO())

    assert mcp_stdio.main(["--url", "https://pawflow.example/mcp/srv_test"]) == 0
    response = json.loads(stdout.getvalue())
    assert [item["id"] for item in response] == [1, 2]
    tool_names = {tool["name"] for tool in response[1]["result"]["tools"]}
    assert "remote" in tool_names
    assert "pawflow_relay_connect" in tool_names


def test_relay_disconnect_keeps_cli_lease_until_bridge_closes(monkeypatch):
    server = {
        "server_id": "srv_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
        "active_client_id": "cli-1",
        "active_relay_id": "relay-1",
    }
    releases = []
    removed = []

    class Store:
        def release_client(self, server_id, client_id):
            releases.append((server_id, client_id))
            return True

    monkeypatch.setattr(endpoint, "_authenticate", lambda _req, _sid: (server, {}))
    monkeypatch.setattr(MCPServerStore, "instance", classmethod(lambda cls: Store()))
    monkeypatch.setattr(endpoint, "remove_mcp_relay", lambda value: removed.append(value))

    disconnect = _Request({"client_id": "cli-1"})
    endpoint.handle_relay_disconnect(disconnect)
    status, _headers, payload = _decoded(disconnect)
    assert status == 200
    assert payload["client_active"] is True
    assert not releases

    close = _Request({"client_id": "cli-1", "release_client": True})
    endpoint.handle_relay_disconnect(close)
    status, _headers, payload = _decoded(close)
    assert status == 200
    assert payload["client_active"] is False
    assert releases == [("srv_test", "cli-1")]
    assert removed == [server, server]


def test_reconfiguring_published_agent_releases_old_cli_relay(monkeypatch):
    from tasks.ai.actions import _agentres_k6

    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
        "active_client_id": "cli-1",
        "active_relay_id": "relay-1",
    }
    calls = []

    class ConversationStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    class Store:
        def get_for_conversation(self, _conversation_id):
            return dict(server)

        def release_client(self, server_id, client_id):
            calls.append(("release", server_id, client_id))
            return True

        def configure(self, owner, conversation_id, agent_name, label="", enabled=True,
                      image_output="native"):
            calls.append(("configure", owner, conversation_id, agent_name, enabled))
            return dict(server, agent_name=agent_name, enabled=enabled,
                        image_output=image_output)

        def list_keys(self, _server_id):
            return []

    class FlowFile:
        def set_content(self, content):
            self.content = content

        def set_attribute(self, _key, _value):
            pass

    mcp_store = Store()
    monkeypatch.setattr(MCPServerStore, "instance", classmethod(lambda cls: mcp_store))
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs",
        lambda _conversation_id: {"agent-a": {}, "agent-b": {}},
    )
    monkeypatch.setattr(
        endpoint, "remove_mcp_relay",
        lambda value: calls.append(("remove", value["active_relay_id"])),
    )
    monkeypatch.setattr(endpoint, "ensure_mcp_routes", lambda: None)

    _agentres_k6._handle_agentres_k6(
        None, "mcp_server_configure",
        {"conversation_id": "conv-1", "agent_name": "agent-b", "enabled": True},
        ConversationStore(), "alice", FlowFile(),
    )

    assert calls[:2] == [
        ("remove", "relay-1"),
        ("release", "srv-1", "cli-1"),
    ]
    assert calls[2] == ("configure", "alice", "conv-1", "agent-b", True)


def test_configure_rejects_unknown_image_output(monkeypatch):
    from tasks.ai.actions import _agentres_k6

    class ConversationStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    class Store:
        def get_for_conversation(self, _conversation_id):
            return None

    class FlowFile:
        def __init__(self):
            self.attributes = {}

        def set_content(self, content):
            self.content = content

        def set_attribute(self, key, value):
            self.attributes[key] = value

    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: Store()))
    flowfile = FlowFile()

    _agentres_k6._handle_agentres_k6(
        None, "mcp_server_configure", {
            "conversation_id": "conv-1", "agent_name": "agent-a",
            "image_output": "auto",
        }, ConversationStore(), "alice", flowfile,
    )

    assert flowfile.attributes["http.response.status"] == "400"
    assert json.loads(flowfile.content)["error"] == (
        "image_output must be 'native' or 'describe'")


def test_only_first_http_listener_restores_published_mcp_routes(monkeypatch):
    from services import http_listener_service as listener_module

    class Store:
        @staticmethod
        def has_servers():
            return True

    registered = []
    monkeypatch.setattr(MCPServerStore, "instance", classmethod(lambda cls: Store()))
    monkeypatch.setattr(
        endpoint, "register_mcp_routes",
        lambda listener: registered.append(listener.port),
    )
    with listener_module._instances_lock:
        listener_module._instances.clear()
    try:
        listener_module.HTTPListenerService({"host": "127.0.0.1", "port": 19771})
        listener_module.HTTPListenerService({"host": "127.0.0.1", "port": 19772})
        assert registered == [19771]
    finally:
        with listener_module._instances_lock:
            listener_module._instances.clear()


def test_published_mcp_ui_is_loaded_and_translated():
    root = Path(__file__).parents[1]
    serve = (root / "tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    render = (root / "tasks/io/chat_ui/resources_render.js").read_text(encoding="utf-8")
    module = root / "tasks/io/chat_ui/resources_mcp_publish.js"

    assert '"resources_mcp_publish.js"' in serve
    assert "showPublishedMcpDialog()" in render
    assert module.exists()
    source = module.read_text(encoding="utf-8")
    assert "t('close')" in source
    assert "contextClose" not in source

    for language in ("en", "fr", "es"):
        catalog = json.loads(
            (root / f"tasks/io/chat_ui/i18n/{language}.json").read_text(encoding="utf-8")
        )
        assert catalog["mcpPublishConfigure"]
        assert catalog["mcpPublishDisconnectClient"]
        assert catalog["mcpPublishKeyOnce"]
        assert catalog["mcpRelayBadge"]
