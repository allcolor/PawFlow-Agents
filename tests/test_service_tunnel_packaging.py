"""Service Tunnel FRP packaging contracts."""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
FRP_VERSION = "0.70.1"
LINUX_AMD64_SHA256 = "333da23d1b9009d7c01638e9ba38cf4600f7d37d393f854e96ee1396adefa9a6"
WINDOWS_AMD64_SHA256 = "531f3cd3cc41c0b4f077b54fe6b7dd83c0ff727e7f0bf412a4c78fa279165de5"


def _relay_builder():
    path = ROOT / "scripts" / "build-relay-cli-installer.py"
    spec = importlib.util.spec_from_file_location("relay_cli_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows_amd64"),
        ("Linux", "x86_64", "linux_amd64"),
        ("Linux", "aarch64", "linux_arm64"),
        ("Darwin", "arm64", "darwin_arm64"),
    ],
)
def test_relay_cli_selects_pinned_frp_asset(monkeypatch, system, machine, expected):
    builder = _relay_builder()
    monkeypatch.setattr(builder.platform, "system", lambda: system)
    monkeypatch.setattr(builder.platform, "machine", lambda: machine)

    platform_name, extension, digest = builder._frp_asset()

    assert builder.FRP_VERSION == FRP_VERSION
    assert platform_name == expected
    assert extension in {"zip", "tar.gz"}
    assert len(digest) == 64


def test_relay_cli_installer_bundles_frpc():
    source = (ROOT / "scripts" / "build-relay-cli-installer.py").read_text(
        encoding="utf-8")

    assert WINDOWS_AMD64_SHA256 in source
    assert LINUX_AMD64_SHA256 in source
    assert 'bin/frpc" "$target/frpc"' in source
    assert '"bin\\frpc.exe"' in source
    assert "download_frpc(binary.parent)" in source


def test_relay_desktop_downloads_and_verifies_frpc():
    package = (ROOT / "pawflow-relay-desktop" / "package.json").read_text(
        encoding="utf-8")
    downloader = (
        ROOT / "pawflow-relay-desktop" / "scripts" / "download-frpc.js"
    ).read_text(encoding="utf-8")
    portable = (
        ROOT / "pawflow-relay-desktop" / "scripts" / "package-portable.js"
    ).read_text(encoding="utf-8")

    assert "download-frpc.js" in package
    assert "download-frpc.js" in portable
    assert f"VERSION = '{FRP_VERSION}'" in downloader
    assert WINDOWS_AMD64_SHA256 in downloader
    assert LINUX_AMD64_SHA256 in downloader
    assert "checksum mismatch" in downloader
    assert "runtime', 'bin'" in downloader


def test_managed_relay_image_verifies_frpc():
    dockerfile = (ROOT / "docker" / "relay-dev" / "Dockerfile").read_text(
        encoding="utf-8")

    assert f"ARG FRP_VERSION={FRP_VERSION}" in dockerfile
    assert WINDOWS_AMD64_SHA256 not in dockerfile
    assert LINUX_AMD64_SHA256 in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "/usr/local/bin/frpc" in dockerfile
