"""A2A publication, transport, client, routing, and UI tests."""

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.a2a_store import A2AStore


class _Request:
    def __init__(self, body=None, publication_id="a2ap_test", task_id="",
                 headers=None, query_string=""):
        self.body = json.dumps(body).encode("utf-8") if body is not None else b""
        self.path_params = {"publication_id": publication_id, "task_id": task_id}
        self.headers = headers or {"Host": "pawflow.example"}
        self.query_string = query_string
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


def _decoded(request):
    status, headers, body = request.completed
    return status, headers, json.loads(body.decode("utf-8")) if body else None


def test_a2a_store_supports_multiple_agents_and_hashes_keys(tmp_path):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    first = store.configure_publication(
        "alice", "conv-1", "Researcher", label="Research")
    second = store.configure_publication(
        "alice", "conv-1", "Writer", context_policy="shared")

    assert first["publication_id"] != second["publication_id"]
    assert len(store.list_publications("conv-1")) == 2
    raw, key = store.create_key(first["publication_id"], "remote client")
    assert raw.startswith("pfa2a_")
    assert store.validate_key(first["publication_id"], raw)["key_id"] == key["key_id"]
    assert store.validate_key(second["publication_id"], raw) is None
    with sqlite3.connect(store.database_path) as connection:
        digest = connection.execute(
            "SELECT token_hash FROM a2a_api_keys WHERE key_id=?",
            (key["key_id"],)).fetchone()[0]
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in digest


def test_a2a_contexts_and_tasks_are_scoped_to_client_key(tmp_path):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice", "conv-1", "Agent", context_policy="isolated")
    _, key_a = store.create_key(publication["publication_id"], "A")
    _, key_b = store.create_key(publication["publication_id"], "B")
    context = store.resolve_context(publication, key_a["key_id"])
    assert context["internal_conversation_id"].startswith("conv-1::a2a::")
    with pytest.raises(PermissionError, match="Unknown A2A context"):
        store.resolve_context(publication, key_b["key_id"], context["context_id"])
    task = store.create_task(
        publication["publication_id"], context["context_id"], key_a["key_id"],
        context["internal_conversation_id"], "turn-1", {"message": {}})
    assert store.get_task(
        publication["publication_id"], task["task_id"], key_b["key_id"]) is None
    store.update_task(task["task_id"], "completed", response={"role": "agent"})
    assert store.get_task(
        publication["publication_id"], task["task_id"], key_a["key_id"]
    )["response"] == {"role": "agent"}


def test_a2a_named_targets_validate_and_update(tmp_path):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    local = store.save_target(
        "alice", "source", "legal", "local",
        target_conversation_id="target", target_agent="Counsel")
    remote = store.save_target(
        "alice", "source", "research", "remote",
        agent_card_url="https://agent.example/card", auth_secret="REMOTE_KEY")
    assert [row["alias"] for row in store.list_targets("source")] == [
        "legal", "research"]
    updated = store.save_target(
        "alice", "source", "legal", "local",
        target_conversation_id="target-2", target_agent="Counsel")
    assert updated["target_id"] == local["target_id"]
    assert updated["target_conversation_id"] == "target-2"
    assert remote["auth_secret"] == "REMOTE_KEY"
    with pytest.raises(ValueError, match="Agent Card"):
        store.save_target("alice", "source", "bad", "remote")


def test_a2a_runtime_submits_target_only_and_completes_synchronously(
        tmp_path, monkeypatch):
    from core import a2a_runtime
    from core.agent_runtime_api import AgentFinalResult, AgentSubmission

    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication("alice", "conv-1", "Agent")
    _, key = store.create_key(publication["publication_id"], "client")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    monkeypatch.setattr(a2a_runtime, "_ensure_isolated_conversation", lambda *_: None)
    captured = {}

    def submit(request):
        captured["request"] = request
        return AgentSubmission("accepted", request.conversation_id,
                               request.msg_id, request.target_agent, wait_for_done=True)

    monkeypatch.setattr(a2a_runtime.AgentRuntimeAPI, "submit_message", staticmethod(submit))
    monkeypatch.setattr(
        a2a_runtime.AgentRuntimeAPI, "wait_for_done",
        staticmethod(lambda cid, tid: AgentFinalResult(
            cid, tid, response="finished", agent_name="Agent")))
    result = a2a_runtime.send_message(publication, key, {
        "message": {"messageId": "m1", "role": "user",
                    "parts": [{"text": "work"}, {"data": {"x": 1}}]},
        "configuration": {"returnImmediately": False},
    })

    assert result["status"]["state"] == "completed"
    assert result["status"]["message"]["parts"] == [{"text": "finished"}]
    source = json.loads(captured["request"].source_attributes["message_source"])
    assert source["visibility"] == "target_only"
    assert source["task_id"] == result["id"]
    assert captured["request"].channel == "a2a"


