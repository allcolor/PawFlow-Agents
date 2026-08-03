"""Admin-gated server-local execution for managed relay services."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import FlowFile
from core._service_defs import ServiceDef
from core.service_registry import ServiceRegistry
from services.filesystem_service import RelayService


def _flowfile(roles="admin"):
    return FlowFile(content=b"{}", attributes={"http.auth.roles": roles})


def test_server_local_access_is_disabled_by_default():
    service = RelayService({
        "_service_id": "Managed",
        "server_managed": True,
    })

    with pytest.raises(PermissionError, match="Server-local execution is disabled"):
        service.exists("/tmp", local=True)


def test_server_local_read_and_exec_do_not_require_relay_connection(tmp_path):
    log_file = tmp_path / "pawflow.log"
    log_file.write_text("relay diagnostic\n", encoding="utf-8")
    service = RelayService({
        "_service_id": "Managed",
        "server_managed": True,
        "server_local_exec": True,
    })

    assert service.read_file(str(log_file), local=True) == b"relay diagnostic\n"
    result = service.exec(str(tmp_path), "pwd", local=True)
    assert result["returncode"] == 0
    assert result["stdout"].strip() == str(tmp_path)


def test_registry_toggle_updates_definition_and_live_instance_without_reconnect(
        monkeypatch):
    registry = ServiceRegistry()
    definition = ServiceDef(
        "Managed", "relay", scope="user", scope_id="alice",
        config={"server_managed": True})
    live = SimpleNamespace(config={"server_managed": True})
    registry._loaded.add("alice")
    registry._definitions["alice"] = {"Managed": definition}
    registry._live_instances["alice"] = {"Managed": live}
    saved = []
    monkeypatch.setattr(registry, "_save", lambda scope, scope_id: saved.append((scope, scope_id)))
    monkeypatch.setattr(
        registry, "_disconnect_one",
        lambda *_args: pytest.fail("toggle must not disconnect the relay"))

    registry.set_managed_relay_server_local_exec(
        "user", "alice", "Managed", True)

    assert definition.config["server_local_exec"] is True
    assert live.config["server_local_exec"] is True
    assert saved == [("user", "alice")]


def test_admin_api_lists_and_toggles_only_managed_relays(monkeypatch):
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    managed = ServiceDef(
        "Managed", "relay", scope="user", scope_id="alice",
        config={"server_managed": True})
    external = ServiceDef(
        "External", "relay", scope="user", scope_id="alice",
        config={"token": "external"})
    calls = []

    class Registry:
        def iter_all_scopes(self, conv_pairs=None):
            assert conv_pairs == []
            return [("user", "alice", "alice", "")]

        def get_all(self, scope, scope_id):
            return {"Managed": managed, "External": external}

        def is_connected(self, scope, scope_id, service_id):
            return True

        def set_managed_relay_server_local_exec(
                self, scope, scope_id, service_id, enabled):
            calls.append((scope, scope_id, service_id, enabled))

    registry = Registry()
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance", lambda: registry)
    monkeypatch.setattr("core.admin_scope.conv_index", lambda: {})

    listed = _handle_admin_settings(
        None, "admin_server_relays_list", {}, None, "admin", _flowfile())
    payload = json.loads(listed[0].get_content())
    assert [relay["service_id"] for relay in payload["relays"]] == ["Managed"]
    assert payload["relays"][0]["server_local_exec"] is False

    toggled = _handle_admin_settings(None, "admin_server_relay_local_exec_set", {
        "service_id": "Managed",
        "scope": "user",
        "scope_id": "alice",
        "enabled": True,
    }, None, "admin", _flowfile())
    assert json.loads(toggled[0].get_content())["ok"] is True
    assert calls == [("user", "alice", "Managed", True)]


def test_non_admin_cannot_list_or_toggle_server_local_access():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in (
            "admin_server_relays_list",
            "admin_server_relay_local_exec_set"):
        result = _handle_admin_settings(
            None, action, {}, None, "alice", _flowfile("user"))
        assert result[0].get_attribute("http.response.status") == "403"


def test_generic_service_update_cannot_set_server_local_exec():
    from tasks.ai.actions.service_flow import _handle_service_flow

    body = {
        "action": "update_service",
        "service_id": "Managed",
        "scope": "user",
        "config": {"server_local_exec": True},
    }
    flowfile = FlowFile(
        content=json.dumps(body).encode(),
        attributes={"http.auth.roles": "admin"})

    result = _handle_service_flow(
        None, "update_service", body, None, "admin", flowfile)

    assert result[0].get_attribute("http.response.status") == "403"
    assert "admin-only" in json.loads(result[0].get_content())["error"]


def test_admin_ui_exposes_server_relay_toggle():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")

    assert "openAdminServerRelaysDialog()" in template
    assert "admin_server_relays_list" in admin_js
    assert "admin_server_relay_local_exec_set" in admin_js
    assert "local=true" in admin_js
