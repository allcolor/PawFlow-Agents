"""A managed relay container that vanished must come back without a restart.

The container is spawned once, from RelayService.connect(), and runs with
`--rm`. Nothing else re-created it, so a crash — or an operator running
`docker rm -f pawflow-relay-srv-<id>` — took the relay out until the whole
PawFlow server was restarted. The transport kept retrying against a container
that no longer existed.
"""

import threading

import pytest

from services.filesystem_service import RelayService


def _managed_service(service_id="MyWorkspace", allow_service_tunnels=False):
    return RelayService({
        "_service_id": service_id,
        "token": "tok",
        "server_managed": True,
        "server_scope": "user",
        "server_scope_id": "allcolor",
        "server_user_id": "allcolor",
        "server_kind": "workspace",
        "allow_service_tunnels": allow_service_tunnels,
    })


class _Manager:
    """Stands in for ServerRelayManager: records what the service asks of it."""

    def __init__(self, running):
        self.running = running
        self.asked = []
        self.spawned = []

    def service_relay_running(self, relay_id, kind="workspace"):
        self.asked.append((relay_id, kind))
        return self.running

    def spawn_service_relay(self, relay_id, token, *, scope, scope_id,
                            user_id, kind="workspace", internal_token="",
                            allow_service_tunnels=False):
        self.spawned.append({
            "relay_id": relay_id, "token": token, "scope": scope,
            "scope_id": scope_id, "user_id": user_id, "kind": kind,
            "allow_service_tunnels": allow_service_tunnels,
        })
        return {"relay_id": relay_id}


@pytest.fixture
def manager(monkeypatch):
    """Install a fake ServerRelayManager and hand it to the test."""
    from core import server_relay_manager as srm

    holder = {}

    def _install(running):
        holder["mgr"] = _Manager(running)
        monkeypatch.setattr(srm.ServerRelayManager, "get_instance",
                            classmethod(lambda cls: holder["mgr"]))
        return holder["mgr"]

    return _install


def test_gone_container_is_respawned_with_the_services_own_identity(manager):
    mgr = manager(running=False)
    svc = _managed_service()

    assert svc.ensure_managed_relay_alive() is True
    assert mgr.asked == [("MyWorkspace", "workspace")]
    # Spawned through the real _start_managed_server_relay, so the identity it
    # passes is covered too — a respawn under a different scope would hand the
    # relay a different workspace.
    assert mgr.spawned == [{
        "relay_id": "MyWorkspace", "token": "tok", "scope": "user",
        "scope_id": "allcolor", "user_id": "allcolor", "kind": "workspace",
        "allow_service_tunnels": False,
    }]


def test_respawn_passes_enabled_service_tunnel_capability(manager):
    mgr = manager(running=False)
    svc = _managed_service(allow_service_tunnels=True)

    assert svc.ensure_managed_relay_alive() is True
    assert mgr.spawned[0]["allow_service_tunnels"] is True


def test_a_connected_relay_is_left_alone_without_spending_cooldown(manager):
    mgr = manager(running=True)
    svc = _managed_service()
    with svc._relay_pool_lock:
        svc._relay_pool.append({"writer": object()})

    assert svc.ensure_managed_relay_alive() is False
    assert mgr.asked == []
    assert mgr.spawned == []
    assert svc._managed_respawn_at == 0.0


def test_a_running_but_disconnected_relay_gets_reconnect_grace(manager):
    mgr = manager(running=True)
    svc = _managed_service()

    assert svc.ensure_managed_relay_alive() is False
    assert mgr.asked == [("MyWorkspace", "workspace")]
    assert mgr.spawned == []
    assert svc._managed_disconnected_at > 0.0
    assert svc._managed_respawn_at == 0.0


def test_a_persistently_disconnected_running_relay_is_replaced(
        manager, monkeypatch):
    from services import _relay_conn as conn_mod

    now = [100.0]
    monkeypatch.setattr(conn_mod.time, "monotonic", lambda: now[0])
    mgr = manager(running=True)
    svc = _managed_service()

    assert svc.ensure_managed_relay_alive() is False
    now[0] += conn_mod._MANAGED_RECONNECT_GRACE_SECONDS - 0.1
    assert svc.ensure_managed_relay_alive() is False
    now[0] += 0.2
    assert svc.ensure_managed_relay_alive() is True
    assert len(mgr.spawned) == 1


