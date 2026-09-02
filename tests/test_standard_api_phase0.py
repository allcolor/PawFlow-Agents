"""Phase 0 contracts for published standard agent APIs."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from core.a2a_store import A2AStore


_COMPLETE_CHAT_CONFIG = {
    "standard_api_enabled": True,
    "api_model_id": "pawflow-agent",
    "api_permission_mode": "read_only",
    "api_session_ttl_seconds": 3600,
    "api_max_sessions_per_key": 20,
    "api_max_concurrent_runs_per_key": 2,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": ["text"],
    "api_chat_completions_enabled": True,
    "api_responses_enabled": False,
    "api_anthropic_messages_enabled": False,
    "api_disconnect_policy": "cancel",
}


class _Request:
    def __init__(self, publication_id="a2ap_test", headers=None):
        self.path_params = {"publication_id": publication_id}
        self.headers = headers or {"Host": "pawflow.example"}
        self.body = b""
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


class _Listener:
    def __init__(self):
        self.routes = []

    def get_routes(self):
        return [{"method": method, "pattern": pattern}
                for method, pattern, *_ in self.routes]

    def register_route(self, method, pattern, owner, callback=None, public=False):
        self.routes.append((method, pattern, owner, callback, public))


def _decoded(request):
    status, headers, body = request.completed
    return status, headers, json.loads(body.decode("utf-8"))


def _chat_available(monkeypatch):
    from core import standard_api_config
    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)


def test_existing_publications_migrate_with_standard_api_disabled(tmp_path):
    database = tmp_path / "a2a.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE a2a_publications (
                publication_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                label TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                context_policy TEXT NOT NULL DEFAULT 'isolated',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(conversation_id, agent_name)
            );
            INSERT INTO a2a_publications VALUES (
                'a2ap_old', 'alice', 'conv-1', 'Agent', 'Agent', '',
                'isolated', 1, 1, 1
            );
            """
        )

    publication = A2AStore(database).get_publication("a2ap_old")

    assert publication["standard_api_enabled"] is False
    assert publication["api_model_id"] == ""
    assert publication["api_generation"] == 0
    assert publication["api_permission_mode"] == ""
    assert publication["api_input_modalities_json"] == []
    assert publication["api_request_overrides_json"] == {}
    assert publication["delete_requested_at"] == 0


def test_disabled_draft_is_typed_and_omitted_fields_are_preserved(tmp_path):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice", "conv-1", "Agent",
        standard_api_config={
            "api_model_id": "draft-agent",
            "api_permission_mode": "read_only",
            "api_input_modalities_json": ["text"],
        },
    )
    updated = store.configure_publication(
        "alice", "conv-1", "Agent", label="Renamed",
        standard_api_config={"strict_fields": True},
    )

    assert publication["standard_api_enabled"] is False
    assert updated["api_model_id"] == "draft-agent"
    assert updated["api_permission_mode"] == "read_only"
    assert updated["api_input_modalities_json"] == ["text"]
    assert updated["strict_fields"] is True
    assert updated["api_generation"] == 0

    with pytest.raises(ValueError, match="strict_fields must be a boolean"):
        store.configure_publication(
            "alice", "conv-1", "Agent",
            standard_api_config={"strict_fields": "false"},
        )


def test_enable_is_complete_isolated_capability_checked_and_generation_bumped(
        tmp_path, monkeypatch):
    from core import standard_api_config

    store = A2AStore(tmp_path / "a2a.sqlite3")

    with pytest.raises(ValueError, match="complete standard API fieldset"):
        store.configure_publication(
            "alice", "conv-1", "Agent",
            standard_api_config={"standard_api_enabled": True},
        )

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", False)
    with pytest.raises(ValueError, match="not available"):
        store.configure_publication(
            "alice", "conv-1", "Agent",
            standard_api_config=_COMPLETE_CHAT_CONFIG,
        )

    _chat_available(monkeypatch)
    publication = store.configure_publication(
        "alice", "conv-1", "Agent",
        standard_api_config=_COMPLETE_CHAT_CONFIG,
    )
    assert publication["api_generation"] == 1

    renamed = store.configure_publication(
        "alice", "conv-1", "Agent", label="Renamed")
    assert renamed["api_generation"] == 1

    changed = store.configure_publication(
        "alice", "conv-1", "Agent",
        standard_api_config={"api_session_ttl_seconds": 7200},
    )
    assert changed["api_generation"] == 2

    with pytest.raises(ValueError, match="context_policy='isolated'"):
        store.configure_publication(
            "alice", "conv-1", "Agent", context_policy="shared")


