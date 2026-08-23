"""External secret references, authorization, caching and resolution."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core.config_store import ConfigStore
from core.config_value import ConfigValue
from core.external_secret_cache import ExternalSecretCache
from core.secret_access_policy import (
    SecretAccessPolicy,
    SecretGrant,
    SecretIdentity,
)
from core.secret_entries import (
    decode_external_secret_ref,
    encode_external_secret_ref,
    external_secret_metadata,
)
from core.secret_provider import (
    ProviderValue,
    SecretProviderFactory,
    SecretResolveContext,
)
from core.secret_resolver import SecretResolver


def test_external_reference_encrypts_locator():
    raw = encode_external_secret_ref(
        "keeper-production", {"record_uid": "abc", "field": "password"})

    assert raw["$type"] == "external_secret"
    assert raw["provider_service"] == "keeper-production"
    assert raw["locator"].startswith("enc:v2:")
    assert "record_uid" not in json.dumps(raw)
    ref = decode_external_secret_ref(raw)
    assert ref.locator == {"record_uid": "abc", "field": "password"}
    assert external_secret_metadata(raw) == {
        "type": "external_secret",
        "provider_service": "keeper-production",
        "version": 1,
        "valid_envelope": True,
    }


def test_local_mutation_preserves_external_sibling(tmp_path):
    path = tmp_path / "secrets.json"
    ConfigStore.upsert_external_secret(
        path, "REMOTE", "provider", {"key": "remote"})
    external_before = ConfigStore.load_secrets_raw(path)["REMOTE"]

    ConfigStore.upsert_local_secret(
        path, "LOCAL", ConfigValue(value="local-value"))

    raw = ConfigStore.load_secrets_raw(path)
    assert raw["REMOTE"] == external_before
    assert ConfigStore.decode_secret_entry(
        path, "LOCAL", raw["LOCAL"]).as_str() == "local-value"

    assert ConfigStore.delete_secret_entry(path, "LOCAL") is True
    assert ConfigStore.load_secrets_raw(path)["REMOTE"] == external_before

    ConfigStore.save_secrets(
        path, {"ANOTHER": ConfigValue(value="another-local")})
    assert ConfigStore.load_secrets_raw(path)["REMOTE"] == external_before


def test_conversation_and_agent_policies_intersect():
    identity = SecretIdentity("TOKEN", "user", "alice")
    grant = SecretGrant("TOKEN", "user")

    assert SecretAccessPolicy(None, None).allows(identity)
    assert SecretAccessPolicy(frozenset({grant}), None).allows(identity)
    assert not SecretAccessPolicy(frozenset(), None).allows(identity)
    assert not SecretAccessPolicy(
        frozenset({grant}), frozenset()).allows(identity)
    assert SecretAccessPolicy(
        frozenset({grant}), frozenset({grant})).allows(identity)


def test_policy_loads_conversation_and_agent_instance_intersection():
    from core.conversation_store import ConversationStore

    store = ConversationStore.instance()
    conversation_id = store.generate_id()
    store.save(conversation_id, [], user_id="alice")
    store.set_extra(conversation_id, "secret_access", {
        "allow": [{"name": "TOKEN", "source_scope": "user"}],
    }, user_id="alice")
    store.set_extra(conversation_id, "conv_agents", {
        "worker": {
            "secret_access": {
                "allow": [{"name": "OTHER", "source_scope": "user"}],
            },
        },
    }, user_id="alice")

    policy = SecretAccessPolicy.load(conversation_id, "WORKER")
    assert not policy.allows(SecretIdentity("TOKEN", "user", "alice"))
    assert not policy.allows(SecretIdentity("OTHER", "user", "alice"))


def test_denied_winning_alias_never_falls_back(monkeypatch):
    resolver = SecretResolver(ExternalSecretCache(max_entries=8))
    sources = [
        ("conv", "c1", None, {"TOKEN": "conv-ciphertext"}),
        ("global", "", None, {"TOKEN": "global-ciphertext"}),
    ]
    monkeypatch.setattr(resolver, "_sources", lambda owner, conv: sources)
    monkeypatch.setattr(
        SecretAccessPolicy, "load",
        classmethod(lambda cls, conv, agent: SecretAccessPolicy(
            frozenset({SecretGrant("TOKEN", "global")}), None)))

    assert resolver.resolve_record(
        "TOKEN", conversation_id="c1") is None


class _FakeProviderService:
    cache_ttl_seconds = 60

    def __init__(self):
        self.calls = 0

    def fetch(self, locator, context):
        self.calls += 1
        assert locator == {"key": "remote-token"}
        assert context.secret_name == "TOKEN"
        return ProviderValue.from_value("materialized-value", version="v1")


def test_external_value_is_materialized_through_cache(monkeypatch):
    resolver = SecretResolver(ExternalSecretCache(max_entries=8))
    service = _FakeProviderService()
    raw = encode_external_secret_ref("provider", {"key": "remote-token"})
    monkeypatch.setattr(
        resolver, "_sources",
        lambda owner, conv: [("global", "", None, {"TOKEN": raw})])
    monkeypatch.setattr(
        resolver, "_provider_service",
        lambda *args: ("global", "", service))
    monkeypatch.setattr(
        SecretAccessPolicy, "load",
        classmethod(lambda cls, conv, agent: SecretAccessPolicy(None, None)))

    first = resolver.resolve_name("TOKEN")
    second = resolver.resolve_name("TOKEN")

    assert first.as_str() == second.as_str() == "materialized-value"
    assert service.calls == 1


def test_external_cache_single_flight():
    cache = ExternalSecretCache(max_entries=8)
    calls = 0
    calls_lock = threading.Lock()
    values = []

    def fetch():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return ProviderValue.from_value("shared")

    threads = [
        threading.Thread(target=lambda: values.append(
            cache.get_or_fetch("key", 60, fetch).value))
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert values == [b"shared"] * 6
    assert calls == 1


def test_aws_secrets_manager_adapter_extracts_json_field():
    from core.secret_provider_adapters import AwsSecretsManagerAdapter

    class Client:
        def get_secret_value(self, **request):
            assert request == {
                "SecretId": "production/api", "VersionStage": "AWSCURRENT"}
            return {
                "SecretString": '{"token":"sk-live","ignored":true}',
                "VersionId": "version-7",
            }

    adapter = object.__new__(AwsSecretsManagerAdapter)
    adapter.config = {}
    adapter._client = Client()
    value = adapter.fetch({
        "secret_id": "production/api",
        "version_stage": "AWSCURRENT",
        "json_key": "token",
    }, SecretResolveContext("TOKEN", "global", ""))

    assert value.value == b"sk-live"
    assert value.version == "version-7"


def test_vault_adapter_rejects_non_http_addresses():
    from core.secret_provider_adapters import HashicorpVaultKvAdapter

    adapter = HashicorpVaultKvAdapter({
        "address": "file:///etc",
        "token": "vault-token",
    })

    with pytest.raises(ValueError, match="http or https"):
        adapter.fetch(
            {"path": "passwd"},
            SecretResolveContext("TOKEN", "global", ""),
        )


def test_wave_a_adapters_are_registered():
    import core.secret_provider_adapters  # noqa: F401

    providers = set(SecretProviderFactory.list_providers())
    assert {
        "aws_secrets_manager",
        "aws_ssm_parameter_store",
        "hashicorp_vault_kv",
        "azure_key_vault",
        "gcp_secret_manager",
        "keeper",
    } <= providers


def test_provider_config_is_one_encrypted_sensitive_scalar():
    from services.secret_provider_service import SecretProviderService

    schema = SecretProviderService({}).get_parameter_schema()
    assert schema["provider_config"]["sensitive"] is True
    with pytest.raises(TypeError):
        SecretProviderService._mapping({"token": "plaintext"})


def test_generic_service_fetches_from_registered_adapter():
    from services.secret_provider_service import SecretProviderService

    service = SecretProviderService({
        "provider": "memory",
        "provider_config": '{"values":{"remote":"resolved"}}',
        "cache_ttl_seconds": 30,
    })
    value = service.fetch(
        {"key": "remote"},
        SecretResolveContext("TOKEN", "global", ""),
    )
    assert value.value == b"resolved"
    service.disconnect()


def test_external_secret_delivery_is_documented_across_product_surfaces():
    root = Path(__file__).resolve().parents[1]
    guide = (root / "docs" / "EXTERNAL_SECRET_PROVIDERS.md").read_text(
        encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    howto = (root / "pawflow-website" / "howtos.html").read_text(
        encoding="utf-8")
    docs_hub = (root / "pawflow-website" / "docs.html").read_text(
        encoding="utf-8")
    features = (root / "pawflow-website" / "features.html").read_text(
        encoding="utf-8")

    for provider in (
            "AWS Secrets Manager", "HashiCorp Vault", "Azure Key Vault",
            "Google Cloud Secret Manager", "Keeper Secrets Manager"):
        assert provider in guide
    assert "per-agent allowlists" in guide
    assert "External Secret Providers" in readme
    assert "one or more attached agents" in readme
    assert "Use local or external secrets" in howto
    assert "External secret provider reference" in docs_hub
    assert "secret providers" in features
    assert 'id="security"' in features
