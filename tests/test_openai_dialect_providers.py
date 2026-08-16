"""Azure OpenAI and GitHub Copilot: the envelope around the OpenAI body.

What is worth pinning here is exactly what differs from plain OpenAI — the
URL layout and the auth header — plus the Copilot token exchange, which is the
only part that can fail at runtime after the service was configured.
"""

import time

import pytest

from core import copilot_auth
from core.llm_client import LLMClient
from core.llm_providers import openai_dialects


def _cfg(values):
    return lambda key, default="": values.get(key, default)


# ── Azure: deployments, api-version, api-key ─────────────────────────


def test_azure_addresses_a_deployment_not_a_model():
    # Azure's path names a deployment; the model field of the body is not what
    # routes the request.
    path = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com", "gpt-4o",
        _cfg({"azure_deployment": "my-deploy"}))

    assert path.startswith("/openai/deployments/my-deploy/chat/completions")
    assert "api-version=" in path


def test_azure_falls_back_to_the_model_name_as_deployment():
    path = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com", "gpt-4o", _cfg({}))

    assert "/deployments/gpt-4o/" in path


def test_azure_refuses_when_no_deployment_can_be_determined():
    # Guessing here would produce a 404 from Azure with no explanation.
    with pytest.raises(ValueError, match="deployment"):
        openai_dialects.endpoint_path(
            "azure-openai", "https://res.openai.azure.com", "", _cfg({}))


def test_azure_api_version_is_configurable_and_pinned_by_default():
    default = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com", "m", _cfg({}))
    custom = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com", "m",
        _cfg({"azure_api_version": "2025-01-01-preview"}))

    assert default.endswith("api-version=" + openai_dialects.DEFAULT_AZURE_API_VERSION)
    assert custom.endswith("api-version=2025-01-01-preview")


def test_azure_does_not_repeat_the_openai_segment_the_operator_already_typed():
    path = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com/openai", "m", _cfg({}))

    assert path.count("/openai") == 0  # base_url already carries it
    assert path.startswith("/deployments/m/")


def test_a_complete_endpoint_url_is_left_alone():
    full = "https://res.openai.azure.com/openai/deployments/d/chat/completions"

    assert openai_dialects.endpoint_path("azure-openai", full, "m", _cfg({})) == ""


# ── The request line the endpoint suffix is pasted into ──────────────
#
# `endpoint_path` returning "" for a complete URL is only half the contract:
# the caller then has to build a request line that still carries what the base
# URL said. Rebuilt from the path alone, the mandatory api-version was dropped
# and Azure rejected every request.


def test_a_complete_azure_url_keeps_its_api_version():
    from core.llm_providers.cli_shared import request_path

    full = ("https://res.openai.azure.com/openai/deployments/d"
            "/chat/completions?api-version=2024-10-21")

    assert request_path(full, "") == (
        "/openai/deployments/d/chat/completions?api-version=2024-10-21")


def test_a_suffix_that_carries_its_own_version_is_not_doubled():
    from core.llm_providers.cli_shared import request_path

    suffix = openai_dialects.endpoint_path(
        "azure-openai", "https://res.openai.azure.com", "m", _cfg({}))

    line = request_path("https://res.openai.azure.com", suffix)
    assert line.count("api-version=") == 1
    assert line == ("/openai/deployments/m/chat/completions"
                    "?api-version=2024-10-21")


def test_a_plain_base_url_is_unchanged():
    from core.llm_providers.cli_shared import request_path

    assert request_path("https://api.openai.com",
                        "/v1/chat/completions") == "/v1/chat/completions"
    assert request_path("https://api.z.ai/api/paas/v4",
                        "/chat/completions") == "/api/paas/v4/chat/completions"
    # No suffix and no query: the base URL is already the whole endpoint, and
    # it must not grow a trailing slash.
    assert request_path(
        "https://res.openai.azure.com/openai/deployments/d/chat/completions",
        "") == "/openai/deployments/d/chat/completions"


