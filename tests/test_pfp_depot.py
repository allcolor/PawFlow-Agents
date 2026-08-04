"""Tests for the permanent per-user PFP depot."""

import json
from pathlib import Path

import pytest

from core import pfp_depot, pfp_package, pfp_registry
from core.file_store import FileStore


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    import core.paths as paths

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", store)
    monkeypatch.setattr(pfp_registry, "list_bundled_packages", lambda: [])


@pytest.fixture(autouse=True)
def _mock_llm_review(monkeypatch):
    import core.package_review as package_review

    class _ReviewLLM:
        def complete(self, **kwargs):
            class _Response:
                content = json.dumps({
                    "risk": "low",
                    "allowed": True,
                    "requires_human_review": False,
                    "findings": [],
                    "sanitized_summary": "ok",
                    "recommended_changes": [],
                })
            return _Response()

    monkeypatch.setattr(
        package_review, "_resolve_review_llm",
        lambda user_id, conversation_id: (_ReviewLLM(), None, "review_llm"))


def _build_package(root: Path, *, marker: str = "one") -> Path:
    keypair = pfp_package.create_signing_key()
    package_dir = root / ("source-" + marker + ".pfpdir")
    content_dir = package_dir / "content" / "service-templates"
    content_dir.mkdir(parents=True)
    (content_dir / "fixture.json").write_text(json.dumps({
        "format": "pawflow.service-template.v1",
        "title": "Depot fixture " + marker,
        "service_type": "llmConnection",
        "config": {"provider": "__depot_test_" + marker + "__"},
    }), encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": "examples.depot-fixture",
        "version": "1.0.0",
        "description": "Depot fixture " + marker,
        "category": "Test fixtures",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [{
            "id": "service_template:fixture",
            "type": "service_template",
            "name": "fixture",
            "path": "content/service-templates/fixture.json",
        }],
    }
    (package_dir / "pfp.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    built = pfp_package.build_pfp(
        str(package_dir), private_key=keypair["private_key"])
    return Path(built["path"])


def _upload(path: Path, *, user_id: str = "alice") -> str:
    return FileStore.instance().store(
        path.name, path.read_bytes(), "application/octet-stream",
        conversation_id="_upload", user_id=user_id, category="upload")


def test_add_list_resolve_and_delete_uploaded_package(tmp_path):
    artifact = _build_package(tmp_path)
    added = pfp_depot.add_upload(_upload(artifact), user_id="alice")

    assert added["ok"] is True
    assert added["already_present"] is False
    row = added["package"]
    assert row["package"] == "examples.depot-fixture"
    assert row["category"] == "Test fixtures"
    assert row["verified"] is True
    assert row["source"] == "uploaded"
    assert row["deletable"] is True
    assert row["ref"].startswith("depot:")

    listed = pfp_depot.list_packages(user_id="alice")
    assert [item["depot_id"] for item in listed["packages"]] == [row["depot_id"]]
    resolved = pfp_registry.resolve_package_path(row["ref"], user_id="alice")
    assert Path(resolved["path"]).is_file()
    assert resolved["source"] == "depot"
    assert resolved["sha256"] == row["sha256"]

    deleted = pfp_depot.delete_package(row["depot_id"], user_id="alice")
    assert deleted["deleted"] is True
    assert pfp_depot.list_packages(user_id="alice")["packages"] == []


def test_depot_is_isolated_by_user(tmp_path):
    artifact = _build_package(tmp_path)
    row = pfp_depot.add_upload(
        _upload(artifact, user_id="alice"), user_id="alice")["package"]

    assert pfp_depot.list_packages(user_id="bob")["packages"] == []
    with pytest.raises(pfp_depot.PfpDepotError, match="not found"):
        pfp_depot.resolve_ref(row["ref"], user_id="bob")
    with pytest.raises(pfp_depot.PfpDepotError, match="not found"):
        pfp_depot.delete_package(row["depot_id"], user_id="bob")


def test_duplicate_is_idempotent_but_same_version_conflict_is_rejected(tmp_path):
    first = _build_package(tmp_path, marker="one")
    first_id = _upload(first)
    added = pfp_depot.add_upload(first_id, user_id="alice")
    duplicate = pfp_depot.add_upload(first_id, user_id="alice")

    assert duplicate["already_present"] is True
    assert duplicate["package"]["depot_id"] == added["package"]["depot_id"]

    conflicting = _build_package(tmp_path, marker="two")
    with pytest.raises(pfp_depot.PfpDepotError, match="different contents"):
        pfp_depot.add_upload(_upload(conflicting), user_id="alice")


@pytest.mark.parametrize("filename,content,error", [
    ("package.zip", b"not a package", r"\.pfp extension"),
    ("package.pfp", b"not a package", "File is not a zip file"),
])
def test_invalid_upload_never_enters_depot(filename, content, error):
    file_id = FileStore.instance().store(
        filename, content, "application/octet-stream",
        conversation_id="_upload", user_id="alice")

    with pytest.raises(pfp_depot.PfpDepotError, match=error):
        pfp_depot.add_upload(file_id, user_id="alice")
    assert pfp_depot.list_packages(user_id="alice")["packages"] == []


def test_bundled_catalog_rows_are_read_only(monkeypatch):
    monkeypatch.setattr(pfp_registry, "list_bundled_packages", lambda: [{
        "package": "pawflow.bundled",
        "version": "1.0.0",
        "ref": "pawflow.bundled@1.0.0",
    }])

    row = pfp_depot.list_packages(user_id="alice")["packages"][0]
    assert row["source"] == "bundled"
    assert row["deletable"] is False
    with pytest.raises(pfp_depot.PfpDepotError):
        pfp_depot.delete_package(row["ref"], user_id="alice")


@pytest.mark.parametrize("depot_id", ["../other.pfp", "bundled:x", "", "x.zip"])
def test_delete_rejects_non_depot_identifiers(depot_id):
    with pytest.raises(pfp_depot.PfpDepotError):
        pfp_depot.delete_package(depot_id, user_id="alice")
