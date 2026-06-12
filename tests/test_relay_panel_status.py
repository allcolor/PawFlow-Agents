"""Regression tests for the Relays panel connection dot (list_resources).

The red/green dot in the webchat Relays panel comes from
`relay_bindings.details[<relay_id>].connected` in the `list_resources`
response. It must be computed exactly like the relay link dialog
(core.relay_bindings.list_available_relays): resolve the relay's
definition across the scope chain and query is_connected with the
definition's OWN scope/scope_id — not a hand-rolled scope guess.
"""

import json
from types import SimpleNamespace

from core import FlowFile
from tasks.ai.actions.agent_resource import _handle_agent_resource


class _StubStore:
    def get_extra(self, conv_id, key):
        return None

    def set_extra(self, conv_id, key, value):
        pass


class _StubSelf:
    def _ensure_active_agent(self, conv_id, active, uid):
        return active or {}


class _FakeRegistry:
    """Registry where the relay definition lives in ONE specific scope.

    is_connected/get_live_instance_cached only answer for the exact
    (scope, scope_id) of the definition — like the real registry, where
    live instances are keyed by the definition's scope id.
    """

    def __init__(self, relay_id, scope, scope_id):
        self._relay_id = relay_id
        self._key = (scope, scope_id)
        self._sdef = SimpleNamespace(
            service_id=relay_id, service_type="relay",
            scope=scope, scope_id=scope_id, enabled=True, config={})
        self._svc = SimpleNamespace(_relay_info={
            "root": "/workspace", "host_root": "/srv/ws",
            "platform": "linux", "containerized": True,
            "allow_local": False,
        })

    def resolve_all(self, *, user_id="", conv_id="", enabled_only=False):
        return {self._relay_id: self._sdef}

    def is_connected(self, scope, scope_id, service_id):
        return (scope, scope_id) == self._key and service_id == self._relay_id

    def get_live_instance_cached(self, scope, scope_id, service_id):
        if (scope, scope_id) == self._key and service_id == self._relay_id:
            return self._svc
        return None


def _list_resources(monkeypatch, registry, conv_id, user_id="alice"):
    import core.relay_bindings as rb
    import core.service_registry as sr

    monkeypatch.setattr(rb, "get_bindings", lambda cid: {
        "linked": {"*": [registry._relay_id]},
        "default": {"*": registry._relay_id},
    })
    monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                        classmethod(lambda cls: registry))
    ff = FlowFile(b"")
    ff.set_attribute("http.auth.roles", "user")
    out = _handle_agent_resource(
        _StubSelf(), "list_resources",
        {"conversation_id": conv_id}, _StubStore(), user_id, ff)
    assert out == [ff]
    return json.loads(ff.content.decode("utf-8"))


def test_conv_scoped_relay_reports_connected(monkeypatch):
    conv = "convA12345678"
    reg = _FakeRegistry("convRelay", "conv", conv)
    data = _list_resources(monkeypatch, reg, conv)
    det = data["relay_bindings"]["details"]["convRelay"]
    assert det["connected"] is True
    assert det["host_root"] == "/srv/ws"


def test_relay_scoped_to_parent_conversation_reports_connected(monkeypatch):
    # Task sub-conversations resolve services from the parent conversation
    # scope; the panel must show the parent's relay as connected too.
    parent = "convA12345678"
    reg = _FakeRegistry("convRelay", "conv", parent)
    data = _list_resources(monkeypatch, reg, parent + "::task::t1")
    det = data["relay_bindings"]["details"]["convRelay"]
    assert det["connected"] is True


def test_user_scoped_relay_reports_connected(monkeypatch):
    reg = _FakeRegistry("MyWorkspace", "user", "alice")
    data = _list_resources(monkeypatch, reg, "convB12345678")
    det = data["relay_bindings"]["details"]["MyWorkspace"]
    assert det["connected"] is True


def test_unknown_relay_reports_disconnected(monkeypatch):
    reg = _FakeRegistry("convRelay", "conv", "otherConv")
    monkeypatch.setattr(
        type(reg), "resolve_all",
        lambda self, *, user_id="", conv_id="", enabled_only=False: {})
    data = _list_resources(monkeypatch, reg, "convC12345678")
    det = data["relay_bindings"]["details"]["convRelay"]
    assert det["connected"] is False