def test_reset_and_delete_lifecycle_are_durable(tmp_path, monkeypatch):
    _chat_available(monkeypatch)
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice", "conv-1", "Agent",
        standard_api_config=_COMPLETE_CHAT_CONFIG,
    )

    reset = store.reset_api_sessions(publication["publication_id"])
    assert reset["api_generation"] == 2
    assert reset["standard_api_enabled"] is True

    deleting = store.request_publication_delete(publication["publication_id"])
    assert deleting["enabled"] is False
    assert deleting["delete_requested_at"] > 0
    assert deleting["api_generation"] == 3
    assert store.get_publication(publication["publication_id"]) is not None


def test_capabilities_are_safe_and_advertise_standard_dialects():
    from core.standard_api_config import get_standard_api_capabilities

    capabilities = get_standard_api_capabilities()

    assert capabilities["dialects"] == {
        "chat_completions": True,
        "responses": True,
        "anthropic_messages": True,
    }
    assert capabilities["permission_modes"] == ["read_only", "default"]
    assert capabilities["modalities"] == ["text"]
    assert capabilities["bounds"]["api_model_id_max_length"] == 128
    assert capabilities["bounds"]["api_session_ttl_seconds"] == {
        "min": 60, "max": 2592000}
    assert "secrets" not in json.dumps(capabilities).lower()


def test_owner_view_exposes_capabilities_runtime_and_write_policy(
        tmp_path, monkeypatch):
    from core import FlowFile
    from tasks.ai.actions._agentres_k7 import _handle_agentres_k7
    from tasks.ai.actions.agent_resource import _ACTION_ROLES

    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication("alice", "conv-1", "Agent")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    conversation_store = MagicMock()
    conversation_store.resolve_owner.return_value = "alice"
    conversation_store.list_conversations.return_value = []
    flowfile = FlowFile()

    _handle_agentres_k7(
        None,
        "a2a_get",
        {"conversation_id": "conv-1"},
        conversation_store,
        "alice",
        flowfile,
    )

    payload = json.loads(flowfile.content)
    assert payload["standard_api_capabilities"]["dialects"] == {
        "chat_completions": True,
        "responses": True,
        "anthropic_messages": True,
    }
    returned = payload["publications"][0]
    assert returned["publication_id"] == publication["publication_id"]
    assert returned["runtime"]["session_count"] == 0
    assert returned["runtime"]["active_run_count"] == 0
    assert "token_hash" not in json.dumps(payload)
    assert _ACTION_ROLES["a2a_publication_reset_api_sessions"] == "write"


def test_neutral_auth_preserves_a2a_error_shapes(tmp_path, monkeypatch):
    from services import a2a_server_endpoint as endpoint

    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication("alice", "conv-1", "Agent")
    raw, _key = store.create_key(publication["publication_id"], "client")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.resolve_owner",
        lambda self, conversation_id: "alice",
    )
    monkeypatch.setattr(
        "core.conv_agent_config.get_all_agent_configs",
        lambda conversation_id: {"Agent": {"runtime_kind": "llm"}},
    )

    unauthorized = _Request(publication_id=publication["publication_id"])
    endpoint.handle_extended_agent_card(unauthorized)
    status, headers, payload = _decoded(unauthorized)
    assert status == 401
    assert headers["WWW-Authenticate"] == "Bearer"
    assert payload == {"error": "Unauthorized"}

    authorized = _Request(
        publication_id=publication["publication_id"],
        headers={"Host": "pawflow.example", "Authorization": f"Bearer {raw}"},
    )
    endpoint.handle_extended_agent_card(authorized)
    assert _decoded(authorized)[0] == 200

    store.request_publication_delete(publication["publication_id"])
    deleting = _Request(
        publication_id=publication["publication_id"],
        headers={"Host": "pawflow.example", "Authorization": f"Bearer {raw}"},
    )
    endpoint.handle_extended_agent_card(deleting)
    assert _decoded(deleting)[0] == 404
    assert _decoded(deleting)[2] == {"error": "A2A publication not found"}


def test_standard_api_stub_routes_are_idempotent_and_native_404():
    from services.standard_api_endpoint import (
        handle_anthropic_messages,
        handle_openai_chat_completions,
        register_standard_api_routes,
    )

    listener = _Listener()
    register_standard_api_routes(listener)
    register_standard_api_routes(listener)

    assert len(listener.routes) == 8
    assert all(route[4] is True for route in listener.routes)

    openai_request = _Request()
    handle_openai_chat_completions(openai_request)
    status, headers, payload = _decoded(openai_request)
    assert status == 404
    assert headers["x-request-id"].startswith("req_")
    assert payload == {
        "error": {
            "message": "The requested standard API surface was not found.",
            "type": "invalid_request_error",
            "param": None,
            "code": "not_found",
        }
    }

    anthropic_request = _Request()
    handle_anthropic_messages(anthropic_request)
    status, headers, payload = _decoded(anthropic_request)
    assert status == 404
    assert headers["request-id"].startswith("req_")
    assert payload["type"] == "error"
    assert payload["error"] == {
        "type": "not_found_error",
        "message": "The requested standard API surface was not found.",
    }
    assert payload["request_id"] == headers["request-id"]
