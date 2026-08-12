import copy

import pytest

import core.paths as paths
from core.repository import ScopedRepository
from core import service_tunnels as tunnels


@pytest.fixture(autouse=True)
def isolated_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    yield
    ScopedRepository.reset()


def _payload(**overrides):
    data = {
        "name": "Home SSH",
        "conversation_id": "conv",
        "access_relay": "laptop",
        "service_relay": "home",
        "bind_host": "127.0.0.1",
        "bind_port": 22022,
        "persistent": True,
    }
    data.update(overrides)
    return data


def _service(**overrides):
    data = {
        "service_id": "ssh-home",
        "name": "SSH home",
        "protocol": "tcp",
        "target_host": "127.0.0.1",
        "target_port": 22,
    }
    data.update(overrides)
    return data


def test_create_list_and_get_hide_secret_and_isolate_owner():
    created = tunnels.create_tunnel("alice", _payload(), _service())
    assert created["bind_port"] == 22022
    assert created["target_port"] == 22
    assert "secret_key" not in created
    assert tunnels.list_tunnels("bob") == []
    assert tunnels.get_tunnel("alice", created["tunnel_id"])["name"] == "Home SSH"
    private = tunnels.get_tunnel("alice", created["tunnel_id"], include_secrets=True)
    assert len(private["secret_key"]) >= 32


def test_target_is_taken_only_from_authorized_service_snapshot():
    payload = _payload(target_host="10.0.0.8", target_port=5432)
    created = tunnels.create_tunnel("alice", payload, _service())
    assert created["target_host"] == "127.0.0.1"
    assert created["target_port"] == 22


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "192.168.1.2", "example.com"])
def test_public_or_network_bind_is_rejected(bind_host):
    with pytest.raises(ValueError, match="loopback-only"):
        tunnels.create_tunnel("alice", _payload(bind_host=bind_host), _service())


def test_same_relay_and_non_tcp_service_are_rejected():
    with pytest.raises(ValueError, match="must be different"):
        tunnels.create_tunnel(
            "alice", _payload(access_relay="home", service_relay="home"), _service())
    with pytest.raises(ValueError, match="Only TCP"):
        tunnels.create_tunnel("alice", _payload(), _service(protocol="udp"))


def test_update_and_delete_are_owner_scoped():
    created = tunnels.create_tunnel("alice", _payload(), _service())
    tunnel_id = created["tunnel_id"]
    updated = tunnels.update_tunnel("alice", tunnel_id, {
        "status": "connected", "secret_key": "replacement", "target_port": 9})
    assert updated["status"] == "connected"
    private = tunnels.get_tunnel("alice", tunnel_id, include_secrets=True)
    assert private["secret_key"] != "replacement"
    assert private["target_port"] == 22
    with pytest.raises(KeyError):
        tunnels.get_tunnel("bob", tunnel_id)
    assert tunnels.delete_tunnel("alice", tunnel_id) is True
    assert tunnels.list_tunnels("alice") == []


def test_grant_is_bound_signed_and_expires():
    token = tunnels.issue_grant(
        "signing-secret", tunnel_id="abc", relay_id="laptop",
        user_id="alice", server_name="pft_abc", role="access",
        ttl_seconds=30, now=100)
    claims = tunnels.verify_grant("signing-secret", token, now=129)
    assert claims["tunnel_id"] == "abc"
    assert claims["relay_id"] == "laptop"
    assert claims["role"] == "access"
    assert claims["user_id"] == "alice"
    assert claims["server_name"] == "pft_abc"

    changed = copy.copy(token[:-1]) + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ValueError, match="Invalid"):
        tunnels.verify_grant("signing-secret", changed, now=101)
    with pytest.raises(ValueError, match="expired"):
        tunnels.verify_grant("signing-secret", token, now=130)
