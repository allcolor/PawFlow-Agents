import json


def test_llm_schema_includes_live_model_helper_with_api_key_notice():
    from core.service_parameter_helpers import apply_service_parameter_helpers
    from services.llm_connection import LLMConnectionService

    schema = apply_service_parameter_helpers(
        "llmConnection",
        LLMConnectionService({}).get_parameter_schema(),
    )

    helper = schema["default_model"]["fill_helper"]
    assert helper["id"] == "llm.models"
    assert "api_key" in helper["requires"]
    assert "api_key" in schema["default_model"]["description"]
    assert schema["api_key"]["fill_helper"]["id"] == "secrets.refs"


def test_service_parameter_helper_returns_llm_fallback_without_api_key():
    from core.service_parameter_helpers import get_service_parameter_helper

    data = get_service_parameter_helper(
        "llmConnection",
        "default_model",
        {"provider": "openai", "api_key": ""},
    )

    assert data["source"] == "fallback"
    assert data["warning"]
    assert any(v["value"] == "gpt-5.5" for v in data["values"])


def test_priority_service_helpers_cover_media_oauth_rclone_and_catalogs():
    from core.service_parameter_helpers import apply_service_parameter_helpers

    schema = apply_service_parameter_helpers("openaiCompatibleSTT", {
        "base_url": {"type": "string"},
        "api_key": {"type": "string"},
        "model": {"type": "string"},
        "response_format": {"type": "string"},
    })
    assert schema["base_url"]["fill_helper"]["id"] == "base_urls"
    assert schema["model"]["fill_helper"]["id"] == "models"
    assert schema["api_key"]["fill_helper"]["id"] == "secrets.refs"

    oauth = apply_service_parameter_helpers("oauthProvider", {
        "scope": {"type": "string"},
        "authorize_url": {"type": "string"},
    })
    assert oauth["scope"]["fill_helper"]["id"] == "oauth.scopes"
    assert oauth["authorize_url"]["fill_helper"]["id"] == "oauth.urls"

    rclone = apply_service_parameter_helpers("rcloneFilesystem", {
        "endpoint": {"type": "string"},
        "region": {"type": "string"},
    })
    assert rclone["endpoint"]["fill_helper"]["id"] == "rclone.backends"
    assert rclone["region"]["fill_helper"]["id"] == "rclone.backends"


def test_object_helper_value_is_json_serializable():
    from core.service_parameter_helpers import get_service_parameter_helper

    data = get_service_parameter_helper("authGateway", "providers", {})
    encoded = json.dumps(data)
    assert "auth.google.client_secret" in encoded