def test_a_reconnect_during_container_inspection_cancels_respawn(
        manager, monkeypatch):
    from services import _relay_conn as conn_mod

    monkeypatch.setattr(conn_mod.time, "monotonic", lambda: 200.0)
    mgr = manager(running=True)
    svc = _managed_service()
    svc._managed_disconnected_at = 100.0

    def _inspect_then_reconnect(relay_id, kind="workspace"):
        mgr.asked.append((relay_id, kind))
        with svc._relay_pool_lock:
            svc._relay_pool.append({"writer": object()})
        return True

    mgr.service_relay_running = _inspect_then_reconnect
    assert svc.ensure_managed_relay_alive() is False
    assert mgr.spawned == []
    assert svc._managed_disconnected_at == 0.0


def test_an_operator_run_relay_is_never_touched(manager):
    """No `server_managed`: PawFlow owns no container and must not inspect one."""
    mgr = manager(running=False)
    svc = RelayService({"_service_id": "laptop", "token": "tok"})

    assert svc.ensure_managed_relay_alive() is False
    assert mgr.asked == []
    assert mgr.spawned == []


def test_explicit_restart_replaces_a_managed_relay_even_when_connected(manager):
    mgr = manager(running=True)
    svc = _managed_service()
    with svc._relay_pool_lock:
        svc._relay_pool.append({"writer": object()})

    assert svc.restart_managed_relay() is True

    assert len(mgr.spawned) == 1
    assert svc._managed_respawn_at > 0.0


def test_explicit_restart_rejects_operator_run_relays(manager):
    mgr = manager(running=True)
    svc = RelayService({"_service_id": "laptop", "token": "tok"})

    with pytest.raises(ValueError, match="managed server relay"):
        svc.restart_managed_relay()

    assert mgr.spawned == []


def test_a_burst_of_failures_asks_for_one_container_start(manager):
    """Every in-flight tool call fails at once; one respawn must follow."""
    mgr = manager(running=False)
    svc = _managed_service()

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        svc.ensure_managed_relay_alive())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert len(mgr.spawned) == 1


def test_a_failing_respawn_is_reported_not_raised(manager, monkeypatch):
    """The caller is a transport retry; it owns the error it raises."""
    mgr = manager(running=False)
    svc = _managed_service()

    def _boom():
        raise RuntimeError("no docker socket")

    monkeypatch.setattr(svc, "_start_managed_server_relay", _boom)
    assert svc.ensure_managed_relay_alive() is False
    assert mgr.spawned == []


def test_an_inspection_failure_does_not_consume_respawn_cooldown(
        manager, monkeypatch):
    mgr = manager(running=False)
    svc = _managed_service()
    monkeypatch.setattr(
        mgr, "service_relay_running",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("daemon down")))

    assert svc.ensure_managed_relay_alive() is False
    assert svc._managed_respawn_at == 0.0


def test_the_retry_loop_respawns_before_it_gives_up(manager, monkeypatch):
    """The regression, end to end at the transport.

    Without the respawn the five retries all hit a container that is gone and
    the relay stays down until the server restarts.
    """
    import services._filesystem_ops as fs_mod

    mgr = manager(running=False)
    svc = _managed_service()
    attempts = {"count": 0}

    def _request_once(_action, _path=".", **_kwargs):
        attempts["count"] += 1
        # The container comes back, and with it the relay's connection.
        if mgr.spawned:
            return "ok"
        raise Exception("Relay not connected to 'MyWorkspace'.")

    monkeypatch.setattr(svc, "_request_once", _request_once)
    monkeypatch.setattr(fs_mod.time, "sleep", lambda _d: None)

    assert svc._request("read_file", "README.md") == "ok"
    assert attempts["count"] == 2
    assert len(mgr.spawned) == 1


def test_an_operator_relay_still_exhausts_its_retries(manager, monkeypatch):
    """Nothing about the respawn hook changes the unmanaged path."""
    import services._filesystem_ops as fs_mod

    mgr = manager(running=False)
    svc = RelayService({"_service_id": "laptop", "token": "tok"})

    def _request_once(_action, _path=".", **_kwargs):
        raise Exception("Relay not connected to 'laptop'.")

    monkeypatch.setattr(svc, "_request_once", _request_once)
    monkeypatch.setattr(fs_mod.time, "sleep", lambda _d: None)

    with pytest.raises(Exception, match="Relay transport retry attempts exhausted"):
        svc._request("read_file", "README.md")
    assert mgr.spawned == []