def test_azure_sends_the_key_in_its_own_header_never_as_a_bearer():
    headers = openai_dialects.auth_headers("azure-openai", "secret-key")

    assert headers == {"api-key": "secret-key"}
    assert "Authorization" not in headers


def test_azure_without_a_key_fails_before_the_request():
    with pytest.raises(ValueError):
        openai_dialects.auth_headers("azure-openai", "")


# ── Copilot: token exchange and editor identification ────────────────


def test_copilot_exchanges_the_github_token_and_identifies_an_editor(monkeypatch):
    # The stored credential is a GitHub token; the chat endpoint only accepts
    # the exchanged one.
    monkeypatch.setattr(copilot_auth, "copilot_token", lambda gh: "copilot-tok")

    headers = openai_dialects.auth_headers("copilot", "gho_stored")

    assert headers["Authorization"] == "Bearer copilot-tok"
    assert headers["Copilot-Integration-Id"] == copilot_auth.INTEGRATION_ID
    assert headers["Editor-Version"]


def test_copilot_endpoint_does_not_get_a_v1_segment():
    path = openai_dialects.endpoint_path(
        "copilot", openai_dialects.COPILOT_BASE_URL, "gpt-4.1", _cfg({}))

    assert path == "/chat/completions"


def test_copilot_token_is_reused_until_it_nears_expiry(monkeypatch):
    calls = []

    def fake_get(host, path, headers):
        calls.append(headers)
        return {"token": "tok-1", "expires_at": 10_000}

    copilot_auth.clear_token_cache()
    monkeypatch.setattr(copilot_auth, "_get_json", fake_get)

    first = copilot_auth.copilot_token("gh-token", now=1_000)
    second = copilot_auth.copilot_token("gh-token", now=2_000)

    assert first == second == "tok-1"
    assert len(calls) == 1
    assert calls[0]["Authorization"] == "token gh-token"


def test_copilot_token_is_renewed_before_it_actually_expires(monkeypatch):
    # Renewing exactly at expiry would let a long turn die mid-stream.
    tokens = iter(["tok-1", "tok-2"])
    copilot_auth.clear_token_cache()
    monkeypatch.setattr(copilot_auth, "_get_json",
                        lambda *a, **k: {"token": next(tokens), "expires_at": 10_000})

    copilot_auth.copilot_token("gh", now=1_000)
    # 200s before expiry: still technically valid, too close to start a turn on.
    inside_margin = copilot_auth.copilot_token("gh", now=9_800)

    assert inside_margin == "tok-2"


def test_two_accounts_never_share_a_cached_token(monkeypatch):
    copilot_auth.clear_token_cache()
    monkeypatch.setattr(
        copilot_auth, "_get_json",
        lambda host, path, headers: {"token": "for-" + headers["Authorization"],
                                     "expires_at": time.time() + 3600})

    assert copilot_auth.copilot_token("gh-a") != copilot_auth.copilot_token("gh-b")


def test_a_github_account_without_copilot_says_so(monkeypatch):
    copilot_auth.clear_token_cache()
    monkeypatch.setattr(copilot_auth, "_get_json", lambda *a, **k: {})

    with pytest.raises(RuntimeError, match="subscription"):
        copilot_auth.copilot_token("gh")


def test_missing_credential_is_rejected_before_any_network_call(monkeypatch):
    monkeypatch.setattr(copilot_auth, "_get_json",
                        lambda *a, **k: pytest.fail("should not reach the network"))

    with pytest.raises(ValueError):
        copilot_auth.copilot_token("")


# ── Device flow ──────────────────────────────────────────────────────