def test_a2a_runtime_rejects_raw_parts():
    from core.a2a_runtime import _message_text
    with pytest.raises(ValueError, match="Raw/base64"):
        _message_text({"messageId": "m1", "role": "user",
                       "parts": [{"raw": "AAAA"}]})


def test_target_only_message_is_audited_but_not_shared(monkeypatch):
    from core.conversation_store import ConversationStore
    from core.llm_client import stamp_message

    store = ConversationStore.instance()
    cid = store.generate_id()
    store.save(cid, [], user_id="a2a-test-user")
    try:
        message = stamp_message({
            "role": "user", "content": "private inbound request",
            "source": {"type": "a2a", "name": "client",
                       "target_agent": "Agent", "visibility": "target_only"},
        }, cid)
        store.append_message(cid, message, agent_name="Agent",
                             user_id="a2a-test-user")
        assert any(row.get("content") == "private inbound request"
                   for row in store.load(cid, user_id="a2a-test-user"))
        assert any(row.get("content") == "private inbound request"
                   for row in (store.load_agent_context(cid, "Agent") or []))
        assert not any(row.get("content") == "private inbound request"
                       for row in (store.load_agent_context(cid, "") or []))
    finally:
        store.delete(cid, user_id="a2a-test-user")


def test_agent_card_advertises_only_implemented_interface(monkeypatch):
    from services import a2a_server_endpoint as endpoint
    publication = {
        "publication_id": "a2ap_test", "owner_user_id": "alice",
        "conversation_id": "conv-1", "agent_name": "Agent",
        "label": "Useful Agent", "description": "Does useful work",
        "context_policy": "isolated", "enabled": True,
    }
    monkeypatch.setattr(endpoint, "_publication",
                        lambda req, authenticate=True: (publication, None))
    request = _Request(headers={"Host": "pawflow.example",
                                "X-Forwarded-Proto": "https"})
    endpoint.handle_agent_card(request)
    status, headers, card = _decoded(request)
    assert status == 200
    assert card["supportedInterfaces"] == [{
        "url": "https://pawflow.example/a2a/a2ap_test",
        "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0",
    }]
    assert card["capabilities"]["streaming"] is False
    assert card["security"] == [{"bearer": []}]
    assert headers["Cache-Control"] == "public, max-age=60"


def test_a2a_routes_are_idempotent():
    from services.a2a_server_endpoint import register_a2a_routes

    class Listener:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return list(self.routes)

        def register_route(self, method, pattern, owner, callback=None, public=False):
            self.routes.append({"method": method, "pattern": pattern,
                                "owner": owner, "callback": callback,
                                "public": public})

    listener = Listener()
    register_a2a_routes(listener)
    register_a2a_routes(listener)
    assert len(listener.routes) == 6
    assert all(row["public"] for row in listener.routes)
    assert ("POST", "/a2a/{publication_id}/message:send") in {
        (row["method"], row["pattern"]) for row in listener.routes}


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))

    def json(self):
        return self.payload


