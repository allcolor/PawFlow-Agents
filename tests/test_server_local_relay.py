"""Admin-gated server-local execution for managed relay services."""

import json

from chat_ui_testing import rendered_chat_html
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


def test_server_local_interactive_actions_use_the_server_dispatcher(monkeypatch):
    from services import _server_local_exec

    seen = []
    monkeypatch.setattr(
        _server_local_exec, "_execute_server_interactive",
        lambda action, arguments: seen.append((action, arguments)) or {
            "session_id": "term-server"})
    service = RelayService({
        "_service_id": "Managed",
        "server_managed": True,
        "server_local_exec": True,
    })

    result = service._request("open_terminal", "/", local=True, cols=120, rows=40)

    assert result == {"session_id": "term-server"}
    assert seen == [("open_terminal", {
        "request_id": seen[0][1]["request_id"], "cols": 120, "rows": 40})]


def test_terminal_handler_routes_server_local_session_through_pawflow(monkeypatch):
    from tasks.ai.actions import _sf_k6

    class Service:
        config = {"server_managed": True, "server_local_exec": True}

        def __init__(self):
            self.calls = []

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            return {"session_id": "term-server"}

    service = Service()
    registered = []
    monkeypatch.setattr(
        "services.terminal_proxy.register_terminal",
        lambda *args, **kwargs: registered.append((args, kwargs)) or "term-token")
    monkeypatch.setattr(_sf_k6, "_ensure_terminal_routes", lambda _ff: None)
    flowfile = FlowFile(attributes={"auth.session_id": "login-1"})
    helpers = (lambda _relay_id: service,) + (None,) * 5

    _sf_k6._handle_sf_k6(None, "open_terminal", {
        "relay_id": "Managed", "local": True, "cols": 100, "rows": 30,
    }, None, "alice", flowfile, helpers)

    assert service.calls == [("open_terminal", {
        "cols": 100, "rows": 30, "local": True})]
    assert registered[0][1]["server_local"] is True
    assert json.loads(flowfile.get_content())["token"] == "term-token"


