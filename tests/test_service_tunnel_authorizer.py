import pytest

import core.paths as paths
from core.repository import ScopedRepository
from core import service_tunnels


@pytest.fixture(autouse=True)
def isolated_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    yield
    ScopedRepository.reset()


def _tunnel():
    created = service_tunnels.create_tunnel(
        "alice",
        {
            "name": "Server SSH",
            "conversation_id": "conv",
            "access_relay": "laptop",
            "service_relay": "server",
            "bind_host": "127.0.0.1",
            "bind_port": 22022,
            "persistent": True,
        },
        {
            "service_id": "ssh",
            "name": "SSH",
            "protocol": "tcp",
            "target_host": "127.0.0.1",
            "target_port": 22,
        },
    )
    return service_tunnels.get_tunnel(
        "alice", created["tunnel_id"], include_secrets=True)


def _grant(tunnel, role, relay_id):
    return service_tunnels.issue_grant(
        "signing", tunnel_id=tunnel["tunnel_id"], relay_id=relay_id,
        user_id="alice", server_name=tunnel["server_name"], role=role,
        ttl_seconds=300, now=100)


def _login(tunnel, role, relay_id, **overrides):
    content = {
        "client_id": f"pft_{tunnel['tunnel_id']}_{role}",
        "metas": {
            "pawflow_grant": _grant(tunnel, role, relay_id),
            "pawflow_relay_id": relay_id,
        },
    }
    content.update(overrides)
    return {"version": "0.1.0", "op": "Login", "content": content}


def test_login_accepts_only_bound_live_tunnel_roles():
    from core.service_tunnel_authorizer import authorize

    tunnel = _tunnel()
    assert authorize(_login(tunnel, "access", "laptop"), "signing", now=101) == {
        "reject": False, "unchange": True}

    wrong = _login(tunnel, "access", "laptop", client_id="pft_other_access")
    denied = authorize(wrong, "signing", now=101)
    assert denied["reject"] is True
    assert "invalid" in denied["reject_reason"].lower()


def test_login_rejects_expired_tampered_and_wrong_relay_grants():
    from core.service_tunnel_authorizer import authorize

    tunnel = _tunnel()
    expired = authorize(_login(tunnel, "access", "laptop"), "signing", now=400)
    assert expired["reject"] is True

    tampered = _login(tunnel, "access", "laptop")
    token = tampered["content"]["metas"]["pawflow_grant"]
    tampered["content"]["metas"]["pawflow_grant"] = token[:-1] + (
        "A" if token[-1] != "A" else "B")
    assert authorize(tampered, "signing", now=101)["reject"] is True

    wrong_relay = _login(tunnel, "access", "server")
    assert authorize(wrong_relay, "signing", now=101)["reject"] is True


def test_new_proxy_matches_service_grant_name_type_and_secret():
    from core.service_tunnel_authorizer import authorize

    tunnel = _tunnel()
    grant = _grant(tunnel, "service", "server")
    request = {
        "version": "0.1.0",
        "op": "NewProxy",
        "content": {
            "user": {
                "metas": {
                    "pawflow_grant": grant,
                    "pawflow_relay_id": "server",
                },
            },
            "proxy_name": tunnel["server_name"],
            "proxy_type": "stcp",
            "sk": tunnel["secret_key"],
            "metas": {"pawflow_grant": grant},
        },
    }
    assert authorize(request, "signing", now=101)["reject"] is False

    for field, value in (
        ("proxy_name", "pft_other"),
        ("proxy_type", "tcp"),
        ("sk", "wrong"),
    ):
        changed = {
            **request,
            "content": {**request["content"], field: value},
        }
        assert authorize(changed, "signing", now=101)["reject"] is True


def test_new_proxy_rejects_access_role_and_disabled_tunnel():
    from core.service_tunnel_authorizer import authorize

    tunnel = _tunnel()
    access_grant = _grant(tunnel, "access", "laptop")
    request = {
        "version": "0.1.0",
        "op": "NewProxy",
        "content": {
            "user": {"metas": {
                "pawflow_grant": access_grant,
                "pawflow_relay_id": "laptop",
            }},
            "proxy_name": tunnel["server_name"],
            "proxy_type": "stcp",
            "sk": tunnel["secret_key"],
            "metas": {"pawflow_grant": access_grant},
        },
    }
    assert authorize(request, "signing", now=101)["reject"] is True

    service_tunnels.update_tunnel(
        "alice", tunnel["tunnel_id"], {"enabled": False})
    assert authorize(_login(tunnel, "service", "server"), "signing", now=101)[
        "reject"] is True


def test_unknown_operation_and_malformed_payload_are_rejected():
    from core.service_tunnel_authorizer import authorize

    assert authorize({"version": "0.1.0", "op": "Ping"}, "signing")[
        "reject"] is True
    assert authorize([], "signing")["reject"] is True
