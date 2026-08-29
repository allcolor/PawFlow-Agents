"""Website Creator immutable template catalog and safe extraction tests."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from core import FlowFile
from core.website_creator_templates import (
    MAX_TEMPLATE_ARCHIVE_BYTES,
    TEMPLATE_CATALOG,
    TEMPLATE_CATALOG_VERSION,
    resolve_template,
    template_cache_identity,
    validate_template_catalog,
)
from tasks.ai.workflow.website_creator_template import DownloadTemplateTask


sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
from fs_archive import action_extract_zip_subtree  # noqa: E402


_WORKSPACE = "/workspace/pawflow-sites/run-1"
_CREATIVE_REF = "startbootstrap:creative:7.0.7"


def _zip(path: Path, entries: dict[str, bytes], *, symlink: str = "") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name == symlink:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, content)


class _TemplateRelay:
    _service_id = "relay-template"

    def __init__(self, *, returned_sha256: str = ""):
        self.files: dict[str, bytes] = {}
        self.fetch_calls: list[tuple[str, str, dict]] = []
        self.extract_calls: list[tuple[str, str, dict]] = []
        self.deleted: list[str] = []
        self.returned_sha256 = returned_sha256

    def mkdir(self, _path, local=False):
        assert local is False

    def exists(self, path, local=False):
        assert local is False
        return path in self.files

    def read_file(self, path, local=False):
        assert local is False
        return self.files[path]

    def atomic_write_file(self, path, content, local=False):
        assert local is False
        self.files[path] = bytes(content)
        return {"written": len(content)}

    def delete_file(self, path, local=False):
        assert local is False
        self.deleted.append(path)
        self.files.pop(path, None)

    def http_fetch_to_file(self, url, path, **kwargs):
        self.fetch_calls.append((url, path, kwargs))
        digest = self.returned_sha256 or resolve_template(_CREATIVE_REF)["sha256"]
        self.files[path] = b"bounded archive placeholder"
        return {
            "saved": True,
            "status": 200,
            "bytes": 2_454_821,
            "sha256": digest,
            "url": url,
        }

    def extract_zip_subtree(self, path, dest_path, **kwargs):
        self.extract_calls.append((path, dest_path, kwargs))
        return {
            "files": 47,
            "bytes": 6_000_000,
            "sha256": "a" * 64,
            "artifact_root": kwargs["artifact_root"],
        }


def _flowfile(template_ref: str = _CREATIVE_REF) -> FlowFile:
    return FlowFile(content=json.dumps({
        "website": {
            "source_url": "https://example.com/",
            "template_url": template_ref,
            "workspace": _WORKSPACE,
            "status": "prepared",
        },
    }).encode("utf-8"))


def _bind(task: DownloadTemplateTask, relay: _TemplateRelay) -> DownloadTemplateTask:
    task._website_fs_service = relay
    return task


def test_shipped_catalog_is_immutable_and_has_verified_archive_metadata():
    validate_template_catalog(TEMPLATE_CATALOG)
    creative = resolve_template(_CREATIVE_REF)
    assert creative == {
        "provider": "startbootstrap",
        "name": "creative",
        "version": "7.0.7",
        "package_url": (
            "https://codeload.github.com/StartBootstrap/startbootstrap-creative/zip/"
            "b1762d8c690a2379c078c776dc0830bdd81c6f55"
        ),
        "sha256": "085435879015b0afe2b2adb4bdcd1226aa9cd4ac6e72e979c48e35e190875eef",
        "license": "MIT",
        "attribution": "Creative 7.0.7 by Start Bootstrap",
        "artifact_root": (
            "startbootstrap-creative-"
            "b1762d8c690a2379c078c776dc0830bdd81c6f55/dist"
        ),
    }
    assert resolve_template("html5up:identity:be7721e3")["sha256"] == (
        "1462b4240fbddfea1bf97cd37fb305f0ddd5eeb00d19d74a0d12f8ae537a9301"
    )


def test_catalog_rejects_mutable_urls_and_preview_only_providers():
    mutable = [{
        **resolve_template(_CREATIVE_REF),
        "package_url": (
            "https://codeload.github.com/StartBootstrap/startbootstrap-creative/zip/master"
        ),
    }]
    with pytest.raises(ValueError, match="immutable"):
        validate_template_catalog(mutable)
    with pytest.raises(ValueError, match="preview-only"):
        resolve_template("themewagon:unknown:latest")


def test_template_cache_identity_includes_catalog_version_and_package_hash():
    entry = resolve_template(_CREATIVE_REF)
    first = template_cache_identity(entry)
    assert first == template_cache_identity(dict(entry))
    assert first != template_cache_identity({**entry, "sha256": "f" * 64})
    assert first != template_cache_identity(entry, catalog_version="next")
    assert TEMPLATE_CATALOG_VERSION in first["inputs"]


def test_download_template_streams_bounded_archive_extracts_root_and_propagates_license():
    relay = _TemplateRelay()
    flowfile = _flowfile()
    _bind(DownloadTemplateTask({}), relay).execute(flowfile)

    entry = resolve_template(_CREATIVE_REF)
    assert relay.fetch_calls == [(
        entry["package_url"],
        f"{_WORKSPACE}/template/.archive.zip",
        {
            "headers": {"User-Agent": "PawFlow Website Creator"},
            "timeout": 300,
            "max_bytes": MAX_TEMPLATE_ARCHIVE_BYTES,
            "public_only": True,
            "local": False,
        },
    )]
    assert relay.extract_calls == [(
        f"{_WORKSPACE}/template/.archive.zip",
        f"{_WORKSPACE}/template/content",
        {"artifact_root": entry["artifact_root"], "local": False},
    )]
    notice = relay.files[f"{_WORKSPACE}/site/THIRD_PARTY_NOTICES.txt"].decode()
    assert "Creative 7.0.7 by Start Bootstrap" in notice
    assert "MIT" in notice
    assert entry["package_url"] in notice
    state = json.loads(flowfile.get_content())["website"]["template"]
    assert state["sha256"] == entry["sha256"]
    assert state["catalog_version"] == TEMPLATE_CATALOG_VERSION
    assert state["artifact_root"] == entry["artifact_root"]


def test_download_template_replays_matching_identity_and_invalidates_stale_identity():
    relay = _TemplateRelay()
    flowfile = _flowfile()
    task = _bind(DownloadTemplateTask({}), relay)
    task.execute(flowfile)
    task.execute(flowfile)
    assert len(relay.fetch_calls) == 1

    state = json.loads(flowfile.get_content())
    state["website"]["template"]["cache_identity"] = {"digest": "stale"}
    flowfile.set_content(json.dumps(state).encode("utf-8"))
    task.execute(flowfile)
    assert len(relay.fetch_calls) == 2


def test_download_template_hash_mismatch_removes_archive_before_extraction():
    relay = _TemplateRelay(returned_sha256="0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        _bind(DownloadTemplateTask({}), relay).execute(_flowfile())
    archive = f"{_WORKSPACE}/template/.archive.zip"
    assert relay.deleted == [archive]
    assert relay.extract_calls == []


def test_extract_zip_subtree_confines_artifact_root_and_replaces_atomically(tmp_path):
    archive = tmp_path / "template.zip"
    _zip(archive, {
        "repo/dist/index.html": b"<h1>new</h1>",
        "repo/dist/assets/site.css": b"body{}",
        "repo/package.json": b"{}",
        "repo/LICENSE": b"MIT",
    })
    destination = tmp_path / "site"
    destination.mkdir()
    (destination / "index.html").write_text("old", encoding="utf-8")

    result = action_extract_zip_subtree(str(tmp_path), str(archive), {
        "dest_path": "site",
        "artifact_root": "repo/dist",
    })

    assert result["files"] == 2
    assert result["sha256"] == hashlib.sha256(
        b"assets/site.css\0" + hashlib.sha256(b"body{}").digest()
        + b"index.html\0" + hashlib.sha256(b"<h1>new</h1>").digest()
    ).hexdigest()
    assert (destination / "index.html").read_text(encoding="utf-8") == "<h1>new</h1>"
    assert (destination / "assets" / "site.css").is_file()
    assert not (destination / "package.json").exists()
    assert not (destination / "LICENSE").exists()
    assert not list(tmp_path.glob("*.extracting-*"))
    assert not list(tmp_path.glob("*.backup-*"))


@pytest.mark.parametrize(
    ("entry_name", "symlink", "message"),
    [
        ("repo/dist/../../escape.txt", "", "unsafe archive path"),
        ("repo/dist/link", "repo/dist/link", "symlink"),
    ],
)
def test_extract_zip_subtree_rejects_traversal_and_symlinks(
    tmp_path, entry_name, symlink, message,
):
    archive = tmp_path / "attack.zip"
    _zip(archive, {entry_name: b"payload"}, symlink=symlink)
    with pytest.raises(ValueError, match=message):
        action_extract_zip_subtree(str(tmp_path), str(archive), {
            "dest_path": "out",
            "artifact_root": "repo/dist",
        })
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "out").exists()


def test_extract_zip_subtree_rejects_size_ratio_file_count_and_missing_root(tmp_path):
    archive = tmp_path / "bounded.zip"
    _zip(archive, {
        "repo/dist/a.txt": bytes(range(256)) * 40,
        "repo/dist/b.txt": b"b",
    })
    with pytest.raises(ValueError, match="compression ratio"):
        action_extract_zip_subtree(str(tmp_path), str(archive), {
            "dest_path": "ratio",
            "artifact_root": "repo/dist",
            "max_compression_ratio": 2,
        })
    with pytest.raises(ValueError, match="total size"):
        action_extract_zip_subtree(str(tmp_path), str(archive), {
            "dest_path": "size",
            "artifact_root": "repo/dist",
            "max_total_bytes": 100,
        })
    with pytest.raises(ValueError, match="file count"):
        action_extract_zip_subtree(str(tmp_path), str(archive), {
            "dest_path": "count",
            "artifact_root": "repo/dist",
            "max_files": 1,
        })
    with pytest.raises(ValueError, match="artifact_root"):
        action_extract_zip_subtree(str(tmp_path), str(archive), {
            "dest_path": "missing",
            "artifact_root": "repo/other",
        })
