"""Published-conversation MCP server tests."""

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from core.mcp_server_store import CLIENT_LEASE_TTL_SECONDS, MCPServerStore
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


def _corrupt_sqlite_schema_format(database: Path) -> bytes:
    """Overwrite only SQLite's schema-format header field with an invalid value."""
    content = bytearray(database.read_bytes())
    assert content[:16] == b"SQLite format 3\x00"
    content[44:48] = (0x7E6F4E6D).to_bytes(4, "big")
    database.write_bytes(content)
    return bytes(content)


def test_corrupt_store_is_preserved_and_mcp_fails_closed(tmp_path, caplog):
    database = tmp_path / "published.sqlite3"
    healthy = MCPServerStore(database)
    server = healthy.configure("alice", "conv-1", "agent-a")
    raw_key, _key = healthy.create_key(server["server_id"], "Codex")
    corrupted = _corrupt_sqlite_schema_format(database)
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    wal_content = b"preserved WAL evidence"
    shm_content = b"preserved SHM evidence"
    wal.write_bytes(wal_content)
    shm.write_bytes(shm_content)
    digest = hashlib.sha256(corrupted).hexdigest()
    wal_digest = hashlib.sha256(wal_content).hexdigest()
    shm_digest = hashlib.sha256(shm_content).hexdigest()

    with caplog.at_level("CRITICAL"):
        store = MCPServerStore(database)
        assert store.available is False
        assert store.has_servers() is False
        assert store.validate_key(server["server_id"], raw_key) is None
        assert store.expire_stale_clients() == []

    assert database.read_bytes() == corrupted
    assert wal.read_bytes() == wal_content
    assert shm.read_bytes() == shm_content
    critical = [
        record.getMessage() for record in caplog.records
        if record.levelname == "CRITICAL"
    ]
    assert len(critical) == 1
    assert "MCP store unavailable/corrupt" in critical[0]
    assert "MCP publication disabled" in critical[0]
    assert f"Database preserved at {database}" in critical[0]
    assert digest in critical[0]
    assert wal_digest in critical[0]
    assert shm_digest in critical[0]

    with pytest.raises(RuntimeError, match="MCP publication store is unavailable"):
        store.list_for_conversation("conv-1")


def test_corrupt_mcp_store_does_not_block_http_listener_restore(
        tmp_path, monkeypatch):
    from core.a2a_store import A2AStore
    from services import http_listener_service as listener_module

    database = tmp_path / "published.sqlite3"
    MCPServerStore(database).configure("alice", "conv-1", "agent-a")
    corrupted = _corrupt_sqlite_schema_format(database)
    store = MCPServerStore(database)

    class EmptyA2AStore:
        @staticmethod
        def has_publications():
            return False

    registered = []
    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: store))
    monkeypatch.setattr(
        A2AStore, "instance", classmethod(lambda cls: EmptyA2AStore()))
    monkeypatch.setattr(
        endpoint, "register_mcp_routes",
        lambda listener: registered.append(listener.port))
    with listener_module._instances_lock:
        listener_module._instances.clear()
    try:
        listener = listener_module.HTTPListenerService({
            "host": "127.0.0.1", "port": 19773,
        })
        assert listener.port == 19773
        assert registered == []
        assert database.read_bytes() == corrupted
    finally:
        with listener_module._instances_lock:
            listener_module._instances.clear()


def test_empty_existing_store_is_preserved_instead_of_reinitialized(tmp_path):
    database = tmp_path / "published.sqlite3"
    database.touch()

    store = MCPServerStore(database)

    assert store.available is False
    assert store.has_servers() is False
    assert database.read_bytes() == b""


def test_runtime_corruption_trips_store_once_and_stops_lease_sweeps(
        tmp_path, caplog):
    database = tmp_path / "published.sqlite3"
    store = MCPServerStore(database)
    store.configure("alice", "conv-1", "agent-a")
    corrupted = _corrupt_sqlite_schema_format(database)

    with caplog.at_level("CRITICAL"):
        assert store.expire_stale_clients() == []
        assert store.available is False
        assert store.expire_stale_clients() == []

    assert database.read_bytes() == corrupted
    critical = [
        record for record in caplog.records if record.levelname == "CRITICAL"
    ]
    assert len(critical) == 1
    assert "unsupported file format" in critical[0].getMessage()


def test_unavailable_store_disables_routes_and_lease_sweep(monkeypatch):
    class UnavailableStore:
        available = False

        @staticmethod
        def expire_stale_clients():
            raise AssertionError("an unavailable store must not be queried")

    class Listener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return self.routes

        def register_route(self, method, pattern, owner, **kwargs):
            self.routes.append((method, pattern, owner, kwargs))

    sweeper_starts = []
    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: UnavailableStore()))
    monkeypatch.setattr(
        endpoint, "_start_lease_sweeper", lambda: sweeper_starts.append(True))

    listener = Listener()
    endpoint.register_mcp_routes(listener)
    assert listener.routes == []
    assert sweeper_starts == []
    assert endpoint.sweep_expired_mcp_relays() == 0


def test_unavailable_store_returns_503_at_mcp_boundaries(monkeypatch):
    from tasks.ai.actions import _agentres_k6

    class UnavailableStore:
        available = False

    class ConversationStore:
        @staticmethod
        def resolve_owner(_conversation_id):
            return "alice"

    class FlowFile:
        def __init__(self):
            self.attributes = {}

        def set_content(self, content):
            self.content = content

        def set_attribute(self, key, value):
            self.attributes[key] = value

    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: UnavailableStore()))

    request = _Request(server_id="srv_test")
    assert endpoint._authenticate(request, "srv_test") == (None, None)
    assert _decoded(request)[0] == 503

    flowfile = FlowFile()
    _agentres_k6._handle_agentres_k6(
        None, "mcp_server_get", {"conversation_id": "conv-1"},
        ConversationStore(), "alice", flowfile,
    )
    assert flowfile.attributes["http.response.status"] == "503"
    assert json.loads(flowfile.content) == {
        "error": "MCP publication store is unavailable",
    }