def test_a2a_client_discovers_http_json_and_sends_with_secret(monkeypatch):
    from core import a2a_client
    calls = []
    monkeypatch.setattr(a2a_client, "resolve_relay_aware_url",
                        lambda value, **kwargs: value.rstrip("/"))
    monkeypatch.setattr(a2a_client, "_resolve_secret", lambda *args, **kwargs: "secret")
    monkeypatch.setattr(a2a_client.requests, "get", lambda url, **kwargs: _Response({
        "name": "Remote", "supportedInterfaces": [{
            "url": "https://agent.example/a2a",
            "protocolBinding": "HTTP+JSON", "tenant": "tenant-1"}],
    }))

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"id": "task-1", "status": {"state": "submitted"}})

    monkeypatch.setattr(a2a_client.requests, "post", post)
    result = a2a_client.call_target({
        "kind": "remote", "agent_card_url": "https://agent.example/card",
        "auth_secret": "REMOTE_KEY", "allow_private": False,
    }, "send", message="hello", user_id="alice", conversation_id="conv-1")
    assert result["id"] == "task-1"
    assert calls[0][0] == "https://agent.example/a2a/message:send"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0][1]["headers"]["A2A-Tenant"] == "tenant-1"
    assert calls[0][1]["allow_redirects"] is False
    assert json.loads(calls[0][1]["data"])["configuration"] == {
        "returnImmediately": True}


def test_delegate_can_route_to_explicit_other_conversation():
    from core.handlers.resource_agent import SpawnAgentsHandler
    handler = SpawnAgentsHandler()
    handler.set_conversation_id("source")
    handler.set_user_id("alice")
    handler.set_source_agent("Caller", "svc")
    handler._client_resolver = lambda sid, uid: (MagicMock(), MagicMock())
    handler._deliver_cross_conversation_delegate = MagicMock(
        return_value={"state": "accepted", "turn_id": "turn-1"})
    result = handler.execute({"tasks": [{
        "agent": "RemoteLocal", "conversation_id": "target",
        "message": "do work", "id": "job-1",
    }]})
    handler._deliver_cross_conversation_delegate.assert_called_once_with(
        "Caller", "RemoteLocal", "do work", "alice",
        "source", "target", "job-1")
    assert "cross_conversation" in result


def test_delegate_resolves_named_local_target(monkeypatch):
    from core.handlers.resource_agent import SpawnAgentsHandler

    class Targets:
        @staticmethod
        def get_target(conversation_id, alias):
            assert (conversation_id, alias) == ("source", "legal")
            return {
                "kind": "local", "target_conversation_id": "target",
                "target_agent": "Counsel",
            }

    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: Targets()))
    handler = SpawnAgentsHandler()
    handler.set_conversation_id("source")
    handler.set_user_id("alice")
    handler.set_source_agent("Caller", "svc")
    handler._client_resolver = lambda sid, uid: (MagicMock(), MagicMock())
    handler._deliver_cross_conversation_delegate = MagicMock(
        return_value={"state": "accepted", "turn_id": "turn-1"})
    result = handler.execute({"tasks": [{
        "target": "legal", "message": "review", "id": "job-2",
    }]})
    handler._deliver_cross_conversation_delegate.assert_called_once_with(
        "Caller", "Counsel", "review", "alice",
        "source", "target", "job-2")
    assert '"target": "legal"' in result


@pytest.mark.parametrize("runtime_kind", ["external_mcp", "external_agui"])
def test_external_agent_a2a_rejects_isolated_context(monkeypatch, runtime_kind):
    from core import a2a_runtime

    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda *_args: {"runtime_kind": runtime_kind})
    with pytest.raises(ValueError, match="require shared context"):
        a2a_runtime.send_message({
            "enabled": True,
            "conversation_id": "conv-1",
            "agent_name": "External",
            "context_policy": "isolated",
        }, {"key_id": "key-1"}, {})


def test_a2a_ui_is_loaded_and_translated():
    root = Path(__file__).parents[1]
    serve = (root / "tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    render = (root / "tasks/io/chat_ui/resources_render.js").read_text(encoding="utf-8")
    module = root / "tasks/io/chat_ui/resources_a2a.js"
    assert '"resources_a2a.js"' in serve
    assert "showA2AConfigDialog()" in render
    assert module.exists()
    source = module.read_text(encoding="utf-8")
    assert "t('close')" in source
    assert "contextClose" not in source
    for language in ("en", "fr", "es"):
        catalog = json.loads((
            root / f"tasks/io/chat_ui/i18n/{language}.json").read_text(encoding="utf-8"))
        for key in ("a2aConfigure", "a2aPublishAgent", "a2aTargets",
                    "a2aKeyOnce", "a2aAllowPrivate"):
            assert catalog[key]