def test_desktop_handler_proxies_server_local_novnc_on_loopback(monkeypatch):
    from tasks.ai.actions import _sf_k7

    class Service:
        config = {"server_managed": True, "server_local_exec": True}

        def __init__(self):
            self.calls = []

        def _request(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "desktop_status":
                return {"running": False}
            if action == "start_desktop":
                return {"novnc_port": 6099}
            return {}

    service = Service()
    registered = []
    monkeypatch.setattr(
        "services.vnc_proxy.register_session",
        lambda *args, **kwargs: registered.append((args, kwargs)) or "vnc-token")
    monkeypatch.setattr(
        "services.vnc_proxy.unregister_session", lambda _session_id: None)
    monkeypatch.setattr(_sf_k7, "_ensure_vnc_routes", lambda _ff: None)
    helpers = (lambda _relay_id: service,) + (None,) * 5
    flowfile = FlowFile(attributes={"auth.session_id": "login-1"})

    _sf_k7._handle_sf_k7(None, "open_desktop", {
        "relay_id": "Managed", "local_screen": True,
    }, None, "alice", flowfile, helpers)

    assert service.calls == [
        ("desktop_status", {"local": True}),
        ("start_desktop", {"local": True}),
    ]
    assert registered == [(('local_desktop_Managed', 6099), {
        "owner_user_id": "alice", "login_session_id": "login-1",
        "host": "127.0.0.1",
    })]
    payload = json.loads(flowfile.get_content())
    assert payload["local_screen"] is True
    assert "/vnc/local_desktop_Managed/vnc-token/" in payload["url"]

    close_flowfile = FlowFile()
    _sf_k7._handle_sf_k7(None, "close_desktop", {
        "relay_id": "Managed", "local_screen": True,
    }, None, "alice", close_flowfile, helpers)
    assert service.calls[-1] == ("stop_desktop", {"local": True})


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


def test_registry_tunnel_toggle_updates_managed_relay_without_reconnect(
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
    monkeypatch.setattr(
        registry, "_save",
        lambda scope, scope_id: saved.append((scope, scope_id)))
    monkeypatch.setattr(
        registry, "_disconnect_one",
        lambda *_args: pytest.fail("toggle must not disconnect the relay"))

    registry.set_managed_relay_service_tunnels(
        "user", "alice", "Managed", True)

    assert definition.config["allow_service_tunnels"] is True
    assert live.config["allow_service_tunnels"] is True
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

        def set_managed_relay_service_tunnels(
                self, scope, scope_id, service_id, enabled):
            calls.append(("tunnels", scope, scope_id, service_id, enabled))

    registry = Registry()
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance", lambda: registry)
    monkeypatch.setattr("core.admin_scope.conv_index", lambda: {})

    listed = _handle_admin_settings(
        None, "admin_server_relays_list", {}, None, "admin", _flowfile())
    payload = json.loads(listed[0].get_content())
    assert [relay["service_id"] for relay in payload["relays"]] == ["Managed"]
    assert payload["relays"][0]["server_local_exec"] is False
    assert payload["relays"][0]["allow_service_tunnels"] is False

    toggled = _handle_admin_settings(None, "admin_server_relay_local_exec_set", {
        "service_id": "Managed",
        "scope": "user",
        "scope_id": "alice",
        "enabled": True,
    }, None, "admin", _flowfile())
    assert json.loads(toggled[0].get_content())["ok"] is True
    assert calls == [("user", "alice", "Managed", True)]

    toggled = _handle_admin_settings(
        None, "admin_server_relay_service_tunnels_set", {
            "service_id": "Managed",
            "scope": "user",
            "scope_id": "alice",
            "enabled": True,
        }, None, "admin", _flowfile())
    assert json.loads(toggled[0].get_content())["ok"] is True
    assert calls[-1] == ("tunnels", "user", "alice", "Managed", True)


def test_non_admin_cannot_list_or_toggle_server_local_access():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in (
            "admin_server_relays_list",
            "admin_server_relay_local_exec_set",
            "admin_server_relay_service_tunnels_set"):
        result = _handle_admin_settings(
            None, action, {}, None, "alice", _flowfile("user"))
        assert result[0].get_attribute("http.response.status") == "403"


@pytest.mark.parametrize(
    "protected_key", ["server_local_exec", "allow_service_tunnels"])
def test_generic_service_update_cannot_set_server_relay_permissions(
        protected_key):
    from tasks.ai.actions.service_flow import _handle_service_flow

    body = {
        "action": "update_service",
        "service_id": "Managed",
        "scope": "user",
        "config": {protected_key: True},
    }
    flowfile = FlowFile(
        content=json.dumps(body).encode(),
        attributes={"http.auth.roles": "admin"})

    result = _handle_service_flow(
        None, "update_service", body, None, "admin", flowfile)

    assert result[0].get_attribute("http.response.status") == "403"
    assert "admin-only" in json.loads(result[0].get_content())["error"]


def test_admin_ui_exposes_server_relay_toggle():
    template = rendered_chat_html()
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")

    assert "openAdminServerRelaysDialog()" in template
    assert "admin_server_relays_list" in admin_js
    assert "admin_server_relay_local_exec_set" in admin_js
    assert "admin_server_relay_service_tunnels_set" in admin_js
    assert "local=true" in admin_js
    assert "Allow tunnels (FRP)" in admin_js


def test_terminal_and_desktop_mode_picker_accepts_server_local_relays():
    terminal_js = Path("tasks/io/chat_ui/terminal.js").read_text(encoding="utf-8")
    commands_js = Path(
        "tasks/io/chat_ui/terminal_commands.js").read_text(encoding="utf-8")

    assert "server_local_exec: det.server_local_exec || false" in terminal_js
    assert "function _relaySupportsLocal" in terminal_js
    assert "relay.allow_local || relay.server_local_exec" in terminal_js
    assert commands_js.count("_relaySupportsLocal(") >= 2