def test_integrity_error_does_not_disable_healthy_store(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")

    with pytest.raises(sqlite3.IntegrityError):
        with store._lock, store._connection() as connection:
            connection.execute(
                """INSERT INTO mcp_servers (
                       server_id, owner_user_id, conversation_id, agent_name,
                       label, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (server["server_id"], "alice", "conv-2", "agent-b",
                 "duplicate", 1, 1),
            )

    assert store.available is True
    assert store.has_servers() is True


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


def test_list_keys_hides_revoked_by_default(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    server_id = server["server_id"]
    _, keep = store.create_key(server_id, "live")
    _, drop = store.create_key(server_id, "doomed")

    store.revoke_key(server_id, drop["key_id"])

    # A revoked key can never authenticate again, so it must disappear from
    # the publication dialog instead of accumulating forever.
    listed = {k["key_id"] for k in store.list_keys(server_id)}
    assert listed == {keep["key_id"]}
    # The row is retained as an audit trail and reachable on request.
    all_ids = {k["key_id"] for k in store.list_keys(server_id, include_revoked=True)}
    assert all_ids == {keep["key_id"], drop["key_id"]}


def test_store_supports_independent_agents_in_one_conversation(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    first = store.configure("alice", "conv-1", "agent-a")
    second = store.configure("alice", "conv-1", "agent-b")

    assert first["server_id"] != second["server_id"]
    assert [item["agent_name"] for item in
            store.list_for_conversation("conv-1")] == ["agent-a", "agent-b"]
    assert store.get_for_conversation(
        "conv-1", "AGENT-B")["server_id"] == second["server_id"]

    updated = store.configure(
        "alice", "conv-1", "Agent-A", mode="full")
    assert updated["server_id"] == first["server_id"]
    assert updated["mode"] == "full"
    assert len(store.list_for_conversation("conv-1")) == 2

    raw, _key = store.create_key(first["server_id"], "first")
    assert store.validate_key(first["server_id"], raw)
    assert store.validate_key(second["server_id"], raw) is None


def test_management_api_addresses_each_agent_publication(
        monkeypatch, tmp_path):
    from tasks.ai.actions import _agentres_k6

    mcp_store = MCPServerStore(tmp_path / "published.sqlite3")

    class ConversationStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    class FlowFile:
        def __init__(self):
            self.attributes = {}
            self.content = b""

        def set_content(self, content):
            self.content = content

        def set_attribute(self, key, value):
            self.attributes[key] = value

    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: mcp_store))
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs",
        lambda _conversation_id: {"agent-a": {}, "agent-b": {}})
    monkeypatch.setattr(
        "core.conv_agent_config.set_agent_config", lambda *_args: None)
    monkeypatch.setattr(endpoint, "ensure_mcp_routes", lambda: None)

    configured = []
    for agent in ("agent-a", "agent-b"):
        flowfile = FlowFile()
        _agentres_k6._handle_agentres_k6(
            None, "mcp_server_configure", {
                "conversation_id": "conv-1", "agent_name": agent,
            }, ConversationStore(), "alice", flowfile)
        configured.append(json.loads(flowfile.content)["server"])

    listed = FlowFile()
    _agentres_k6._handle_agentres_k6(
        None, "mcp_server_get", {"conversation_id": "conv-1"},
        ConversationStore(), "alice", listed)
    payload = json.loads(listed.content)
    assert {item["agent_name"] for item in payload["servers"]} == {
        "agent-a", "agent-b"}
    assert payload["server"] is None

    keyed = FlowFile()
    _agentres_k6._handle_agentres_k6(
        None, "mcp_server_create_key", {
            "conversation_id": "conv-1",
            "server_id": configured[1]["server_id"],
            "label": "agent-b-key",
        }, ConversationStore(), "alice", keyed)
    assert json.loads(keyed.content)["key"]["server_id"] == (
        configured[1]["server_id"])
    assert not mcp_store.list_keys(configured[0]["server_id"])


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


def test_idle_lease_sweep_does_not_take_a_write_lock(tmp_path, monkeypatch):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    store.configure("alice", "conv-1", "agent-a")
    statements = []
    original_connect = store._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced_connect)

    assert store.has_client_leases() is False
    assert store.expire_stale_clients() == []
    assert not any(
        statement.upper().startswith("BEGIN IMMEDIATE")
        for statement in statements
    )


def test_lease_sweep_interval_matches_the_heartbeat_ttl():
    assert CLIENT_LEASE_TTL_SECONDS == 120.0
    assert endpoint._LEASE_SWEEP_INTERVAL_SECONDS == CLIENT_LEASE_TTL_SECONDS


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
    second = store.configure("alice", "conv-old", "agent-b")
    assert second["server_id"] != "srv-old"
    assert len(store.list_for_conversation("conv-old")) == 2


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


@pytest.mark.parametrize(
    ("requested", "negotiated"),
    [
        ("2025-03-26", "2025-03-26"),
        ("2025-11-25", "2025-11-25"),
        ("2099-01-01", "2025-11-25"),
    ],
)
def test_mcp_initialize_creates_scoped_session(
        monkeypatch, requested, negotiated):
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
        "params": {"protocolVersion": requested},
    })
    endpoint.handle_mcp_post(request)
    status, headers, payload = _decoded(request)

    assert status == 200
    assert payload["result"]["capabilities"] == {"tools": {}}
    assert payload["result"]["protocolVersion"] == negotiated
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


def test_published_mcp_conversation_context_and_idempotent_messages(monkeypatch):
    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    key = {"key_id": "key-1", "label": "Codex"}

    class Store:
        def __init__(self):
            self.rows = [
                {"role": "user", "content": "first", "msg_id": "m1", "seq": 3},
                {"role": "assistant", "content": "second", "msg_id": "m2", "seq": 7},
            ]

        def load_agent_context(self, _cid, _agent):
            return list(self.rows)

        def load_transcript_for_agent(self, _cid, _agent):
            raise AssertionError("agent context should be preferred")

        def append_message_if_absent(self, _cid, message, **_kwargs):
            if any(row["msg_id"] == message["msg_id"] for row in self.rows):
                return False
            stored = dict(message)
            stored["seq"] = max(row["seq"] for row in self.rows) + 1
            self.rows.append(stored)
            return True

    class Writer:
        def __init__(self):
            self.events = []

        def enqueue_sse_events(self, events, wait=False):
            assert wait is True
            self.events.extend(events)

    store = Store()
    writer = Writer()
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance", lambda: store)
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: writer)

    class Task:
        system_prompt = "Published agent system prompt"

    monkeypatch.setattr(
        "core.agent_executor.resolve_agent_task",
        lambda *_args, **_kwargs: Task())

    initial = json.loads(endpoint._conversation_tool_result(
        server, key, "get_initial_context", {}))
    assert initial["cursor"] == 7
    assert "# PawFlow Initial Context" in initial["document"]
    assert "Published agent system prompt" in initial["document"]
    assert '"content": "second"' in initial["document"]

    updates = json.loads(endpoint._conversation_tool_result(
        server, key, "get_context_updates", {"after_seq": 3}))
    assert updates["cursor"] == 7
    assert [row["msg_id"] for row in updates["messages"]] == ["m2"]

    first = json.loads(endpoint._conversation_tool_result(
        server, key, "send_agent_message", {
            "content": "external response", "message_id": "external-1",
        }))
    retry = json.loads(endpoint._conversation_tool_result(
        server, key, "send_agent_message", {
            "content": "external response", "message_id": "external-1",
        }))
    assert first["accepted"] is True and first["duplicate"] is False
    assert retry["accepted"] is False and retry["duplicate"] is True
    assert len(writer.events) == 1
    assert writer.events[0]["data"]["msg_id"] == "external-1"


def test_every_builtin_tool_call_is_audited_in_conversation(monkeypatch):
    """tools/call rows must appear in the conversation like a normal agent's
    tool calls; context reads audit a compact summary, not the document."""
    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    key = {"key_id": "key-1", "label": "ChatGPT", "kind": "connector"}

    class Store:
        def load_agent_context(self, _cid, _agent):
            return [{"role": "user", "content": "first", "msg_id": "m1",
                     "seq": 3}]

    class Task:
        system_prompt = "Published agent system prompt"

    class Registry:
        def get_tool_definitions(self):
            return [{"name": "read", "description": "Read", "parameters": {}}]

        def get(self, _name):
            return None

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance", lambda: Store())
    monkeypatch.setattr(
        "core.agent_executor.resolve_agent_task",
        lambda *_args, **_kwargs: Task())
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    audits = []
    monkeypatch.setattr(
        endpoint, "_persist_tool_call_start",
        lambda _s, _k, name, args, call_id: audits.append(("start", name)))
    monkeypatch.setattr(
        endpoint, "_persist_tool_call",
        lambda _s, _k, name, args, result, call_id: audits.append(
            ("result", name, result)))

    endpoint._call_tool(server, key, "get_initial_context", {})
    endpoint._call_tool(server, key, "get_context_updates", {"after_seq": 0})
    endpoint._call_tool(server, key, "get_tool_schema", {"tool_name": "read"})

    assert [item[1] for item in audits if item[0] == "start"] == [
        "get_initial_context", "get_context_updates", "get_tool_schema"]
    initial = next(item[2] for item in audits
                   if item[0] == "result" and item[1] == "get_initial_context")
    assert "document_chars" in initial
    assert "PawFlow Initial Context" not in initial
    updates = next(item[2] for item in audits
                   if item[0] == "result" and item[1] == "get_context_updates")
    assert "new_messages" in updates
    schema = next(item[2] for item in audits
                  if item[0] == "result" and item[1] == "get_tool_schema")
    assert "read" in schema


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
    cleared = []

    class Store:
        def clear_terminal(self, server_id, client_id):
            cleared.append((server_id, client_id))
            return True

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
    assert cleared == [("srv_test", "cli-1"), ("srv_test", "cli-1")]
    assert removed == [server, server]


def test_configuring_second_published_agent_keeps_first_cli_relay(monkeypatch):
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
        def __init__(self):
            self.servers = [dict(server)]

        def list_for_conversation(self, _conversation_id):
            return [dict(item) for item in self.servers]

        def get(self, server_id):
            return next((
                dict(item) for item in self.servers
                if item["server_id"] == server_id), None)

        def get_for_conversation(self, _conversation_id, agent_name=""):
            return next((
                dict(item) for item in self.servers
                if not agent_name
                or item["agent_name"].lower() == agent_name.lower()), None)

        def release_client(self, server_id, client_id):
            calls.append(("release", server_id, client_id))
            return True

        def configure(self, owner, conversation_id, agent_name, label="", enabled=True,
                      image_output="native", tool_allowlist=None, mode=None):
            calls.append(("configure", owner, conversation_id, agent_name, enabled))
            configured = dict(
                server, server_id="srv-2", agent_name=agent_name,
                active_client_id="", active_relay_id="", enabled=enabled,
                image_output=image_output, mode=mode or "api")
            self.servers.append(configured)
            return dict(configured)

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

    assert calls == [
        ("configure", "alice", "conv-1", "agent-b", True),
    ]
    assert mcp_store.servers[0]["active_client_id"] == "cli-1"


def test_configure_rejects_unknown_image_output(monkeypatch):
    from tasks.ai.actions import _agentres_k6

    class ConversationStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    class Store:
        def list_for_conversation(self, _conversation_id):
            return []

        def get(self, _server_id):
            return None

        def get_for_conversation(self, _conversation_id, _agent_name=""):
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
    # The published-server status row is rendered from the list_resources
    # payload so an existing publication stays visible and reachable.
    assert "mcp_published_server" in render
    assert "mcpPublishedRowEnabled" in render
    assert "mcpPublishedRowDisabled" in render
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
        assert catalog["mcpPublishConnectorSection"]
        assert catalog["mcpPublishConnectorUrlOnce"]
        assert catalog["mcpPublishCreateConnectorKey"]
        assert catalog["mcpPublishToolAllowlist"]
        assert catalog["mcpPublishConnectorPromptTitle"]
        assert catalog["mcpPublishConnectorPromptHint"]
        assert "{agent}" in catalog["mcpPublishedRowEnabled"]
        assert "{agent}" in catalog["mcpPublishedRowDisabled"]
        assert catalog["mcpPublishMode"]
        assert catalog["mcpPublishModeApi"]
        assert catalog["mcpPublishModeFull"]
        assert catalog["mcpPublishModeHint"]

    # The one-way bootstrap prompt covers the full connector contract.
    assert "publishedMcpMode" in source
    assert "mode: mode" in source
    for marker in ("_publishedMcpConnectorPrompt", "get_initial_context",
                   "get_context_updates", "send_user_message",
                   "send_agent_message", "schedule_continuation",
                   "Problem initializing pawflow mcp"):
        assert marker in source


def test_mcp_tools_carry_behavior_annotations():
    # ChatGPT (and other MCP clients) treat unannotated tools as write
    # actions and may refuse them outright; every published tool must
    # declare its behavior hints.
    from services.mcp_server_endpoint import _MCP_TOOLS
    read_only = {"get_initial_context", "get_context_updates", "get_tool_schema"}
    for tool in _MCP_TOOLS:
        annotations = tool["annotations"]
        assert isinstance(annotations["readOnlyHint"], bool)
        assert annotations["readOnlyHint"] is (tool["name"] in read_only)
    by_name = {tool["name"]: tool["annotations"] for tool in _MCP_TOOLS}
    assert by_name["send_user_message"]["idempotentHint"] is True
    assert by_name["send_agent_message"]["idempotentHint"] is True
    assert by_name["use_tool"]["destructiveHint"] is True


def test_store_connector_keys_are_kind_scoped(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    server_id = server["server_id"]
    raw_bearer, bearer = store.create_key(server_id, "Codex")
    raw_connector, connector = store.create_key(
        server_id, "ChatGPT", kind="connector")

    assert raw_bearer.startswith("pfmcp_")
    assert raw_connector.startswith("pfmcc_")
    assert bearer["kind"] == "bearer"
    assert connector["kind"] == "connector"
    assert store.validate_key(
        server_id, raw_connector, kind="connector")["key_id"] == connector["key_id"]
    # No cross-kind use: a bearer key never validates on the connector surface
    # and a connector key never validates as an Authorization bearer.
    assert store.validate_key(server_id, raw_bearer, kind="connector") is None
    assert store.validate_key(server_id, raw_connector) is None
    assert store.validate_key(server_id, raw_connector, kind="header") is None
    kinds = {key["key_id"]: key["kind"] for key in store.list_keys(server_id)}
    assert kinds == {bearer["key_id"]: "bearer",
                     connector["key_id"]: "connector"}
    with pytest.raises(ValueError, match="kind"):
        store.create_key(server_id, kind="urlsafe")


def test_store_migrates_pre_kind_keys_to_bearer(tmp_path):
    database = tmp_path / "published.sqlite3"
    raw = "pfmcp_legacy-token"
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
            """CREATE TABLE mcp_api_keys (
                   key_id TEXT PRIMARY KEY, server_id TEXT NOT NULL,
                   label TEXT NOT NULL, prefix TEXT NOT NULL,
                   token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
                   last_used_at REAL NOT NULL DEFAULT 0,
                   revoked_at REAL NOT NULL DEFAULT 0
               )"""
        )
        connection.execute(
            """INSERT INTO mcp_servers (
                   server_id, owner_user_id, conversation_id, agent_name,
                   label, enabled, created_at, updated_at)
               VALUES ('srv-old', 'alice', 'conv-old', 'agent-a',
                       'Old server', 1, 1, 1)"""
        )
        connection.execute(
            """INSERT INTO mcp_api_keys (
                   key_id, server_id, label, prefix, token_hash, created_at)
               VALUES ('key-old', 'srv-old', 'CLI', ?, ?, 1)""",
            (raw[:14], hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )

    store = MCPServerStore(database)

    assert store.validate_key("srv-old", raw)["kind"] == "bearer"
    assert store.validate_key("srv-old", raw, kind="connector") is None
    assert store.get("srv-old")["tool_allowlist"] == []
    # A server row created before the mode column existed defaults to api.
    assert store.get("srv-old")["mode"] == "api"


def test_store_tool_allowlist_round_trip(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    assert server["tool_allowlist"] == []

    server = store.configure(
        "alice", "conv-1", "agent-a", tool_allowlist=["read", " grep "])
    assert server["tool_allowlist"] == ["read", "grep"]

    # Omitting the parameter preserves the stored allowlist.
    server = store.configure("alice", "conv-1", "agent-a")
    assert server["tool_allowlist"] == ["read", "grep"]

    server = store.configure("alice", "conv-1", "agent-a", tool_allowlist=[])
    assert server["tool_allowlist"] == []

    with pytest.raises(ValueError, match="tool_allowlist"):
        store.configure("alice", "conv-1", "agent-a", tool_allowlist=[""])
    with pytest.raises(ValueError, match="tool_allowlist"):
        store.configure("alice", "conv-1", "agent-a", tool_allowlist="read")


def test_store_mode_round_trip(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    # Default exposure is the api gateway.
    assert store.configure("alice", "conv-1", "agent-a")["mode"] == "api"

    assert store.configure(
        "alice", "conv-1", "agent-a", mode="full")["mode"] == "full"
    # Omitting the parameter preserves the stored mode.
    assert store.configure("alice", "conv-1", "agent-a")["mode"] == "full"
    assert store.configure(
        "alice", "conv-1", "agent-a", mode="api")["mode"] == "api"

    with pytest.raises(ValueError, match="mode"):
        store.configure("alice", "conv-1", "agent-a", mode="bogus")


def test_connector_key_authenticates_only_via_path(monkeypatch, tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    server_id = server["server_id"]
    raw_bearer, _bearer_key = store.create_key(server_id, "Codex")
    raw_connector, connector = store.create_key(
        server_id, "ChatGPT", kind="connector")

    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: store))

    class ConvStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: ConvStore())
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs",
        lambda _cid: {"agent-a": {}})

    def request(path_key="", headers=None):
        req = _Request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }, server_id=server_id, headers=headers)
        if path_key:
            req.path_params["connector_key"] = path_key
        return req

    endpoint._sessions.clear()
    accepted = request(path_key=raw_connector)
    endpoint.handle_mcp_post(accepted)
    status, headers, payload = _decoded(accepted)
    assert status == 200
    assert payload["result"]["protocolVersion"] == "2025-03-26"
    assert headers["Mcp-Session-Id"] in endpoint._sessions

    # A bearer key in the path is rejected: kinds never cross surfaces.
    bearer_in_path = request(path_key=raw_bearer)
    endpoint.handle_mcp_post(bearer_in_path)
    status, headers, _payload = _decoded(bearer_in_path)
    assert status == 401
    assert "WWW-Authenticate" not in headers

    # A connector key in the Authorization header is rejected too.
    connector_as_bearer = request(
        headers={"Authorization": f"Bearer {raw_connector}"})
    endpoint.handle_mcp_post(connector_as_bearer)
    assert _decoded(connector_as_bearer)[0] == 401

    store.revoke_key(server_id, connector["key_id"])
    revoked = request(path_key=raw_connector)
    endpoint.handle_mcp_post(revoked)
    assert _decoded(revoked)[0] == 401


def test_connector_requests_ignore_foreign_origin(monkeypatch, tmp_path):
    """ChatGPT sends its own Origin; the connector key is the credential."""
    store = MCPServerStore(tmp_path / "published.sqlite3")
    server = store.configure("alice", "conv-1", "agent-a")
    server_id = server["server_id"]
    raw_connector, _meta = store.create_key(server_id, "ChatGPT", kind="connector")
    monkeypatch.setattr(
        MCPServerStore, "instance", classmethod(lambda cls: store))

    class ConvStore:
        def resolve_owner(self, _conversation_id):
            return "alice"

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: ConvStore())
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs",
        lambda _cid: {"agent-a": {}})
    endpoint._sessions.clear()

    foreign = {
        "Origin": "https://chatgpt.com",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "pawflow.example",
    }
    connector = _Request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    }, server_id=server_id, headers=dict(foreign))
    connector.path_params["connector_key"] = raw_connector
    endpoint.handle_mcp_post(connector)
    status, _headers, payload = _decoded(connector)
    assert status == 200
    assert payload["result"]["protocolVersion"] == "2025-06-18"

    # Bearer routes keep the DNS-rebinding rejection.
    bearer = _Request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    }, server_id=server_id, headers=dict(foreign))
    endpoint.handle_mcp_post(bearer)
    assert _decoded(bearer)[0] == 403


def test_connector_synthesizes_session_for_stale_or_missing_id(monkeypatch):
    """One-way clients replaying a dropped session must not receive 404."""
    server = {
        "server_id": "srv_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    connector_key = {"key_id": "key-c", "label": "ChatGPT", "kind": "connector"}
    monkeypatch.setattr(
        endpoint, "_authenticate", lambda _req, _sid: (server, connector_key))
    endpoint._sessions.clear()

    stale = _Request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                     headers={"Mcp-Session-Id": "gone"})
    endpoint.handle_mcp_post(stale)
    status, headers, payload = _decoded(stale)
    assert status == 200
    assert payload["result"]["tools"] == endpoint._MCP_TOOLS
    assert headers["Mcp-Session-Id"] != "gone"
    assert headers["Mcp-Session-Id"] in endpoint._sessions

    missing = _Request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    endpoint.handle_mcp_post(missing)
    assert _decoded(missing)[0] == 200

    # Bearer keys keep the strict session contract.
    bearer_key = {"key_id": "key-b", "label": "Codex", "kind": "bearer"}
    monkeypatch.setattr(
        endpoint, "_authenticate", lambda _req, _sid: (server, bearer_key))
    strict = _Request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                      headers={"Mcp-Session-Id": "gone"})
    endpoint.handle_mcp_post(strict)
    assert _decoded(strict)[0] == 404
    absent = _Request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    endpoint.handle_mcp_post(absent)
    assert _decoded(absent)[0] == 400


def test_call_tool_enforces_publication_allowlist(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [
                {"name": "read", "description": "Read", "parameters": {}},
                {"name": "bash", "description": "Shell", "parameters": {}},
            ]

        def get(self, _name):
            return None

    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
        "tool_allowlist": ["read"],
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: (_ for _ in ()).throw(
        AssertionError("excluded tools must not reach the runtime")))
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)

    listing = endpoint._call_tool(server, {"key_id": "key-1"},
                                  "get_tool_schema", {})
    assert not listing["isError"]
    assert len(listing["content"]) == 1
    assert listing["content"][0]["type"] == "text"
    rows = json.loads(listing["content"][0]["text"])
    names = [row.get("name") for row in rows]
    assert "read" in names and "bash" not in names

    excluded_schema = endpoint._call_tool(
        server, {"key_id": "key-1"}, "get_tool_schema", {"tool_name": "bash"})
    assert excluded_schema["isError"]

    blocked = endpoint._call_tool(server, {"key_id": "key-1"}, "use_tool", {
        "tool_name": "bash", "arguments_json": "{}",
    })
    assert blocked["isError"]
    assert "not exposed by this publication" in blocked["content"][0]["text"]


def test_get_tool_schema_empty_listing_is_valid_mcp_text(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return []

        def get(self, _name):
            return None

    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)

    listing = endpoint._call_tool(server, {"key_id": "key-1"},
                                  "get_tool_schema", {})

    assert listing == {
        "content": [{"type": "text", "text": "[]"}],
        "isError": False,
    }


def test_full_mode_lists_tools_with_real_annotations(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [
                {"name": "read", "description": "Read",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "bash", "description": "Shell",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "get_tool_schema", "description": "shim",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "use_tool", "description": "shim",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "odd", "description": None, "parameters": None},
            ]

    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": "full",
    }
    tools = {tool["name"]: tool for tool in endpoint._tools_for_server(server)}
    # Conversation/messaging meta tools stay; the use_tool/get_tool_schema
    # shims are dropped because every tool is now first-class.
    assert "get_initial_context" in tools
    assert "use_tool" not in tools and "get_tool_schema" not in tools
    # Real tools carry honest behavior annotations.
    assert tools["read"]["annotations"]["readOnlyHint"] is True
    assert tools["bash"]["annotations"]["readOnlyHint"] is False
    assert tools["bash"]["annotations"]["destructiveHint"] is True
    assert tools["read"]["inputSchema"]["type"] == "object"
    assert tools["odd"]["description"] == ""
    assert tools["odd"]["inputSchema"] == {
        "type": "object", "properties": {}}

    # api mode still advertises exactly the six meta tools.
    api_names = {tool["name"] for tool in endpoint._tools_for_server(
        dict(server, mode="api"))}
    assert "use_tool" in api_names and "read" not in api_names


def test_full_mode_allowlist_is_case_insensitive(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [
                {"name": "Read", "description": "Read",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "bash", "description": "Shell",
                 "parameters": {"type": "object", "properties": {}}},
            ]

    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": "full", "tool_allowlist": ["READ"],
    }

    names = {tool["name"] for tool in endpoint._tools_for_server(server)}

    assert "Read" in names
    assert "bash" not in names


def test_full_mode_replaces_invalid_dynamic_input_schema(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [
                {"name": "broken", "description": "Broken",
                 "parameters": {"type": 7}},
                {"name": "invalid name", "description": "Space",
                 "parameters": {}},
                {"name": "é", "description": "Unicode",
                 "parameters": {}},
                {"name": "x" * 129, "description": "Long",
                 "parameters": {}},
            ]

    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": "full",
    }

    tools = {tool["name"]: tool for tool in endpoint._tools_for_server(server)}

    assert tools["broken"]["inputSchema"] == {
        "type": "object", "properties": {}}
    assert "invalid name" not in tools
    assert "é" not in tools
    assert "x" * 129 not in tools


def test_full_mode_real_registry_tools_are_mcp_well_formed(monkeypatch):
    from core.tool_registry import create_default_registry

    registry = create_default_registry()
    monkeypatch.setattr(endpoint, "_registry", lambda _server: registry)
    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": "full",
    }

    tools = endpoint._tools_for_server(server)
    names = [tool["name"] for tool in tools]
    normalized_names = [name.casefold() for name in names]

    assert len(names) == len(set(normalized_names))
    assert "get_tool_schema" not in normalized_names
    assert "use_tool" not in normalized_names
    for tool in tools:
        name = tool["name"]
        assert 1 <= len(name) <= 128
        assert all(character.isascii()
                   and (character.isalnum() or character in "_.-")
                   for character in name)
        assert isinstance(tool["description"], str)
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        annotations = tool["annotations"]
        assert isinstance(annotations, dict)
        assert all(isinstance(value, bool)
                   for value in annotations.values())


@pytest.mark.parametrize("message", [
    "Error: invalid arguments",
    "Error calling remote service: disconnected",
    "Blocked by hook: writes are frozen",
    "MCP error: upstream tool failed",
    "HTTP 404\nnot found",
    "HTTP 503\nunavailable",
])
def test_published_mcp_marks_all_tool_error_prefixes(message):
    class Registry:
        def get(self, _name):
            return None

    content, is_error, compact = endpoint._encode_tool_content(
        Registry(), "tool", {}, message)

    assert content == [{"type": "text", "text": message}]
    assert compact == message
    assert is_error is True


def test_published_mcp_does_not_mark_successful_http_result_as_error():
    class Registry:
        def get(self, _name):
            return None

    _content, is_error, _compact = endpoint._encode_tool_content(
        Registry(), "tool", {}, "HTTP 200\nok")

    assert is_error is False


def test_malformed_typed_content_is_serialized_instead_of_forwarded():
    class Registry:
        def get(self, _name):
            return None

    malformed = [{"type": "resource", "resource": {}}]
    content, is_error, compact = endpoint._encode_tool_content(
        Registry(), "tool", {}, malformed)

    assert content == [{"type": "text", "text": json.dumps(malformed)}]
    assert compact == json.dumps(malformed)
    assert is_error is False


def test_full_mode_direct_call_dispatches_like_use_tool(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [{"name": "read", "description": "Read", "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, request_id, name, arguments, user_id, conv_id,
                        agent):
            assert name == "read"
            return {"type": "result", "request_id": request_id, "data": "ok"}

    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": "full",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)

    # A direct call to `read` (not use_tool) reaches the runtime through the
    # shared dispatch path and returns the tool result.
    result = endpoint._call_tool(server, {"key_id": "key-1"}, "read",
                                 {"path": "x"})
    assert result == {"content": [{"type": "text", "text": "ok"}],
                      "isError": False}

    # The publication allowlist still applies to direct calls.
    guarded = dict(server, tool_allowlist=["list_dir"])
    monkeypatch.setattr(endpoint, "_runtime", lambda: (_ for _ in ()).throw(
        AssertionError("allowlisted-out tools must not reach the runtime")))
    blocked = endpoint._call_tool(guarded, {"key_id": "key-1"}, "read",
                                  {"path": "x"})
    assert blocked["isError"]
    assert "not exposed by this publication" in blocked["content"][0]["text"]


def test_dispatch_rejects_unknown_tools_and_non_object_arguments(monkeypatch):
    server = {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
    }
    key = {"key_id": "key-1"}

    unknown = endpoint._dispatch_session_message(server, key, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "missing", "arguments": {}},
    }, "session-1")
    malformed = endpoint._dispatch_session_message(server, key, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_initial_context", "arguments": []},
    }, "session-1")

    assert unknown["error"]["code"] == -32602
    assert "Unknown tool" in unknown["error"]["message"]
    assert malformed["error"]["code"] == -32602
    assert "arguments" in malformed["error"]["message"]


def test_runtime_exception_becomes_mcp_tool_error(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [{"name": "read", "description": "Read", "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, *_args, **_kwargs):
            raise RuntimeError("relay unavailable")

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
        lambda *_args: {"llm_service": "test"})

    result = endpoint._call_tool(
        server, {"key_id": "key-1"}, "use_tool",
        {"tool_name": "read", "arguments_json": "{}"},
        session_id="session-1", mcp_request_id=3)

    assert result["isError"] is True
    assert "relay unavailable" in result["content"][0]["text"]


def test_replayed_request_id_reuses_result_without_reexecuting(monkeypatch):
    from core import external_call_router

    external_call_router.reset_for_tests()
    calls = []

    class Registry:
        def get_tool_definitions(self):
            return [{"name": "write", "description": "Write", "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, *_args, **_kwargs):
            calls.append("executed")
            return {"type": "result", "data": {"saved": True}}

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
        lambda *_args: {"llm_service": "test"})
    arguments = {"tool_name": "write", "arguments_json": "{}"}

    first = endpoint._call_tool(
        server, {"key_id": "key-1"}, "use_tool", arguments,
        session_id="session-1", mcp_request_id=4)
    replay = endpoint._call_tool(
        server, {"key_id": "key-1"}, "use_tool", arguments,
        session_id="session-1", mcp_request_id=4)

    assert first == replay
    assert calls == ["executed"]


def test_one_way_publication_refuses_scheduling_tools(monkeypatch):
    class Registry:
        def get_tool_definitions(self):
            return [{"name": "schedule_continuation", "description": "",
                     "parameters": {}}]

        def get(self, _name):
            return None

    class Runtime:
        def _do_execute(self, request_id, name, arguments, user_id, conv_id,
                        agent):
            return {"type": "result", "request_id": request_id, "data": "ok"}

    server = {
        "server_id": "srv-1",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "agent-a",
    }
    monkeypatch.setattr(endpoint, "_registry", lambda _server: Registry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: (_ for _ in ()).throw(
        AssertionError("one-way scheduling must not reach the runtime")))
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)

    for tool in ("schedule_continuation", "ScheduleWakeup"):
        blocked = endpoint._call_tool(server, {"key_id": "key-1"}, "use_tool", {
            "tool_name": tool, "arguments_json": "{}",
        })
        assert blocked["isError"]
        assert "no return channel" in blocked["content"][0]["text"]

    # With a registered client terminal, the wake has a transport: allowed.
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    allowed = endpoint._call_tool(
        dict(server, terminal_ready=True), {"key_id": "key-1"}, "use_tool", {
            "tool_name": "schedule_continuation", "arguments_json": "{}",
        })
    assert allowed == {"content": [{"type": "text", "text": "ok"}],
                       "isError": False}


def test_poller_redirects_external_mcp_wake(monkeypatch):
    from tasks.ai.agent_poller import AgentPollerMixin

    persisted = []
    routed = []

    class Writer:
        def enqueue_message(self, message, agent_name="", user_id="",
                            wait=False, **_kwargs):
            assert wait is True
            persisted.append((message, agent_name, user_id))

    class ConvStore:
        def get_extra(self, _cid, _key):
            return {"agent": "agent-a"}

        def get_metadata(self, _cid):
            return {"user_id": "alice"}

    configs = {"agent-a": {"runtime_kind": "external_mcp"}}
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: ConvStore())
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: Writer())
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda _cid, agent: configs.get(agent, {"runtime_kind": "llm"}))

    import services.mcp_terminal_router as terminal_router
    monkeypatch.setattr(
        terminal_router, "route_published_terminal_prompt",
        lambda cid, agent, content, msg_id, **_kwargs: (
            routed.append((agent, content, msg_id)) or True))

    poller = AgentPollerMixin()

    # An llm agent is untouched: the internal loop stays in charge.
    assert poller._redirect_external_mcp_wake(
        "conv-1", ["[scheduled:agent-b] [continuation] finish"]) is False
    assert not persisted

    # The external agent's wake is persisted and injected, never looped.
    handled = poller._redirect_external_mcp_wake(
        "conv-1", ["[scheduled:agent-a] [continuation] finish the report"])
    assert handled is True
    assert len(persisted) == 1
    message, agent_name, user_id = persisted[0]
    assert agent_name == "agent-a" and user_id == "alice"
    assert "finish the report" in message["content"]
    assert routed and routed[0][2] == message["msg_id"]

    # Terminal unavailable: still handled (persisted only), never looped.
    monkeypatch.setattr(
        terminal_router, "route_published_terminal_prompt",
        lambda *_args, **_kwargs: False)
    assert poller._redirect_external_mcp_wake(
        "conv-1", ["[scheduled:agent-a] [continuation] retry"]) is True
    assert len(persisted) == 2

    # Agent resolved from active_resources when reasons carry no agent tag.
    assert poller._redirect_external_mcp_wake("conv-1", []) is True
    assert len(persisted) == 3


def test_register_mcp_routes_includes_connector_paths(monkeypatch):
    monkeypatch.setattr(endpoint, "_start_lease_sweeper", lambda: None)

    class Listener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return []

        def register_route(self, method, pattern, _owner, callback=None,
                           public=False, gateway_exempt=False):
            self.routes.append((method, pattern, public, gateway_exempt))

    listener = Listener()
    endpoint.register_mcp_routes(listener)
    exemptions = {(method, pattern): gateway_exempt
                  for method, pattern, _public, gateway_exempt in listener.routes}

    for method in ("POST", "DELETE", "GET"):
        # Connector clients cannot send the gateway header; the key in the
        # URL is the credential, so the gateway challenge is skipped.
        assert exemptions[(method, "/mcp/{server_id}/k/{connector_key}")] is True
        assert exemptions[(method, "/mcp/{server_id}")] is False
    assert all(public for _m, _p, public, _g in listener.routes)


def test_sanitize_path_for_log_redacts_connector_key():
    from services._http_base import sanitize_path_for_log

    assert sanitize_path_for_log("/mcp/srv_x/k/pfmcc_secret-token") == (
        "/mcp/srv_x/k/[redacted]")
    assert sanitize_path_for_log("/mcp/srv_x") == "/mcp/srv_x"
    assert sanitize_path_for_log("/mcp/srv_x/relay/status") == (
        "/mcp/srv_x/relay/status")
    assert sanitize_path_for_log("") == ""


def test_store_readonly_modes_round_trip(tmp_path):
    store = MCPServerStore(tmp_path / "published.sqlite3")
    assert store.configure(
        "alice", "conv-1", "agent-a",
        mode="api_readonly")["mode"] == "api_readonly"
    assert store.configure(
        "alice", "conv-1", "agent-a",
        mode="full_readonly")["mode"] == "full_readonly"
    # Omitting the parameter preserves the stored mode.
    assert store.configure("alice", "conv-1", "agent-a")["mode"] == "full_readonly"


class _ReadWriteRegistry:
    def get_tool_definitions(self):
        return [
            {"name": "read", "description": "Read",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "bash", "description": "Shell",
             "parameters": {"type": "object", "properties": {}}},
        ]

    def get(self, _name):
        return None


def _readonly_server(mode):
    return {
        "server_id": "srv-1", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "agent-a",
        "mode": mode,
    }


def test_api_readonly_advertises_no_write_tool(monkeypatch):
    monkeypatch.setattr(
        endpoint, "_registry", lambda _server: _ReadWriteRegistry())
    tools = {tool["name"]: tool for tool in endpoint._tools_for_server(
        _readonly_server("api_readonly"))}
    assert "send_user_message" not in tools
    assert "send_agent_message" not in tools
    assert "get_initial_context" in tools and "get_context_updates" in tools
    # The gateway only executes read-only tools, so it is honestly read-only.
    assert tools["use_tool"]["annotations"]["readOnlyHint"] is True
    assert "read-only" in tools["use_tool"]["description"]
    assert tools["get_tool_schema"]["annotations"]["readOnlyHint"] is True
    # The api-mode module constant is not mutated by the copy-on-write above.
    assert next(t for t in endpoint._MCP_TOOLS if t["name"] == "use_tool")[
        "annotations"]["readOnlyHint"] is False


def test_full_readonly_advertises_only_read_tools(monkeypatch):
    monkeypatch.setattr(
        endpoint, "_registry", lambda _server: _ReadWriteRegistry())
    tools = {tool["name"]: tool for tool in endpoint._tools_for_server(
        _readonly_server("full_readonly"))}
    assert set(tools) == {"get_initial_context", "get_context_updates", "read"}
    assert tools["read"]["annotations"]["readOnlyHint"] is True


def test_readonly_schema_listing_excludes_write_tools(monkeypatch):
    registry = _ReadWriteRegistry()
    rows = endpoint._tool_schema(registry, "", frozenset(), readonly=True)
    assert [row["name"] for row in rows] == ["read"]
    denied = endpoint._tool_schema(registry, "bash", frozenset(), readonly=True)
    assert "read-only" in denied
    assert endpoint._tool_schema(
        registry, "read", frozenset(), readonly=True)["name"] == "read"


def test_readonly_modes_block_write_execution(monkeypatch):
    monkeypatch.setattr(
        endpoint, "_registry", lambda _server: _ReadWriteRegistry())
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)
    monkeypatch.setattr(
        endpoint, "_persist_tool_call_start", lambda *_args, **_kw: None)
    monkeypatch.setattr(endpoint, "_runtime", lambda: (_ for _ in ()).throw(
        AssertionError("write tools must not reach the runtime")))
    monkeypatch.setattr(
        endpoint, "_conversation_tool_result",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("blocked write meta tools must not persist")))

    # The messaging meta tools are writes: blocked in both read-only modes.
    for mode in ("api_readonly", "full_readonly"):
        blocked = endpoint._call_tool(
            _readonly_server(mode), {"key_id": "key-1"}, "send_user_message",
            {"content": "hi", "message_id": "m-1"})
        assert blocked["isError"]
        assert "read-only" in blocked["content"][0]["text"]

    # The api_readonly gateway refuses write tools before execution.
    blocked = endpoint._call_tool(
        _readonly_server("api_readonly"), {"key_id": "key-1"}, "use_tool",
        {"tool_name": "bash", "arguments_json": "{}"})
    assert blocked["isError"]
    assert "read-only" in blocked["content"][0]["text"]

    # A full_readonly direct call to a write tool is funneled and refused too.
    blocked = endpoint._call_tool(
        _readonly_server("full_readonly"), {"key_id": "key-1"}, "bash",
        {"command": "ls"})
    assert blocked["isError"]
    assert "read-only" in blocked["content"][0]["text"]


def test_full_readonly_direct_read_call_executes(monkeypatch):
    class Runtime:
        def _do_execute(self, request_id, name, arguments, user_id, conv_id,
                        agent):
            assert name == "read"
            return {"type": "result", "request_id": request_id, "data": "ok"}

    monkeypatch.setattr(
        endpoint, "_registry", lambda _server: _ReadWriteRegistry())
    monkeypatch.setattr(endpoint, "_runtime", lambda: Runtime())
    monkeypatch.setattr(endpoint, "_persist_tool_call", lambda *_args: None)
    monkeypatch.setattr(
        endpoint, "_persist_tool_call_start", lambda *_args, **_kw: None)

    result = endpoint._call_tool(
        _readonly_server("full_readonly"), {"key_id": "key-1"}, "read",
        {"path": "x"})
    assert result == {"content": [{"type": "text", "text": "ok"}],
                      "isError": False}


def test_bootstrap_contract_is_mode_aware():
    rows = [{"seq": 1, "role": "user", "content": "hi"}]
    # A read-only publication must never instruct the client to call the
    # messaging write tools it does not expose.
    for mode in ("api_readonly", "full_readonly"):
        doc = endpoint._initial_context_document(_readonly_server(mode), rows)
        assert "read-only" in doc
        assert "send_user_message" not in doc
        assert "send_agent_message" not in doc
    for mode in ("api", "full"):
        doc = endpoint._initial_context_document(_readonly_server(mode), rows)
        assert "send_user_message" in doc
        assert "send_agent_message" in doc


def test_full_readonly_real_registry_invariant(monkeypatch):
    """Long-term security invariant: with the real default tool registry,
    every tool a full_readonly publication advertises must be classified
    read-only by ToolApprovalGate, and the critical mutable tools must be
    absent. A misclassification here is a hole in the read-only guarantee."""
    from core.tool_approval import ToolApprovalGate
    from core.tool_registry import create_default_registry

    registry = create_default_registry()
    monkeypatch.setattr(endpoint, "_registry", lambda _server: registry)
    tools = endpoint._tools_for_server(_readonly_server("full_readonly"))
    names = {tool["name"] for tool in tools}
    assert len(names) > 3, "expected real read-only tools beyond the meta pair"
    for tool in tools:
        if tool["name"] in endpoint._READONLY_META:
            continue
        assert ToolApprovalGate.is_read_only_allowed(tool["name"]), tool["name"]
        assert tool["annotations"]["readOnlyHint"] is True, tool["name"]
    for critical in ("bash", "write", "edit", "apply_patch", "batch_edit",
                     "use_tool", "send_user_message", "send_agent_message",
                     "schedule_continuation", "manage_resource"):
        assert critical not in names, critical
