"""End-to-end tests for PFP-defined namespaced repository resources."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from core import FlowFile, pfp_package
from core.extension_repository import ExtensionRepository


def _manifest(package_id, keypair, objects, *, dependencies=None):
    value = {
        "format": "pawflow.package.v1",
        "package": package_id,
        "version": "1.0.0",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": objects,
    }
    if dependencies is not None:
        value["dependencies"] = dependencies
    return value


def _write_owner_package(
        root: Path, keypair, *, package_id="examples.avatar-runtime",
        valid_document=True, include_resource=True,
        contributions="dependencies"):
    pkg = root / (package_id.replace(".", "-") + ".pfpdir")
    content = pkg / "content"
    content.mkdir(parents=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["format", "title"],
        "properties": {
            "format": {"const": "example.avatar.v1"},
            "title": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    document = {
        "format": "example.avatar.v1",
        "title": "Luna",
    }
    if not valid_document:
        document["title"] = 42
    (content / "avatar.schema.json").write_text(
        json.dumps(schema), encoding="utf-8")
    (content / "luna.json").write_text(
        json.dumps(document), encoding="utf-8")
    (content / "luna.vrm").write_bytes(b"VRM fixture")
    objects = [{
        "id": "repository_type:avatar",
        "type": "repository_type",
        "name": "avatar",
        "resource_type": "example.avatar",
        "schema_version": "1",
        "schema": "content/avatar.schema.json",
        "contributions": contributions,
        "mutable": True,
        "asset_extensions": [".vrm", ".webp"],
    }]
    if include_resource:
        objects.append({
            "id": "repository_resource:luna",
            "type": "repository_resource",
            "name": "luna",
            "resource_type": "example.avatar",
            "schema_version": "1",
            "path": "content/luna.json",
            "assets": [{
                "id": "model",
                "path": "content/luna.vrm",
            }],
        })
    (pkg / "pfp.json").write_text(
        json.dumps(_manifest(package_id, keypair, objects)),
        encoding="utf-8")
    return pkg


def _write_pack(
        root: Path, keypair, *, package_id="examples.avatar-pack",
        dependencies=None):
    pkg = root / (package_id.replace(".", "-") + ".pfpdir")
    content = pkg / "content"
    content.mkdir(parents=True)
    (content / "nova.json").write_text(json.dumps({
        "format": "example.avatar.v1",
        "title": "Nova",
    }), encoding="utf-8")
    (content / "nova.vrm").write_bytes(b"Nova VRM fixture")
    objects = [{
        "id": "repository_resource:nova",
        "type": "repository_resource",
        "name": "nova",
        "resource_type": "example.avatar",
        "schema_version": "1",
        "path": "content/nova.json",
        "assets": [{"id": "model", "path": "content/nova.vrm"}],
    }]
    (pkg / "pfp.json").write_text(
        json.dumps(_manifest(
            package_id, keypair, objects, dependencies=dependencies)),
        encoding="utf-8")
    return pkg


def _build(pkg: Path, keypair):
    return pfp_package.build_pfp(
        str(pkg), private_key=keypair["private_key"])["path"]


@pytest.fixture(autouse=True)
def _repository_root(tmp_path, monkeypatch):
    import core.paths as paths

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ExtensionRepository.reset()


def test_repository_objects_are_installable_without_builtin_resource_type(
        tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(tmp_path, keypair), keypair)

    plan = pfp_package.inspect_pfp(artifact, user_id="alice")
    by_id = {row["id"]: row for row in plan["objects"]}
    assert {"repository_type", "repository_resource"} <= (
        pfp_package._INSTALLABLE_TYPES)
    assert by_id["repository_type:avatar"]["status"] == "new"
    assert by_id["repository_resource:luna"]["status"] == "new"
    assert (
        "repository.type:example.avatar@1"
        in by_id["repository_type:avatar"]["capabilities"]["provides"])
    assert by_id["repository_resource:luna"]["hash"].startswith("sha256:")

    result = pfp_package.install_pfp(artifact, user_id="alice")
    assert result["ok"] is True
    stored = ExtensionRepository.instance().get(
        "example.avatar", "luna", user_id="alice", scope="user")
    assert stored["document"]["title"] == "Luna"
    assert stored["assets"][0]["path"] == "content/luna.vrm"
    assert stored["installed_from"]["package"] == "examples.avatar-runtime"
    descriptor = pfp_package.resolve_repository_type(
        "example.avatar", user_id="alice")
    assert descriptor["owner_package"] == "examples.avatar-runtime"
    assert descriptor["schema_version"] == "1"


def test_invalid_resource_document_is_blocked_during_inspection(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(
        tmp_path, keypair, valid_document=False), keypair)

    plan = pfp_package.inspect_pfp(artifact, user_id="alice")
    row = next(
        item for item in plan["objects"]
        if item["id"] == "repository_resource:luna")
    assert row["status"] == "blocked"
    assert "does not match its schema" in row["reason"]


def test_selecting_resource_without_its_new_descriptor_is_skipped(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(tmp_path, keypair), keypair)

    result = pfp_package.install_pfp(
        artifact, user_id="alice",
        include=["repository_resource:luna"])
    assert result["ok"] is True
    assert result["installed"] == []
    resource_skip = next(
        item for item in result["skipped"]
        if item["id"] == "repository_resource:luna")
    assert resource_skip == {
        "id": "repository_resource:luna",
        "reason": "missing_dependency",
        "missing_repository_type": "repository_type:avatar",
    }
    assert ExtensionRepository.instance().list(
        "example.avatar", user_id="alice", scope="user") == []


def test_dependent_pack_requires_explicit_owner_dependency(tmp_path):
    owner_key = pfp_package.create_signing_key()
    owner = _build(_write_owner_package(
        tmp_path, owner_key, include_resource=False), owner_key)
    assert pfp_package.install_pfp(
        owner, user_id="alice")["ok"] is True

    pack_key = pfp_package.create_signing_key()
    missing_dep = _build(_write_pack(
        tmp_path, pack_key, package_id="examples.bad-pack"), pack_key)
    bad_plan = pfp_package.inspect_pfp(missing_dep, user_id="alice")
    assert bad_plan["objects"][0]["status"] == "blocked"
    assert "must depend on owner package" in bad_plan["objects"][0]["reason"]

    pack = _build(_write_pack(
        tmp_path, pack_key,
        dependencies=["examples.avatar-runtime@1.0.0"]), pack_key)
    plan = pfp_package.inspect_pfp(pack, user_id="alice")
    assert plan["objects"][0]["status"] == "new"
    result = pfp_package.install_pfp(pack, user_id="alice")
    assert result["ok"] is True
    nova = ExtensionRepository.instance().get(
        "example.avatar", "nova", user_id="alice", scope="user")
    assert nova["owner_package"] == "examples.avatar-runtime"
    assert nova["contributor_package"] == "examples.avatar-pack"


def test_pack_reports_missing_dependency_before_owner_is_installed(tmp_path):
    keypair = pfp_package.create_signing_key()
    pack = _build(_write_pack(
        tmp_path, keypair,
        dependencies=["examples.avatar-runtime@1.0.0"]), keypair)
    plan = pfp_package.inspect_pfp(pack, user_id="alice")
    assert plan["objects"][0]["status"] == "missing_dependency"
    assert "examples.avatar-runtime@1.0.0" in (
        plan["objects"][0]["reason"])


def test_second_package_cannot_replace_repository_type_owner(tmp_path):
    owner_key = pfp_package.create_signing_key()
    owner = _build(_write_owner_package(
        tmp_path, owner_key, include_resource=False), owner_key)
    assert pfp_package.install_pfp(
        owner, user_id="alice")["ok"] is True

    other_key = pfp_package.create_signing_key()
    other = _build(_write_owner_package(
        tmp_path, other_key, package_id="examples.other-runtime",
        include_resource=False), other_key)
    plan = pfp_package.inspect_pfp(other, user_id="alice")
    assert plan["objects"][0]["status"] == "blocked"
    assert "owned by examples.avatar-runtime" in (
        plan["objects"][0]["reason"])
    result = pfp_package.install_pfp(
        other, user_id="alice", replace=True,
        include=["repository_type:avatar"])
    assert result["installed"] == []
    assert result["skipped"][0]["reason"] == "blocked"


def test_conversation_scope_is_isolated_from_user_scope(tmp_path):
    store = ExtensionRepository.instance()
    common = {
        "resource_type": "example.avatar",
        "name": "luna",
        "user_id": "alice",
        "schema_version": "1",
        "owner_package": "examples.avatar-runtime",
        "contributor_package": "examples.avatar-runtime",
        "assets": [],
        "installed_from": {},
        "source": "user",
    }
    store.create(
        scope="user", document={"title": "User Luna"}, **common)
    store.create(
        scope="conversation", conversation_id="conv-1",
        document={"title": "Conversation Luna"}, **common)

    assert store.get(
        "example.avatar", "luna", user_id="alice",
        scope="user")["document"]["title"] == "User Luna"
    assert store.get(
        "example.avatar", "luna", user_id="alice",
        scope="conversation",
        conversation_id="conv-1")["document"]["title"] == (
            "Conversation Luna")
    assert store.list(
        "example.avatar", user_id="alice", scope="conversation",
        conversation_id="conv-2") == []
    available = store.list_available(
        "example.avatar", user_id="alice", conversation_id="conv-1")
    assert [item["_scope"] for item in available] == [
        "conversation", "user"]
    assert store.get_available(
        "example.avatar", "luna", user_id="alice",
        conversation_id="conv-1")["document"]["title"] == (
            "Conversation Luna")


def test_runtime_host_crud_is_owner_only_and_schema_validated(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(
        tmp_path, keypair, include_resource=False), keypair)
    assert pfp_package.install_pfp(
        artifact, user_id="alice")["ok"] is True

    from core import pfp_runtime
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "examples.avatar-runtime",
            "object_id": "ui_extension:avatar",
        })
    created = host.handle_host_call({
        "format": pfp_runtime.HOST_CALL_FORMAT,
        "kind": "repository",
        "target": "example.avatar",
        "operation": "create",
        "arguments": {
            "name": "custom",
            "document": {
                "format": "example.avatar.v1",
                "title": "Custom",
            },
        },
    })
    assert created["source"] == "user"
    assert host.execute_repository_call(
        "example.avatar", "get", {"name": "custom"}
    )["document"]["title"] == "Custom"

    with pytest.raises(
            pfp_runtime.PackageRuntimeError, match="does not own"):
        pfp_runtime.PackageRuntimeHost(
            user_id="alice",
            caller_runtime={
                "package": "examples.avatar-pack",
                "object_id": "tool:mutate",
            }).execute_repository_call(
                "example.avatar", "list", {})
    with pytest.raises(ValueError, match="does not match"):
        host.execute_repository_call(
            "example.avatar", "update", {
                "name": "custom",
                "document": {
                    "format": "example.avatar.v1",
                    "title": 9,
                },
            })


def test_relay_sdk_repository_facade_emits_repository_host_calls():
    sdk_path = (
        Path(__file__).resolve().parents[1]
        / "docker" / "pawflow_sdk" / "pawflow.py")
    spec = importlib.util.spec_from_file_location(
        "pawflow_sdk_repository_test", sdk_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    calls = []

    def _host_call(kind, target, **kwargs):
        calls.append((kind, target, kwargs))
        return {"ok": True}

    module.pfp._host_call = _host_call
    assert module.pfp.repository.create(
        "example.avatar", "luna", {"title": "Luna"}) == {"ok": True}
    assert calls == [(
        "repository",
        "example.avatar",
        {
            "operation": "create",
            "arguments": {
                "name": "luna",
                "document": {"title": "Luna"},
            },
        },
    )]


def test_runtime_repository_assets_have_stable_refs_and_authenticated_urls(
        tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(tmp_path, keypair), keypair)
    assert pfp_package.install_pfp(
        artifact, user_id="alice")["ok"] is True

    from core import pfp_runtime
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "examples.avatar-runtime",
            "object_id": "ui_extension:avatar",
        })
    row = host.execute_repository_call("example.avatar", "get", {
        "name": "luna",
    })
    asset = row["assets"][0]
    assert asset["ref"] == "pfp-asset:example.avatar/user/luna/model"
    assert asset["url"].startswith(
        "/chat/ext/examples.avatar-runtime/")
    assert asset["url"].endswith(
        "/__repository__/example.avatar/user/luna/model.vrm")

    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    request = FlowFile(content=b"")
    request.set_attribute("http.path", asset["url"])
    request.set_attribute("http.auth.principal", "alice")
    response = ServePfpExtensionAssetsTask({}).execute(request)[0]
    assert response.get_attribute("http.response.status") == "200"
    assert response.get_content() == b"VRM fixture"

    denied = FlowFile(content=b"")
    denied.set_attribute("http.path", asset["url"])
    denied.set_attribute("http.auth.principal", "bob")
    denied = ServePfpExtensionAssetsTask({}).execute(denied)[0]
    assert denied.get_attribute("http.response.status") == "404"

    stored = ExtensionRepository.instance().get(
        "example.avatar", "luna", user_id="alice", scope="user")
    model = (Path(stored["installed_from"]["content_dir"])
             / stored["assets"][0]["path"])
    model.write_bytes(b"tampered")
    tampered = FlowFile(content=b"")
    tampered.set_attribute("http.path", asset["url"])
    tampered.set_attribute("http.auth.principal", "alice")
    tampered = ServePfpExtensionAssetsTask({}).execute(tampered)[0]
    assert tampered.get_attribute("http.response.status") == "404"


def test_repository_asset_url_is_isolated_to_its_conversation(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(tmp_path, keypair), keypair)
    assert pfp_package.install_pfp(
        artifact, user_id="alice", conversation_id="conv-1",
        scope="conversation")["ok"] is True

    from core import pfp_runtime
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice", conversation_id="conv-1", scope="conversation",
        caller_runtime={
            "package": "examples.avatar-runtime",
            "object_id": "ui_extension:avatar",
        })
    asset = host.execute_repository_call(
        "example.avatar", "get", {"name": "luna"})["assets"][0]
    assert asset["ref"] == (
        "pfp-asset:example.avatar/conversation/luna/model")

    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    allowed = FlowFile(content=b"")
    allowed.set_attribute("http.path", asset["url"])
    allowed.set_attribute("http.auth.principal", "alice")
    allowed.set_attribute("http.cookie.pawflow_conv", "conv-1")
    allowed = ServePfpExtensionAssetsTask({}).execute(allowed)[0]
    assert allowed.get_attribute("http.response.status") == "200"
    assert allowed.get_content() == b"VRM fixture"

    denied = FlowFile(content=b"")
    denied.set_attribute("http.path", asset["url"])
    denied.set_attribute("http.auth.principal", "alice")
    denied.set_attribute("http.cookie.pawflow_conv", "conv-2")
    denied = ServePfpExtensionAssetsTask({}).execute(denied)[0]
    assert denied.get_attribute("http.response.status") == "404"


def test_owner_runtime_can_serve_dependent_pack_assets(tmp_path):
    owner_key = pfp_package.create_signing_key()
    owner = _build(_write_owner_package(
        tmp_path, owner_key, include_resource=False), owner_key)
    assert pfp_package.install_pfp(owner, user_id="alice")["ok"] is True
    pack_key = pfp_package.create_signing_key()
    pack = _build(_write_pack(
        tmp_path, pack_key,
        dependencies=["examples.avatar-runtime@1.0.0"]), pack_key)
    assert pfp_package.install_pfp(pack, user_id="alice")["ok"] is True

    from core import pfp_runtime
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "examples.avatar-runtime",
            "object_id": "ui_extension:avatar",
        })
    rows = host.execute_repository_call("example.avatar", "list", {})
    asset = rows[0]["assets"][0]
    assert "/chat/ext/examples.avatar-pack/" in asset["url"]

    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    request = FlowFile(content=b"")
    request.set_attribute("http.path", asset["url"])
    request.set_attribute("http.auth.principal", "alice")
    response = ServePfpExtensionAssetsTask({}).execute(request)[0]
    assert response.get_attribute("http.response.status") == "200"
    assert response.get_content() == b"Nova VRM fixture"

    assert pfp_package.uninstall_pfp(
        "examples.avatar-pack", user_id="alice")["ok"] is True
    missing = FlowFile(content=b"")
    missing.set_attribute("http.path", asset["url"])
    missing.set_attribute("http.auth.principal", "alice")
    missing = ServePfpExtensionAssetsTask({}).execute(missing)[0]
    assert missing.get_attribute("http.response.status") == "404"


def test_uninstall_keeps_descriptor_while_user_resource_exists(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = _build(_write_owner_package(tmp_path, keypair), keypair)
    assert pfp_package.install_pfp(
        artifact, user_id="alice")["ok"] is True
    host = __import__(
        "core.pfp_runtime", fromlist=["PackageRuntimeHost"]
    ).PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "examples.avatar-runtime",
            "object_id": "ui_extension:avatar",
        })
    host.execute_repository_call(
        "example.avatar", "create", {
            "name": "custom",
            "document": {
                "format": "example.avatar.v1",
                "title": "Custom",
            },
        })

    result = pfp_package.uninstall_pfp(
        "examples.avatar-runtime", user_id="alice")
    assert result["ok"] is False
    assert [item["kind"] for item in result["kept"]] == [
        "repository_type"]
    assert pfp_package.resolve_repository_type(
        "example.avatar", user_id="alice") is not None
    assert ExtensionRepository.instance().get(
        "example.avatar", "custom", user_id="alice",
        scope="user") is not None

    forced = pfp_package.uninstall_pfp(
        "examples.avatar-runtime", user_id="alice", force=True)
    assert forced["ok"] is True
    assert pfp_package.resolve_repository_type(
        "example.avatar", user_id="alice") is None
    assert ExtensionRepository.instance().get(
        "example.avatar", "custom", user_id="alice",
        scope="user") is not None
