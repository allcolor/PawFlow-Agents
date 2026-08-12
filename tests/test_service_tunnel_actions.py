import json
from pathlib import Path

from core import FlowFile


def _call(monkeypatch, action, body=None, user_id="alice"):
    from tasks.ai.actions import service_tunnels as actions

    flowfile = FlowFile(content=b"")
    result = actions._handle_service_tunnels(
        None, action, body or {}, None, user_id, flowfile)
    payload = json.loads(flowfile.get_content().decode("utf-8"))
    return result, flowfile, payload


def test_unrelated_action_is_not_handled():
    from tasks.ai.actions.service_tunnels import _handle_service_tunnels

    assert _handle_service_tunnels(
        None, "not_a_tunnel_action", {}, None, "alice", FlowFile()) is None


def test_list_uses_authenticated_owner_and_hides_internal_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.service_tunnels.list_tunnels",
        lambda user_id: calls.append(user_id) or [{"tunnel_id": "t1"}])

    result, flowfile, payload = _call(
        monkeypatch, "service_tunnels_list", {"user_id": "mallory"})

    assert result == [flowfile]
    assert payload == {"tunnels": [{"tunnel_id": "t1"}]}
    assert calls == ["alice"]


def test_relay_actions_require_a_conversation(monkeypatch):
    called = []
    monkeypatch.setattr(
        "core.service_tunnel_control.list_catalog",
        lambda *args: called.append(args) or [])

    result, flowfile, payload = _call(
        monkeypatch, "service_tunnel_catalog", {"relay_id": "home"})

    assert result == [flowfile]
    assert payload == {"error": "conversation_id is required"}
    assert flowfile.get_attribute("http.response.status") == "400"
    assert called == []


def test_create_routes_authenticated_owner_and_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.service_tunnel_control.create_tunnel",
        lambda user_id, conversation_id, payload:
            calls.append((user_id, conversation_id, payload)) or
            {"tunnel_id": "t1", "status": "connected"})

    body = {
        "conversation_id": "conv",
        "name": "Server SSH",
        "access_relay": "laptop",
        "service_relay": "server",
        "service_id": "ssh",
        "bind_port": 22022,
        "user_id": "mallory",
    }
    _, _, payload = _call(monkeypatch, "service_tunnel_create", body)

    assert payload["tunnel"]["tunnel_id"] == "t1"
    assert calls == [("alice", "conv", {
        "name": "Server SSH",
        "access_relay": "laptop",
        "service_relay": "server",
        "service_id": "ssh",
        "bind_port": 22022,
    })]


def test_catalog_mutations_and_tunnel_lifecycle_are_wired(monkeypatch):
    from tasks.ai.actions import service_tunnels as actions

    monkeypatch.setattr(
        "core.service_tunnel_control.save_catalog_service",
        lambda user, conv, relay, service: {
            "service_id": service["service_id"], "relay_id": relay})
    monkeypatch.setattr(
        "core.service_tunnel_control.delete_catalog_service",
        lambda user, conv, relay, service_id: service_id == "ssh")
    monkeypatch.setattr(
        "core.service_tunnel_control.start_tunnel",
        lambda user, conv, tunnel_id: {"tunnel_id": tunnel_id, "status": "connected"})
    monkeypatch.setattr(
        "core.service_tunnel_control.stop_tunnel",
        lambda user, conv, tunnel_id: {"tunnel_id": tunnel_id, "status": "stopped"})
    monkeypatch.setattr(
        "core.service_tunnel_control.tunnel_status",
        lambda user, conv, tunnel_id: {"tunnel_id": tunnel_id, "status": "connected"})
    monkeypatch.setattr(
        "core.service_tunnel_control.delete_tunnel",
        lambda user, conv, tunnel_id: tunnel_id == "t1")

    _, _, saved = _call(monkeypatch, "service_tunnel_catalog_save", {
        "conversation_id": "conv", "relay_id": "server",
        "service": {"service_id": "ssh"}})
    _, _, removed = _call(monkeypatch, "service_tunnel_catalog_delete", {
        "conversation_id": "conv", "relay_id": "server", "service_id": "ssh"})
    assert saved["service"]["relay_id"] == "server"
    assert removed == {"deleted": True}

    expected = {
        "service_tunnel_start": "connected",
        "service_tunnel_stop": "stopped",
        "service_tunnel_status": "connected",
    }
    for action, status in expected.items():
        _, _, payload = _call(monkeypatch, action, {
            "conversation_id": "conv", "tunnel_id": "t1"})
        assert payload["tunnel"]["status"] == status

    _, _, deleted = _call(monkeypatch, "service_tunnel_delete", {
        "conversation_id": "conv", "tunnel_id": "t1"})
    assert deleted == {"deleted": True}


def test_handler_is_registered_before_agent_dispatch():
    source = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
    assert "from tasks.ai.actions.service_tunnels import _handle_service_tunnels" in source
    assert "_handle_service_tunnels," in source
