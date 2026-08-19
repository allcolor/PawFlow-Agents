"""Release-builder coverage for official bundled PFP artifacts."""

from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

from core import pfp_package


SCRIPT = Path("scripts/build-bundled-pfps.py")


def _load_builder():
    spec = importlib.util.spec_from_file_location("bundled_pfp_builder", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_official_key_creation_stores_only_the_private_half(
        tmp_path, monkeypatch):
    import core.paths as paths
    from core.config_store import ConfigStore

    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    result = pfp_package.create_stored_signing_key(
        "PAWFLOW_PFP_SIGNING_KEY", "alice")

    assert set(result) == {"ok", "secret_name", "public_key"}
    assert result["ok"] is True
    assert result["public_key"].startswith("ed25519:")
    stored = ConfigStore.load_secrets(paths.user_secrets_path("alice"))
    assert str(stored["PAWFLOW_PFP_SIGNING_KEY"]).startswith("ed25519:")

    try:
        pfp_package.create_stored_signing_key(
            "PAWFLOW_PFP_SIGNING_KEY", "alice")
    except pfp_package.PfpError as exc:
        assert "refusing key rotation" in str(exc)
    else:
        raise AssertionError("an official signing key must never be overwritten")


def test_bundled_avatar_catalog_build_is_signed_and_reproducible(
        tmp_path, monkeypatch):
    builder = _load_builder()
    sources = tmp_path / "sources"
    package_specs = []
    for spec in builder.PACKAGE_SPECS:
        source = sources / spec["source"].name
        shutil.copytree(spec["source"], source)
        manifest_path = source / "pfp.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("developer", {})["public_key"] = ""
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        package_specs.append({**spec, "source": source})
    builder.PACKAGE_SPECS = tuple(package_specs)
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    index_path = bundled / "index.json"
    index_path.write_text(json.dumps({
        "format": "pawflow.bundled-packages.v1",
        "packages": [{
            "package": "keep.example",
            "version": "1.0.0",
            "artifact": "keep.example-1.0.0.pfp",
        }],
    }), encoding="utf-8")
    builder.BUNDLED_DIR = bundled
    builder.INDEX_PATH = index_path

    keypair = pfp_package.create_signing_key()
    monkeypatch.setenv("TEST_PFP_KEY", keypair["private_key"])
    first = builder.build_catalog(bundled, private_key_env="TEST_PFP_KEY")
    index_path.write_bytes(builder._json_bytes(first))
    first_bytes = {
        path.name: path.read_bytes() for path in bundled.glob("*.pfp")
    }

    rebuilt = tmp_path / "rebuilt"
    second = builder.build_catalog(rebuilt, private_key_env="TEST_PFP_KEY")
    second_bytes = {
        path.name: path.read_bytes() for path in rebuilt.glob("*.pfp")
    }

    assert first == second
    assert first_bytes == second_bytes
    assert builder.verify_catalog(bundled) == []
    assert [row["package"] for row in first["packages"]] == [
        "keep.example",
        "pawflow.avatar-runtime",
        "pawflow.avatar-helper",
        "pawflow.avatar-pack.starter",
        "pawflow.comfyui-operator",
        "pawflow.pixazo-provider",
        "pawflow.wavespeed-provider",
        "pawflow.kling-provider",
    ]
    for row in first["packages"][1:]:
        assert row["developer_key"] == keypair["public_key"]
        assert keypair["private_key"] not in json.dumps(row)
        with zipfile.ZipFile(bundled / row["artifact"]) as archive:
            assert all("__pycache__" not in name for name in archive.namelist())
            assert all(not name.endswith((".pyc", ".pyo"))
                       for name in archive.namelist())
            lock = json.loads(archive.read("pfp.lock.json"))
            assert lock["generated_at"] == 0
