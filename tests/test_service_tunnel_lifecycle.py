"""Service Tunnel grant-refresh and reconnect lifecycle tests."""

import threading
from pathlib import Path

import pytest

import core.paths as paths
from core.repository import ScopedRepository
from core import service_tunnel_control as control
from core import service_tunnels


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    yield
    ScopedRepository.reset()


def _create(owner, conversation, *, persistent=True, enabled=True,
            access_relay="laptop", service_relay="server"):
    created = service_tunnels.create_tunnel(
        owner,
        {
            "name": "SSH",
            "conversation_id": conversation,
            "access_relay": access_relay,
            "service_relay": service_relay,
            "bind_host": "127.0.0.1",
            "bind_port": 22000 + len(service_tunnels.list_tunnels(owner)),
            "persistent": persistent,
        },
        {
            "service_id": "ssh",
            "name": "SSH",
            "protocol": "tcp",
            "target_host": "127.0.0.1",
            "target_port": 22,
        },
    )
    if not enabled:
        service_tunnels.update_tunnel(
            owner, created["tunnel_id"], {"enabled": False})
    return created


def test_reconcile_for_relay_enumerates_owners_and_uses_stored_conversation(monkeypatch):
    first = _create("alice", "conv-a")
    second = _create("bob", "conv-b", access_relay="desktop")
    _create("carol", "conv-c", persistent=False)
    _create("dave", "conv-d", enabled=False)

    calls = []
    monkeypatch.setattr(
        control, "start_tunnel",
        lambda owner, conversation, tunnel_id:
        calls.append((owner, conversation, tunnel_id)))

    control.reconcile_for_relay("server")

    assert calls == [
        ("alice", "conv-a", first["tunnel_id"]),
        ("bob", "conv-b", second["tunnel_id"]),
    ]


def test_internal_owner_enumeration_never_changes_public_projection():
    created = _create("alice", "conv-a")

    internal = service_tunnels.list_all_tunnels(include_secrets=True)
    assert internal[0]["_owner_id"] == "alice"
    assert internal[0]["conversation_id"] == "conv-a"
    assert internal[0]["secret_key"]

    public = service_tunnels.list_tunnels("alice")[0]
    assert public["tunnel_id"] == created["tunnel_id"]
    assert "_owner_id" not in public
    assert "conversation_id" not in public
    assert "secret_key" not in public


def test_refresh_once_serializes_and_reconciles_all(monkeypatch):
    from core import service_tunnel_lifecycle as lifecycle

    calls = []
    monkeypatch.setattr(
        lifecycle.service_tunnel_control, "reconcile_for_relay",
        lambda relay_id=None: calls.append(relay_id))

    lifecycle.refresh_once()

    assert calls == [None]


def test_relay_connect_hook_runs_reconciliation_off_thread(monkeypatch):
    from core import service_tunnel_lifecycle as lifecycle

    calls = []

    class ImmediateThread:
        def __init__(self, *, target, args, name, daemon):
            assert daemon is True
            assert name == "service-tunnel-reconcile-server"
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        lifecycle.service_tunnel_control, "reconcile_for_relay",
        lambda relay_id=None: calls.append(relay_id))

    lifecycle.on_relay_connected("server")

    assert calls == ["server"]


def test_lifecycle_start_is_idempotent_and_refreshes_immediately(monkeypatch):
    from core import service_tunnel_lifecycle as lifecycle

    lifecycle.stop()
    started = []
    refreshed = []

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            assert daemon is True
            assert name == "service-tunnel-refresh"
            self.target = target
            self._alive = False

        def start(self):
            self._alive = True
            started.append(self)
            self.target()

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            self._alive = False

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(lifecycle, "refresh_once", lambda: refreshed.append(True))
    monkeypatch.setattr(
        lifecycle._stop_event, "wait", lambda timeout: True)

    assert lifecycle.start() is True
    assert lifecycle.start() is False
    assert len(started) == 1
    assert refreshed == [True]
    lifecycle.stop()


def test_lifecycle_is_wired_to_server_startup_and_relay_registration():
    startup = (ROOT / "cli.py").read_text(encoding="utf-8")
    relay_connection = (
        ROOT / "services" / "_relay_conn.py").read_text(encoding="utf-8")

    assert "start_tunnel_lifecycle()" in startup
    assert "reconcile_service_tunnels(relay_id)" in relay_connection
