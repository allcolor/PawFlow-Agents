import json
from types import SimpleNamespace

from core import FlowFile
from tasks.ai.actions.misc import _handle_misc


class _Registry:
    def __init__(self, definition, service=None):
        self.definition = definition
        self.service = service

    def resolve_definition(self, service_id, *, user_id="", conv_id=""):
        if self.definition and self.definition.service_id == service_id:
            return self.definition
        return None

    def get_live_instance_cached(self, scope, scope_id, service_id):
        return self.service


def _definition(*, managed=True, scope="user", scope_id="alice"):
    return SimpleNamespace(
        service_id="MyWorkspace",
        service_type="relay",
        scope=scope,
        scope_id=scope_id,
        enabled=True,
        config={"server_managed": managed},
    )


def _run(monkeypatch, registry, *, roles="user", relay_id="MyWorkspace"):
    from core import service_registry as registry_module

    monkeypatch.setattr(
        registry_module.ServiceRegistry,
        "get_instance",
        classmethod(lambda cls: registry),
    )
    flowfile = FlowFile(
        content=b"",
        attributes={"http.auth.roles": roles},
    )
    result = _handle_misc(
        None,
        "relay_reconnect",
        {"conversation_id": "conv1", "relay_id": relay_id},
        None,
        "alice",
        flowfile,
    )
    assert result == [flowfile]
    return flowfile, json.loads(flowfile.get_content())


def test_relay_reconnect_restarts_the_managed_server_relay(monkeypatch):
    calls = []
    service = SimpleNamespace(
        restart_managed_relay=lambda: calls.append("restart") or True,
    )

    flowfile, payload = _run(
        monkeypatch,
        _Registry(_definition(), service),
    )

    assert flowfile.get_attribute("http.response.status") in (None, "200")
    assert payload["ok"] is True
    assert payload["reconnecting"] is True
    assert calls == ["restart"]


def test_relay_reconnect_rejects_remote_relay_clients(monkeypatch):
    service = SimpleNamespace(
        restart_managed_relay=lambda: (_ for _ in ()).throw(
            AssertionError("remote relay must not be restarted")),
    )

    flowfile, payload = _run(
        monkeypatch,
        _Registry(_definition(managed=False), service),
    )

    assert flowfile.get_attribute("http.response.status") == "400"
    assert "managed server relay" in payload["error"].lower()


def test_relay_reconnect_rejects_unknown_relay(monkeypatch):
    flowfile, payload = _run(monkeypatch, _Registry(None), relay_id="missing")

    assert flowfile.get_attribute("http.response.status") == "404"
    assert "not found" in payload["error"].lower()


def test_relay_reconnect_requires_admin_for_global_relay(monkeypatch):
    service = SimpleNamespace(
        restart_managed_relay=lambda: (_ for _ in ()).throw(
            AssertionError("unauthorized relay must not be restarted")),
    )

    flowfile, payload = _run(
        monkeypatch,
        _Registry(_definition(scope="global", scope_id=""), service),
    )

    assert flowfile.get_attribute("http.response.status") == "403"
    assert "admin" in payload["error"].lower()
