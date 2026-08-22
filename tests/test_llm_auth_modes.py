"""Three credential modes for every LLM provider, CLI or API.

Authentication used to be decided by provider family: CLI providers could use
an OAuth pool, everything else had to carry an api_key. These tests pin the
replacement rule and the inference that keeps pre-existing services working.
"""

import threading

import pytest

from core.llm_auth_modes import (
    API_KEY,
    MODES,
    NONE,
    OAUTH,
    default_mode,
    resolve_mode,
    validation_error,
)


class TestVocabulary:

    def test_three_modes(self):
        assert MODES == (NONE, API_KEY, OAUTH)


class TestInference:
    """An unset auth_mode must keep every existing service working."""

    def test_credential_pool_implies_oauth(self):
        assert default_mode("openai", {"credential_service_id": "pool"}) == OAUTH

    def test_api_key_implies_api_key(self):
        assert default_mode("openai", {"api_key": "sk-x"}) == API_KEY

    def test_api_keys_pool_also_implies_api_key(self):
        assert default_mode("openai", {"api_keys_pool": "a,b"}) == API_KEY

    def test_bare_cli_provider_implies_none(self):
        """Its binary may already hold a session; that used to be allowed."""
        assert default_mode("claude-code", {}) == NONE
        assert default_mode("codex-interactive", {}) == NONE

    def test_bare_api_provider_implies_api_key(self):
        """So the missing key is still reported, as it always was."""
        assert default_mode("openai", {}) == API_KEY

    def test_explicit_mode_wins_over_inference(self):
        assert resolve_mode("openai", {"auth_mode": NONE, "api_key": "sk-x"}) == NONE

    def test_unknown_mode_falls_back_to_inference(self):
        assert resolve_mode("openai", {"auth_mode": "nonsense",
                                       "api_key": "sk-x"}) == API_KEY


class TestValidation:
    """One rule for every provider, and no silent fallback between modes."""

    def test_api_key_mode_without_a_key_is_refused(self):
        problem = validation_error("openai", {"auth_mode": API_KEY})
        assert "api_key" in problem

    def test_the_refusal_points_at_the_none_mode(self):
        """A forgotten key must not be mistakable for a deliberate choice."""
        assert "auth_mode=none" in validation_error("openai", {"auth_mode": API_KEY})

    def test_oauth_mode_without_a_pool_is_refused(self):
        problem = validation_error("openai", {"auth_mode": OAUTH})
        assert "credential_service_id" in problem

    def test_none_mode_carrying_a_credential_is_refused(self):
        assert validation_error("openai", {"auth_mode": NONE, "api_key": "sk-x"})
        assert validation_error(
            "openai", {"auth_mode": NONE, "credential_service_id": "pool"})

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "claude-code", "gemini"])
    def test_oauth_is_available_to_every_provider(self, provider):
        """The point of the change: OAuth is no longer a CLI privilege."""
        assert validation_error(
            provider, {"auth_mode": OAUTH, "credential_service_id": "pool"}) == ""

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "claude-code"])
    def test_none_is_available_to_every_provider(self, provider):
        assert validation_error(provider, {"auth_mode": NONE}) == ""

    def test_api_key_mode_with_a_key_passes(self):
        assert validation_error("openai", {"auth_mode": API_KEY, "api_key": "k"}) == ""

    def test_legacy_api_service_without_a_key_still_fails(self):
        """The old 'api_key is required' behaviour, now via inference."""
        assert validation_error("openai", {})

    def test_legacy_cli_service_without_anything_still_passes(self):
        assert validation_error("claude-code", {}) == ""


