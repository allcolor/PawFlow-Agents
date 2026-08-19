from types import SimpleNamespace
from pathlib import Path
import threading

import pytest

from core import provider_pfp_migration as migration


@pytest.fixture(autouse=True)
def _reset_guard():
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry

    service_types = {
        service_type
        for package_types in migration.PROVIDER_PACKAGES.values()
        for service_type in package_types
    }
    original_services = {
        service_type: ServiceFactory._services[service_type]
        for service_type in service_types
        if service_type in ServiceFactory._services
    }

    def _remove_provider_services():
        ServiceRegistry.reset()
        for service_type in service_types:
            ServiceFactory._services.pop(service_type, None)

    migration._state.active = False
    _remove_provider_services()
    yield
    migration._state.active = False
    _remove_provider_services()
    ServiceFactory._services.update(original_services)


def _definition(service_type):
    return SimpleNamespace(service_type=service_type)


def _installed_result(path):
    package = Path(path).stem
    return {
        "ok": True,
        "installed": [
            {"service_type": service_type}
            for service_type in migration.PROVIDER_PACKAGES[package]
        ],
    }


def test_provider_pfp_migration_is_noop_for_unrelated_service(monkeypatch):
    monkeypatch.setattr(
        "core.pfp_package.install_pfp",
        lambda *args, **kwargs: pytest.fail("unrelated scope must not install a PFP"))

    assert migration.migrate_scope(
        "user", "alice", [_definition("openaiCompatibleImageGeneration")]) == []


def test_provider_pfp_migration_installs_each_required_vendor_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.pfp_package.list_installed_packages",
        lambda **kwargs: {"packages": []})
    monkeypatch.setattr(
        "core.pfp_registry.resolve_package_path",
        lambda package, **kwargs: {"path": f"/bundled/{package}.pfp"})

    def _install(path, **kwargs):
        calls.append((path, kwargs))
        return _installed_result(path)

    monkeypatch.setattr("core.pfp_package.install_pfp", _install)

    migrated = migration.migrate_scope("user", "alice", [
        _definition("pixazoImageGeneration"),
        _definition("pixazoVideoGeneration"),
        _definition("wavespeedAudioGeneration"),
        _definition("klingVideoGeneration"),
    ])

    assert migrated == [
        "pawflow.pixazo-provider",
        "pawflow.wavespeed-provider",
        "pawflow.kling-provider",
    ]
    assert [item[0] for item in calls] == [
        "/bundled/pawflow.pixazo-provider.pfp",
        "/bundled/pawflow.wavespeed-provider.pfp",
        "/bundled/pawflow.kling-provider.pfp",
    ]
    assert all(item[1]["user_id"] == "alice" for item in calls)
    assert all(item[1]["scope"] == "user" for item in calls)


def test_provider_pfp_migration_routes_conversation_to_owner(monkeypatch):
    calls = []

    class _Store:
        def resolve_owner(self, conversation_id):
            assert conversation_id == "conv1"
            return "alice"

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: _Store())
    monkeypatch.setattr(
        "core.pfp_package.list_installed_packages",
        lambda **kwargs: {"packages": []})
    monkeypatch.setattr(
        "core.pfp_registry.resolve_package_path",
        lambda package, **kwargs: {"path": f"/bundled/{package}.pfp"})
    monkeypatch.setattr(
        "core.pfp_package.install_pfp",
        lambda path, **kwargs: calls.append((path, kwargs)) or _installed_result(path))

    assert migration.migrate_scope(
        "conv", "conv1", [_definition("wavespeedImageGeneration")]
    ) == ["pawflow.wavespeed-provider"]
    assert calls[0][1]["user_id"] == "alice"
    assert calls[0][1]["conversation_id"] == "conv1"
    assert calls[0][1]["scope"] == "conversation"


def test_provider_pfp_migration_suppresses_recursive_scope_reload(monkeypatch):
    nested = []
    monkeypatch.setattr(
        "core.pfp_package.list_installed_packages",
        lambda **kwargs: {"packages": []})
    monkeypatch.setattr(
        "core.pfp_registry.resolve_package_path",
        lambda package, **kwargs: {"path": f"/bundled/{package}.pfp"})

    def _install(path, **kwargs):
        nested.extend(migration.migrate_scope(
            "user", "alice", [_definition("pixazoImageGeneration")]))
        return _installed_result(path)

    monkeypatch.setattr("core.pfp_package.install_pfp", _install)

    assert migration.migrate_scope(
        "user", "alice", [_definition("pixazoImageGeneration")]
    ) == ["pawflow.pixazo-provider"]
    assert nested == []


def test_provider_pfp_migration_rejects_empty_success(monkeypatch):
    monkeypatch.setattr(
        "core.pfp_package.list_installed_packages",
        lambda **kwargs: {"packages": []})
    monkeypatch.setattr(
        "core.pfp_registry.resolve_package_path",
        lambda package, **kwargs: {"path": f"/bundled/{package}.pfp"})
    monkeypatch.setattr(
        "core.pfp_package.install_pfp",
        lambda *args, **kwargs: {"ok": True, "installed": []})

    with pytest.raises(
            migration.ProviderPfpMigrationError, match="migration incomplete"):
        migration.migrate_scope(
            "user", "alice", [_definition("pixazoImageGeneration")])


