import pytest

import core.paths as paths
from core.repository import ScopedRepository
from core import service_tunnels
from core import service_tunnel_control as control


class Relay:
    TYPE = "relay"

    def __init__(self, relay_id, *, fail_action=""):
        self.relay_id = relay_id
        self.fail_action = fail_action
        self.calls = []
        self._relay_info = {
            "allow_service_tunnels": True,
            "service_tunnels_local": relay_id != "server",
        }

    def is_connected(self):
        return True

    def _request(self, action, path, **kwargs):
        self.calls.append((action, kwargs))
        if action == self.fail_action:
            raise RuntimeError(f"failed {action}")
        if action == "service_tunnel_catalog":
            return {"services": [{
                "service_id": "ssh", "name": "SSH", "protocol": "tcp",
                "target_host": "127.0.0.1", "target_port": 22,
            }]}
        if action == "service_tunnel_status":
            return {"running": True, "role": kwargs["role"]}
        return {"running": action == "service_tunnel_apply"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    monkeypatch.setenv("PAWFLOW_FRPS_SERVER", "tunnels.example")
    monkeypatch.setenv("PAWFLOW_FRPS_PORT", "7000")
    monkeypatch.setenv("PAWFLOW_FRPS_TOKEN", "shared")
    monkeypatch.setenv("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY", "signing")
    relays = {"laptop": Relay("laptop"), "server": Relay("server")}
    monkeypatch.setattr(
        control, "_resolve_relay",
        lambda user_id, conversation_id, relay_id: relays[relay_id])
    yield relays
    ScopedRepository.reset()


def payload():
    return {
        "name": "Server SSH", "access_relay": "laptop",
        "service_relay": "server", "service_id": "ssh",
        "bind_host": "127.0.0.1", "bind_port": 22022,
        "persistent": True,
    }


def test_create_uses_catalog_and_starts_both_roles(isolated):
    created = control.create_tunnel("alice", "conv", payload())
    assert created["status"] == "connected"
    service_apply = isolated["server"].calls[-1]
    access_apply = isolated["laptop"].calls[-1]
    assert service_apply[0] == "service_tunnel_apply"
    assert service_apply[1]["service_id"] == "ssh"
    assert service_apply[1]["local"] is False
    assert access_apply[0] == "service_tunnel_apply"
    assert access_apply[1]["bind_port"] == 22022
    assert access_apply[1]["local"] is True


def test_access_failure_rolls_back_service_and_records_error(isolated):
    isolated["laptop"].fail_action = "service_tunnel_apply"
    with pytest.raises(RuntimeError, match="failed"):
        control.create_tunnel("alice", "conv", payload())
    tunnel = service_tunnels.list_tunnels("alice")[0]
    assert tunnel["status"] == "error"
    assert any(call[0] == "service_tunnel_stop"
               for call in isolated["server"].calls)


def test_duplicate_listener_is_rejected(isolated):
    control.create_tunnel("alice", "conv", payload())
    with pytest.raises(ValueError, match="already used"):
        control.create_tunnel("alice", "conv", payload())


def test_status_and_delete_are_owner_scoped(isolated):
    created = control.create_tunnel("alice", "conv", payload())
    status = control.tunnel_status("alice", "conv", created["tunnel_id"])
    assert status["status"] == "connected"
    assert status["roles"]["service"]["running"] is True
    with pytest.raises(KeyError):
        control.tunnel_status("bob", "conv", created["tunnel_id"])
    assert control.delete_tunnel("alice", "conv", created["tunnel_id"]) is True


def test_stop_disables_refresh_and_explicit_start_reenables(isolated):
    created = control.create_tunnel("alice", "conv", payload())
    tunnel_id = created["tunnel_id"]

    stopped = control.stop_tunnel("alice", "conv", tunnel_id)
    assert stopped["enabled"] is False

    before = sum(len(relay.calls) for relay in isolated.values())
    control.reconcile_for_relay("server")
    assert sum(len(relay.calls) for relay in isolated.values()) == before

    started = control.start_tunnel("alice", "conv", tunnel_id)
    assert started["enabled"] is True