class TestGenericCredentialPool:

    def test_generic_is_a_provider_value(self):
        from services.llm_credential_oauth import GENERIC, PROVIDERS

        assert GENERIC in PROVIDERS

    def test_a_generic_pool_is_accepted_by_any_provider(self):
        """Including the CLIs: they can point at another OAuth backend."""
        from services.llm_credential_oauth import is_credential_service_def

        class _Def:
            service_type = "llmCredentialOAuthProvider"
            config = {"provider": "generic"}

        for provider in ("openai", "anthropic", "claude-code", "gemini"):
            assert is_credential_service_def(_Def(), provider)

    def test_a_vendor_pool_stays_bound_to_its_vendor(self):
        from services.llm_credential_oauth import is_credential_service_def

        class _Def:
            service_type = "llmCredentialOAuthProvider"
            config = {"provider": "claude-code"}

        assert is_credential_service_def(_Def(), "claude-code")
        assert not is_credential_service_def(_Def(), "gemini")

    def test_presets_come_from_the_existing_oauth_module(self):
        """No second copy of the identity-provider endpoints."""
        from services.auth_providers.generic_oauth import PRESETS

        assert {"keycloak", "okta", "auth0", "gitlab"} <= set(PRESETS)

    def test_generic_pool_rejects_missing_client_credentials(self):
        from core import ServiceError
        from services.llm_credential_oauth import (
            LLMCredentialOAuthProviderService)

        service = LLMCredentialOAuthProviderService(
            {"provider": "generic", "identity_provider": "gitlab"})
        with pytest.raises(ServiceError, match="client_id"):
            service._create_connection()

    def test_generic_pool_resolves_preset_placeholders(self):
        from services.llm_credential_oauth import (
            LLMCredentialOAuthProviderService)

        service = LLMCredentialOAuthProviderService({
            "provider": "generic",
            "identity_provider": "okta",
            "client_id": "cid",
            "client_secret": "secret",  # nosec B106
            "preset_vars": {"domain": "acme.okta.com"},
        })
        endpoints = service._generic_endpoints()
        assert endpoints["token_url"] == "https://acme.okta.com/oauth2/default/v1/token"
        assert "{domain}" not in endpoints["authorize_url"]

    def test_explicit_urls_override_the_preset(self):
        from services.llm_credential_oauth import (
            LLMCredentialOAuthProviderService)

        service = LLMCredentialOAuthProviderService({
            "provider": "generic",
            "identity_provider": "gitlab",
            "token_url": "https://id.example/token",  # nosec B106
        })
        assert service._generic_endpoints()["token_url"] == "https://id.example/token"


class TestRefreshIsSerialised:
    """Concurrent refreshes on a rotating identity provider kill the pool."""

    def test_one_lock_per_pool(self):
        from core.llm_oauth_credential import pool_lock

        assert pool_lock("pool-a") is pool_lock("pool-a")
        assert pool_lock("pool-a") is not pool_lock("pool-b")

    def test_the_lock_actually_excludes(self):
        from core.llm_oauth_credential import pool_lock

        lock = pool_lock("pool-exclusion")
        with lock:
            acquired = lock.acquire(blocking=False)
        assert acquired is False

    def test_lock_registry_is_thread_safe(self):
        from core.llm_oauth_credential import pool_lock

        seen = []
        barrier = threading.Barrier(8)

        def grab():
            barrier.wait()
            seen.append(pool_lock("pool-race"))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len({id(lock) for lock in seen}) == 1


class TestExpiry:

    def test_a_token_expiring_within_the_margin_is_stale(self):
        import time

        from core.llm_oauth_credential import EXPIRY_MARGIN_SECONDS, _expired

        assert _expired({"expires_at": time.time() + EXPIRY_MARGIN_SECONDS - 5})
        assert not _expired({"expires_at": time.time() + EXPIRY_MARGIN_SECONDS + 60})

    def test_no_expiry_recorded_is_not_treated_as_expired(self):
        """Otherwise a working long-lived token is rotated on every call."""
        from core.llm_oauth_credential import _expired

        assert not _expired({"expires_at": 0})
        assert not _expired({})

    def test_unparseable_expiry_is_treated_as_expired(self):
        from core.llm_oauth_credential import _expired

        assert _expired({"expires_at": "soon"})


class TestOmniRouteFieldWasRenamed:
    """auth_mode is the general field now; OmniRoute's has its own name."""

    def test_schema_exposes_both_fields_distinctly(self):
        from services.llm_connection import LLMConnectionService

        schema = LLMConnectionService.get_parameter_schema(LLMConnectionService)
        assert set(schema["omniroute_auth_mode"]["options"]) == {"bearer", "none"}
        assert set(schema["auth_mode"]["options"]) == {"", NONE, API_KEY, OAUTH}
