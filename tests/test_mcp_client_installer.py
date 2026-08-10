"""Tests for the universal PawFlow MCP client installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(filename: str, module_name: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _fake_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    package = bundle / "runtime" / "pawflow_relay"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mcp_stdio.py").write_text("def main(argv=None): return 0\n", encoding="utf-8")
    (bundle / "runtime" / "tools").mkdir(exist_ok=True)
    (bundle / "launcher.py").write_text(
        (ROOT / "scripts" / "mcp-client-launcher.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return bundle


def _request(installer, relay_dir: Path):
    return installer.InstallRequest(
        name="pawflow-work",
        url="https://pawflow.example/mcp/srv_test",
        api_key="mcp-secret-value",
        gateway_key="gateway-secret-value",
        relay_dir=relay_dir,
        clients=("cc", "codex", "agy"),
        readonly=True,
        allow_exec=False,
    )


def test_installer_merges_all_clients_without_copying_secrets(tmp_path):
    installer = _load_script("install-mcp-client.py", "pawflow_mcp_client_installer_test")
    home = tmp_path / "home"
    home.mkdir()
    relay_dir = tmp_path / "project"
    relay_dir.mkdir()
    bundle = _fake_bundle(tmp_path)
    install_root = tmp_path / "installed"

    (home / ".claude.json").write_text(
        json.dumps({"keep": True, "mcpServers": {"other": {"command": "other"}}}),
        encoding="utf-8",
    )
    result = installer.install(
        _request(installer, relay_dir),
        bundle_root=bundle,
        install_root=install_root,
        home=home,
        python=sys.executable,
    )

    profile_path = Path(result["profile"])
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["api_key"] == "mcp-secret-value"
    assert profile["gateway_key"] == "gateway-secret-value"
    assert profile["relay_dir"] == str(relay_dir.resolve())
    if os.name != "nt":
        assert stat.S_IMODE(profile_path.stat().st_mode) == 0o600

    claude = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert claude["keep"] is True
    assert claude["mcpServers"]["other"]["command"] == "other"
    claude_entry = claude["mcpServers"]["pawflow-work"]
    assert claude_entry["type"] == "stdio"
    assert claude_entry["command"] == str(Path(sys.executable).resolve())
    assert "--profile" in claude_entry["args"]

    codex = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert codex.count("# BEGIN PAWFLOW MCP: pawflow-work") == 1
    assert '[mcp_servers."pawflow-work"]' in codex

    agy_settings = json.loads(
        (home / ".gemini" / "antigravity-cli" / "settings.json").read_text(
            encoding="utf-8"))
    assert agy_settings["allowMCPServers"] == ["pawflow-work"]
    assert agy_settings["mcp"]["allowed"] == ["pawflow-work"]
    assert agy_settings["permissions"]["allow"] == [
        "mcp(pawflow-work/*)", "mcp_pawflow-work_*"]
    agy_list = json.loads(
        (home / ".gemini" / "antigravity-cli" / "mcp_config.json").read_text(
            encoding="utf-8"))
    assert agy_list["mcpServers"][0]["serverName"] == "pawflow-work"
    assert agy_list["mcpServers"][0]["disabled"] is False

    client_text = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in result["configs"])
    assert "mcp-secret-value" not in client_text
    assert "gateway-secret-value" not in client_text
    assert len(result["backups"]) == 1
    assert Path(result["backups"][0]).name.startswith(".claude.json.bak-")

    repeated = installer.install(
        _request(installer, relay_dir),
        bundle_root=bundle,
        install_root=install_root,
        home=home,
        python=sys.executable,
    )
    assert repeated["backups"] == []
    assert (home / ".codex" / "config.toml").read_text(
        encoding="utf-8").count("# BEGIN PAWFLOW MCP: pawflow-work") == 1
    agy_repeated = json.loads(
        (home / ".gemini" / "antigravity-cli" / "mcp_config.json").read_text(
            encoding="utf-8"))
    assert len(agy_repeated["mcpServers"]) == 1


def test_installer_refuses_invalid_endpoint_and_unmanaged_codex_table(tmp_path):
    installer = _load_script("install-mcp-client.py", "pawflow_mcp_client_installer_invalid")
    relay_dir = tmp_path / "project"
    relay_dir.mkdir()
    request = installer.InstallRequest(
        name="pawflow",
        url="https://pawflow.example/not-mcp",
        api_key="key",
        gateway_key="",
        relay_dir=relay_dir,
        clients=("codex",),
    )
    with pytest.raises(ValueError, match="/mcp/"):
        installer.install(
            request,
            bundle_root=_fake_bundle(tmp_path),
            install_root=tmp_path / "installed",
            home=tmp_path / "home",
        )

    home = tmp_path / "valid-home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[mcp_servers.pawflow]\ncommand = \"other\"\n", encoding="utf-8")
    valid = installer.InstallRequest(
        name="pawflow",
        url="https://pawflow.example/mcp/srv_test",
        api_key="key",
        gateway_key="",
        relay_dir=relay_dir,
        clients=("codex",),
    )
    with pytest.raises(ValueError, match="unmanaged"):
        installer.install(
            valid,
            bundle_root=_fake_bundle(tmp_path),
            install_root=tmp_path / "installed-valid",
            home=home,
        )


def test_relay_home_ownership_check_is_a_noop_without_geteuid(monkeypatch):
    from pawflow_relay import cli

    monkeypatch.delattr(cli.os, "geteuid")
    cli._ensure_home_writable()


def test_mcp_relay_subprocess_receives_bundled_runtime_path(monkeypatch, tmp_path):
    from pawflow_relay.mcp_stdio import HTTPBridge, RelayController

    class Bridge(HTTPBridge):
        def __init__(self):
            super().__init__("https://pawflow.example/mcp/srv_test", "secret")

        def control(self, action, payload=None):
            if action == "connect":
                return {
                    "relay_id": "mcp-cli-test",
                    "ws_url": "wss://pawflow.example/ws/relay/mcp-cli-test",
                    "relay_token": "relay-secret",
                }
            return {"connected": True, "relay_id": "mcp-cli-test"}

    captured = {}

    class Process:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs["env"]
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("pawflow_relay.mcp_stdio.subprocess.Popen", Process)
    controller = RelayController(Bridge(), tmp_path, "Codex")
    controller.connect()
    try:
        runtime_root = str((ROOT / "pawflow_relay").parent)
        assert captured["env"]["PAWFLOW_RELAY_RUNTIME_ROOT"] == runtime_root
        assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == runtime_root
        assert "relay-secret" not in " ".join(captured["argv"])
        assert str(tmp_path) not in " ".join(captured["argv"])
    finally:
        controller.close()


def test_builder_creates_reproducible_safe_zip_and_tar(tmp_path):
    builder = _load_script(
        "build-mcp-client-installer.py", "pawflow_mcp_client_builder_test")
    version = "1.2.3-test"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = builder.build(version, first_dir)
    second = builder.build(version, second_dir)

    assert [path.name for path in first] == [
        f"pawflow-mcp-client-{version}.zip",
        f"pawflow-mcp-client-{version}.tar.gz",
    ]
    for left, right in zip(first, second):
        assert hashlib.sha256(left.read_bytes()).digest() == hashlib.sha256(
            right.read_bytes()).digest()

    root = f"pawflow-mcp-client-{version}"
    expected = {
        f"{root}/install.py",
        f"{root}/install.sh",
        f"{root}/install.cmd",
        f"{root}/install.ps1",
        f"{root}/launcher.py",
        f"{root}/README.md",
        f"{root}/LICENSE",
        f"{root}/VERSION",
        f"{root}/runtime/pawflow_relay/mcp_stdio.py",
        f"{root}/runtime/tools/fs_actions.py",
    }
    with zipfile.ZipFile(first[0]) as archive:
        names = set(archive.namelist())
        assert expected <= names
        assert all(not name.startswith("/") and ".." not in Path(name).parts
                   for name in names)
        assert all("__pycache__" not in name and not name.endswith(".pyc")
                   for name in names)
    with tarfile.open(first[1], "r:gz") as archive:
        names = set(archive.getnames())
        assert expected <= names
        assert all(not name.startswith("/") and ".." not in Path(name).parts
                   for name in names)


def test_release_workflow_and_website_publish_mcp_client_archives():
    workflow = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8")
    site = (ROOT / "pawflow-website" / "site.js").read_text(encoding="utf-8")
    howto = (ROOT / "pawflow-website" / "howtos.html").read_text(
        encoding="utf-8")
    docs_hub = (ROOT / "pawflow-website" / "docs.html").read_text(
        encoding="utf-8")

    assert "python scripts/build-mcp-client-installer.py --version" in workflow
    assert "dist/mcp-client-installers/*.zip" in workflow
    assert "dist/mcp-client-installers/*.tar.gz" in workflow
    assert "mcpClientZip" in site and "mcpClientTar" in site
    assert 'id="published-mcp-client"' in howto
    assert 'data-release-download="mcpClientZip"' in howto
    assert "MCP_CLIENT_INSTALLER.md" in howto
    assert "howtos.html#published-mcp-client" in docs_hub
