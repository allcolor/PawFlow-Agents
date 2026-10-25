"""A connected relay must report as connected on a cold registry read.

2026-10-25: "Login via relay" did nothing. The button lists relays with
``claude_code_list_relays``, which drops every entry whose ``connected`` is
false -- and on the live server all six relays reported ``connected=False``,
including MyWorkspace, which was connected and serving traffic.

Cause: ``is_connected`` and ``get_live_instance_cached`` read
``_live_instances`` directly, while only ``_ensure_loaded`` populates that table
for a scope. On a process that has not touched the scope yet the table is empty,
so every relay reads as disconnected. ``get_compatible`` (the neighbouring
method) does load first, which is why the rest of the UI worked.
"""

from core.service_registry import ServiceRegistry


def _registry_with_one_relay(monkeypatch):
    reg = ServiceRegistry()
    loaded = []

    def _ensure_loaded(scope, scope_id):
        loaded.append((scope, scope_id))
        with reg._data_lock:
            reg._live_instances.setdefault(scope_id, {})["relay-a"] = _Live()

    monkeypatch.setattr(reg, "_ensure_loaded", _ensure_loaded)
    monkeypatch.setattr(reg, "_load", lambda *_a, **_k: None)
    return reg, loaded


class _Live:
    def is_connected(self):
        return True


def test_is_connected_loads_the_scope_first(monkeypatch):
    """A cold read must not report a live relay as disconnected."""
    reg, loaded = _registry_with_one_relay(monkeypatch)

    assert reg.is_connected("user", "allcolor", "relay-a") is True
    assert loaded == [("user", "allcolor")]


def test_get_live_instance_cached_loads_the_scope_first(monkeypatch):
    reg, loaded = _registry_with_one_relay(monkeypatch)

    svc = reg.get_live_instance_cached("user", "allcolor", "relay-a")

    assert svc is not None
    assert svc.is_connected() is True
    assert loaded == [("user", "allcolor")]


def test_a_missing_service_still_reports_disconnected(monkeypatch):
    """Loading must not turn an unknown service into a false positive."""
    reg, _loaded = _registry_with_one_relay(monkeypatch)

    assert reg.is_connected("user", "allcolor", "nope") is False
    assert reg.get_live_instance_cached("user", "allcolor", "nope") is None