def test_provider_pfp_migration_installs_verified_bundle_and_preserves_definition(
        tmp_path, monkeypatch):
    import core.paths as paths
    from core import Service, ServiceFactory, pfp_registry
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore
    from core.service_registry import ServiceRegistry, SCOPE_USER

    bundled = (
        Path(__file__).resolve().parents[1]
        / "data" / "repository" / "packages" / "bundled"
        / "pawflow.pixazo-provider-1.0.0.pfp"
    )
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        pfp_registry,
        "resolve_package_path",
        lambda package, **kwargs: {
            "path": str(bundled) if package == "pawflow.pixazo-provider" else "",
        },
    )
    ScopedRepository.reset()
    ResourceStore.reset()
    ServiceRegistry.reset()

    class _LegacyPixazoImage(Service):
        TYPE = "pixazoImageGeneration"

        def connect(self):
            pass

        def disconnect(self):
            pass

        def is_connected(self):
            return True

    ServiceFactory.register(_LegacyPixazoImage)
    registry = ServiceRegistry.get_instance()
    legacy = registry.install(
        SCOPE_USER,
        "alice",
        "existing-pixazo",
        "pixazoImageGeneration",
        config={"api_key": "secret-ref", "model": "nano-banana"},
        enabled=False,
    )
    ServiceFactory._services.pop("pixazoImageGeneration")

    assert migration.migrate_scope("user", "alice", [legacy]) == [
        "pawflow.pixazo-provider",
    ]

    migrated = registry.get_definition(
        SCOPE_USER, "alice", "existing-pixazo")
    assert migrated.service_id == "existing-pixazo"
    assert migrated.service_type == "pixazoImageGeneration"
    assert migrated.enabled is False
    assert migrated.config["api_key"] == "secret-ref"
    assert migrated.config["model"] == "nano-banana"
    assert migrated.config["package_runtime"]["object_id"] == "service_provider:image"
    assert migrated.config["_package_service_provider"]["package"] == (
        "pawflow.pixazo-provider")


def test_ensure_loaded_migrates_off_the_caller_thread(tmp_path, monkeypatch):
    """The first scope load must not carry the migration synchronously.

    The migration installs provider packages; installing runs the package
    review. Carried synchronously, the first caller after a restart (once
    the webchat's list_stt_services) blocks for its full duration.
    """
    import core.service_registry as sr
    from core.service_registry import ServiceRegistry, SCOPE_USER

    monkeypatch.setattr(sr, "_user_services_dir", lambda: tmp_path / "users")
    monkeypatch.setattr(sr, "_global_services_dir", lambda: tmp_path / "global")
    ServiceRegistry.reset()
    seen = {}
    done = threading.Event()

    def _migrate(scope, sid, definitions):
        seen["thread"] = threading.current_thread().name
        seen["scope"] = (scope, sid)
        done.set()
        return []

    monkeypatch.setattr(migration, "migrate_scope", _migrate)
    registry = ServiceRegistry.get_instance()
    registry._ensure_loaded(SCOPE_USER, "alice")
    assert done.wait(5), "migration was never started"
    assert seen["scope"] == ("user", "alice")
    assert seen["thread"].startswith("pfp-migrate-")
    assert seen["thread"] != threading.main_thread().name


def test_bundled_catalog_trust_requires_exact_index_match(monkeypatch):
    from core.pfp_package import _pp_mod3

    row = {"version": "1.0.0", "sha256": "sha256:" + "a" * 64,
           "developer_key": "ed25519:KEY"}
    monkeypatch.setattr(
        "core.pfp_registry._bundled_row_for_ref",
        lambda ref: dict(row) if ref == "pawflow.pixazo-provider" else {})
    package = {
        "verified": True,
        "sha256": "sha256:" + "a" * 64,
        "manifest": {
            "package": "pawflow.pixazo-provider",
            "version": "1.0.0",
            "developer": {"public_key": "ed25519:KEY"},
        },
    }
    assert _pp_mod3._bundled_catalog_trust(package) is True
    assert _pp_mod3._bundled_catalog_trust(
        {**package, "verified": False}) is False
    assert _pp_mod3._bundled_catalog_trust(
        {**package, "sha256": "sha256:" + "b" * 64}) is False
    tampered = {**package, "manifest": {
        **package["manifest"], "developer": {"public_key": "ed25519:OTHER"}}}
    assert _pp_mod3._bundled_catalog_trust(tampered) is False
    unknown = {**package, "manifest": {
        **package["manifest"], "package": "acme.other"}}
    assert _pp_mod3._bundled_catalog_trust(unknown) is False


def test_bundled_trusted_install_skips_the_llm_review(monkeypatch):
    from core.pfp_package import _pp_mod3

    monkeypatch.setattr(_pp_mod3, "_bundled_catalog_trust", lambda package: True)
    monkeypatch.setattr(
        "core.package_review.review_package_object",
        lambda *args, **kwargs: pytest.fail(
            "bundled-trusted install must not re-review"))
    monkeypatch.setattr(
        "core.package_review.review_skill_content",
        lambda *args, **kwargs: pytest.fail(
            "bundled-trusted install must not re-review"))
    obj = {"type": "service_provider", "path": "runtime/provider.py"}
    _pp_mod3._review_object_for_install(
        {"object": obj, "name": "image"}, {"lock": {"files": {}}},
        False, "alice", "", operation="pfp_install")
    assert obj["_review"]["reviewer"] == "bundled-catalog"
    assert obj["_review"]["allowed"] is True