def test_device_flow_returns_what_the_user_has_to_type(monkeypatch):
    monkeypatch.setattr(copilot_auth, "_post_form", lambda *a, **k: {
        "device_code": "dev-1", "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 7, "expires_in": 600})

    started = copilot_auth.start_device_login()

    assert started["user_code"] == "ABCD-1234"
    assert started["device_code"] == "dev-1"
    assert started["interval"] == 7


def test_pending_and_slow_down_are_not_failures(monkeypatch):
    # Treating these as errors would abort a login the user is still doing.
    for error, slow in (("authorization_pending", False), ("slow_down", True)):
        monkeypatch.setattr(copilot_auth, "_post_form",
                            lambda *a, **k: {"error": error})
        result = copilot_auth.poll_device_login("dev-1")
        assert result["status"] == "pending"
        assert result["slow_down"] is slow


def test_a_denied_login_is_terminal(monkeypatch):
    monkeypatch.setattr(copilot_auth, "_post_form", lambda *a, **k: {
        "error": "access_denied", "error_description": "user cancelled"})

    result = copilot_auth.poll_device_login("dev-1")

    assert result["status"] == "error"
    assert "cancelled" in result["error"]


def test_successful_poll_yields_the_github_token(monkeypatch):
    monkeypatch.setattr(copilot_auth, "_post_form",
                        lambda *a, **k: {"access_token": "gho_abc"})

    assert copilot_auth.poll_device_login("dev-1") == {
        "status": "ok", "access_token": "gho_abc"}


# ── Wiring: both providers ride the OpenAI path ──────────────────────


def test_both_providers_are_selectable_and_route_to_the_openai_body():
    from core._llm_client_driver import OPENAI_WIRE_PROVIDERS

    assert "azure-openai" in LLMClient.PROVIDERS
    assert "copilot" in LLMClient.PROVIDERS
    assert "azure-openai" in OPENAI_WIRE_PROVIDERS
    assert "copilot" in OPENAI_WIRE_PROVIDERS


def test_copilot_has_a_default_endpoint_but_azure_cannot():
    # Every Azure resource has its own host, so defaulting one would send
    # requests to somebody else's deployment.
    assert LLMClient.DEFAULT_URLS["copilot"] == openai_dialects.COPILOT_BASE_URL
    assert "azure-openai" not in LLMClient.DEFAULT_URLS


def test_client_builds_the_azure_request_end_to_end():
    client = LLMClient(provider="azure-openai", config={
        "api_key": "k", "base_url": "https://res.openai.azure.com",
        "azure_deployment": "prod-4o"})

    path = client._openai_endpoint_path(client.base_url, "gpt-4o")

    assert path.startswith("/openai/deployments/prod-4o/chat/completions?api-version=")
    assert client._openai_auth_headers() == {"api-key": "k"}


def test_plain_openai_is_untouched_by_the_dialect_seam():
    client = LLMClient(provider="openai", config={"api_key": "k"})

    assert client._openai_endpoint_path("https://api.openai.com", "gpt-5") == "/v1/chat/completions"
    assert client._openai_auth_headers() == {"Authorization": "Bearer k"}


# ── Service surface ──────────────────────────────────────────────────


def _rules_for(provider):
    from services.llm_connection import LLMConnectionService
    svc = LLMConnectionService.__new__(LLMConnectionService)
    merged = {}
    for rule in svc.get_parameter_rules():
        providers = rule.get("when", {}).get("provider")
        if providers and provider in providers:
            for field, spec in rule["set"].items():
                merged.setdefault(field, {}).update(spec)
    return merged


def test_azure_only_fields_are_hidden_for_every_other_provider():
    assert _rules_for("azure-openai")["azure_deployment"]["visible"] is True
    for other in ("openai", "anthropic", "copilot", "claude-code", "gemini"):
        assert _rules_for(other)["azure_deployment"]["visible"] is False


def test_azure_requires_the_resource_endpoint():
    # Without base_url the request would go to api.openai.com with an
    # Azure key.
    assert _rules_for("azure-openai")["base_url"]["required"] is True


def test_the_github_login_button_only_shows_for_copilot():
    from services.llm_connection import LLMConnectionService
    svc = LLMConnectionService.__new__(LLMConnectionService)

    actions = svc.get_service_actions()

    assert [a["id"] for a in actions] == [
        "omniroute_models_list", "copilot_device_login"]
    copilot = next(a for a in actions if a["id"] == "copilot_device_login")
    assert copilot["when"] == {"provider": ["copilot"]}
    assert copilot["flow"] == "device_code"
