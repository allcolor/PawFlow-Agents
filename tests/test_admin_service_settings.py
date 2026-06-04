import json

import pytest

from core import FlowFile, ServiceFactory


def _admin_flowfile():
    return FlowFile(content=b"{}", attributes={"http.auth.roles": "admin"})


def test_system_params_manifest_excludes_service_owned_defaults():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    ff = _admin_flowfile()
    result = _handle_admin_settings(
        None, "system_params_get", {}, None, "admin", ff)

    payload = result[0].get_content().decode("utf-8")
    assert "embedding_llm_service" in payload
    assert "PAWFLOW_USE_RTK" in payload
    assert "llm.default.service" not in payload
    assert "image_default_service" not in payload
    assert "pawflow.bg_compact" not in payload


def test_last_enabled_admin_cannot_be_deleted_or_disabled(tmp_path, monkeypatch):
    import core.paths as paths
    from core.security import SecurityManager, Role

    monkeypatch.setattr(paths, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(paths, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(paths, "SECURITY_FILE", tmp_path / "security.json")
    SecurityManager._instance = None
    sm = SecurityManager.get_instance()
    sm.create_user("admin", "admin-password", Role.ADMIN)

    with pytest.raises(ValueError, match="last enabled admin"):
        sm.delete_user("admin")

    with pytest.raises(ValueError, match="last enabled admin"):
        sm.update_user("admin", enabled=False)

    SecurityManager._instance = None


def test_private_gateway_service_registered_and_uses_explicit_secret_refs(monkeypatch):
    import services.private_gateway as private_gateway
    from core.config_value import ConfigValue

    assert "privateGateway" in ServiceFactory.list_types()

    monkeypatch.setattr(
        "core.expression._load_global_secrets",
        lambda: {"gateway_key": ConfigValue(value="RoyBetty")},
    )

    svc = private_gateway.PrivateGateway({
        "enabled": True,
        "secret_refs": "gateway_key",
        "skin": "matrix",
    })

    assert svc.is_enabled() is True
    assert private_gateway.verify_secret("RoyBetty", "gateway_key") is True
    assert private_gateway.verify_secret("RoyBetty", "privategateway.legacy") is False


def test_private_gateway_ws_accepts_gateway_key_header(monkeypatch):
    import services.private_gateway as private_gateway
    from core.config_value import ConfigValue

    monkeypatch.setattr(
        "core.expression._load_global_secrets",
        lambda: {"relay_gateway": ConfigValue(value="open-sesame")},
    )

    svc = private_gateway.PrivateGateway({
        "enabled": True,
        "secret_refs": "relay_gateway",
        "skin": "matrix",
    })

    assert svc.check_ws(
        "/ws/relay/fs_client",
        {"X-PawFlow-Gateway-Key": "open-sesame"},
        ("172.17.0.2", 50000),
    ) is False
    assert svc.check_ws(
        "/ws/relay/fs_client",
        {"X-PawFlow-Gateway-Key": "wrong"},
        ("172.17.0.2", 50000),
    ) is True



def test_private_gateway_cookie_is_bound_to_secret_refs():
    import services.private_gateway as private_gateway

    cookie = private_gateway._make_cookie_value("127.0.0.1", "privategateway.bootstrap")

    assert private_gateway._verify_cookie(
        cookie, "127.0.0.1", secret_refs="privategateway.bootstrap") is True
    assert private_gateway._verify_cookie(
        cookie, "127.0.0.1", secret_refs="privategateway.main") is False
    assert private_gateway._verify_cookie(
        cookie.split(".", 1)[1], "127.0.0.1",
        secret_refs="privategateway.bootstrap") is False


def test_admin_can_create_list_and_delete_oauth_onboarding_tokens(tmp_path, monkeypatch):
    import core.paths as paths
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    monkeypatch.setattr(paths, "OAUTH_INVITE_TOKENS_FILE", tmp_path / "oauth_tokens.json")

    ff = _admin_flowfile()
    result = _handle_admin_settings(None, "admin_oauth_token_create", {
        "role": "user",
        "ttl_seconds": 600,
    }, None, "admin", ff)
    created = json.loads(result[0].get_content().decode("utf-8"))

    assert created["ok"] is True
    assert created["token"]["token"].startswith("pfo_")

    ff_list = _admin_flowfile()
    listed = _handle_admin_settings(
        None, "admin_oauth_tokens_list", {}, None, "admin", ff_list)
    payload = json.loads(listed[0].get_content().decode("utf-8"))
    assert len(payload["tokens"]) == 1
    assert "token" not in payload["tokens"][0]

    ff_del = _admin_flowfile()
    deleted = _handle_admin_settings(None, "admin_oauth_token_revoke", {
        "token_id": payload["tokens"][0]["id"],
    }, None, "admin", ff_del)
    assert json.loads(deleted[0].get_content().decode("utf-8"))["ok"] is True

    ff_empty = _admin_flowfile()
    empty = _handle_admin_settings(
        None, "admin_oauth_tokens_list", {}, None, "admin", ff_empty)
    assert json.loads(empty[0].get_content().decode("utf-8"))["tokens"] == []


def test_admin_can_add_update_and_delete_user_identity_links(tmp_path, monkeypatch):
    import core.paths as paths
    from core.identity_service import IdentityService
    from core.security import SecurityManager, Role
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    monkeypatch.setattr(paths, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(paths, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(paths, "SECURITY_FILE", tmp_path / "security.json")
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    SecurityManager._instance = None
    IdentityService.reset()
    sm = SecurityManager.get_instance()
    sm.create_user("alice", "pass", Role.USER, email="alice@example.com")
    sm.create_user("bob", "pass", Role.USER, email="bob@example.com")

    created = _handle_admin_settings(None, "admin_identity_link", {
        "username": "alice",
        "channel": "github",
        "channel_id": "gh-1",
    }, None, "admin", _admin_flowfile())
    assert json.loads(created[0].get_content().decode("utf-8"))["ok"] is True
    ids = IdentityService.instance()
    assert ids.resolve("github", "gh-1") == "alice"

    updated = _handle_admin_settings(None, "admin_identity_link", {
        "username": "alice",
        "old_channel": "github",
        "channel": "github",
        "channel_id": "gh-2",
    }, None, "admin", _admin_flowfile())
    assert json.loads(updated[0].get_content().decode("utf-8"))["ok"] is True
    assert ids.resolve("github", "gh-1") is None
    assert ids.resolve("github", "gh-2") == "alice"

    conflict = _handle_admin_settings(None, "admin_identity_link", {
        "username": "bob",
        "channel": "github",
        "channel_id": "gh-2",
    }, None, "admin", _admin_flowfile())
    assert conflict[0].get_attribute("http.response.status") == "409"

    deleted = _handle_admin_settings(None, "admin_identity_unlink", {
        "username": "alice",
        "channel": "github",
    }, None, "admin", _admin_flowfile())
    assert json.loads(deleted[0].get_content().decode("utf-8"))["ok"] is True
    assert ids.resolve("github", "gh-2") is None

    SecurityManager._instance = None
    IdentityService.reset()
