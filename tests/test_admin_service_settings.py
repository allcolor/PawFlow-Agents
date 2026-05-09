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


def test_last_enabled_admin_cannot_be_deleted_or_disabled():
    from core.security import SecurityManager

    sm = SecurityManager.get_instance()

    with pytest.raises(ValueError, match="last enabled admin"):
        sm.delete_user("admin")

    with pytest.raises(ValueError, match="last enabled admin"):
        sm.update_user("admin", enabled=False)


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
