"""Tests for the universal PawFlow MCP client installer."""

from __future__ import annotations

import hashlib
import importlib.util
import io
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
    (bundle / "client.py").write_text(
        (ROOT / "scripts" / "mcp-session-launcher.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (bundle / "hook.py").write_text(
        (ROOT / "scripts" / "mcp-client-hook.py").read_text(encoding="utf-8"),
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


def test_installer_creates_instance_bundle_without_global_config(tmp_path):
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
    original_claude = (home / ".claude.json").read_text(encoding="utf-8")
    result = installer.install(
        _request(installer, relay_dir),
        bundle_root=bundle,
        install_root=install_root,
        python=sys.executable,
    )

    profile_path = Path(result["profile"])
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["api_key"] == "mcp-secret-value"
    assert profile["gateway_key"] == "gateway-secret-value"
    assert profile["relay_dir"] == str(relay_dir.resolve())
    if os.name != "nt":
        assert stat.S_IMODE(profile_path.stat().st_mode) == 0o600

    assert (home / ".claude.json").read_text(encoding="utf-8") == original_claude
    assert not (home / ".codex").exists()
    assert not (home / ".gemini").exists()

    session_path = Path(result["session"])
    session = json.loads(session_path.read_text(encoding="utf-8"))
    assert session["name"] == "pawflow-work"
    assert session["clients"] == ["cc", "codex", "agy"]
    claude = json.loads(
        Path(session["configs"]["cc"]).read_text(encoding="utf-8"))
    claude_entry = claude["mcpServers"]["pawflow-work"]
    assert claude_entry["type"] == "stdio"
    assert claude_entry["command"] == str(Path(sys.executable).resolve())
    assert "--profile" in claude_entry["args"]
    assert Path(session["configs"]["agy_home"]).parent == session_path.parent
    assert Path(session["configs"]["codex_home"]).parent == session_path.parent
    assert Path(session["terminal"]["marker_path"]).parent == session_path.parent
    claude_settings = json.loads(
        Path(session["configs"]["cc_settings"]).read_text(encoding="utf-8"))
    assert set(claude_settings["hooks"]) == {
        "SessionStart", "UserPromptSubmit", "Stop"}
    codex_hooks = json.loads(
        (Path(session["configs"]["codex_home"]) / "hooks.json").read_text(
            encoding="utf-8"))
    assert set(codex_hooks["hooks"]) == {
        "SessionStart", "UserPromptSubmit", "Stop"}
    agy_settings = json.loads(
        (Path(session["configs"]["agy_home"]) / ".gemini"
         / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    assert agy_settings["allowMCPServers"] == ["pawflow-work"]
    assert agy_settings["mcp"]["allowed"] == ["pawflow-work"]
    assert agy_settings["permissions"]["allow"] == [
        "mcp(pawflow-work/*)", "mcp_pawflow-work_*"]
    assert set(agy_settings["hooks"]) == {"PreInvocation", "Stop"}
    client_text = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in result["configs"])
    assert "mcp-secret-value" not in client_text
    assert "gateway-secret-value" not in client_text
    assert set(result["launch_commands"]) == {"cc", "codex", "agy"}

    repeated = installer.install(
        _request(installer, relay_dir),
        bundle_root=bundle,
        install_root=install_root,
        python=sys.executable,
    )
    assert repeated["session"] == result["session"]
    assert (home / ".claude.json").read_text(encoding="utf-8") == original_claude


def test_installer_refuses_invalid_endpoint_but_ignores_global_codex(tmp_path):
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
    original = config.read_text(encoding="utf-8")
    result = installer.install(
        valid,
        bundle_root=_fake_bundle(tmp_path),
        install_root=tmp_path / "installed-valid",
    )
    assert Path(result["session"]).is_file()
    assert config.read_text(encoding="utf-8") == original


def test_installer_generates_native_profiles_for_additional_harnesses(tmp_path):
    installer = _load_script(
        "install-mcp-client.py", "pawflow_mcp_additional_harness_installer")
    relay_dir = tmp_path / "project"
    relay_dir.mkdir()
    request = installer.InstallRequest(
        name="pawflow",
        url="https://pawflow.example/mcp/srv_test",
        api_key="private-key",
        gateway_key="gateway-key",
        relay_dir=relay_dir,
        clients=("opencode", "jcode", "pi", "hermes"),
    )

    result = installer.install(
        request, bundle_root=_fake_bundle(tmp_path),
        install_root=tmp_path / "installed", python=sys.executable)
    session = json.loads(Path(result["session"]).read_text(encoding="utf-8"))

    assert set(result["launch_commands"]) == {
        "opencode", "jcode", "pi", "hermes"}
    opencode_home = Path(session["configs"]["opencode_home"])
    opencode = json.loads(
        (opencode_home / "opencode.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["pawflow"]["type"] == "local"
    assert opencode["plugin"][0].startswith("file:")
    assert "chat.message" in (
        opencode_home / "plugins" / "pawflow.js").read_text(encoding="utf-8")

    jcode_home = Path(session["configs"]["jcode_home"])
    assert json.loads((jcode_home / "mcp.json").read_text(
        encoding="utf-8"))["mcpServers"]["pawflow"]["type"] == "stdio"
    jcode_config = (jcode_home / "config.toml").read_text(encoding="utf-8")
    assert all(event in jcode_config for event in (
        "session_start", "turn_start", "turn_end"))
    jcode_overlay = (jcode_home / "prompt-overlay.md").read_text(
        encoding="utf-8")
    assert "get_initial_context" in jcode_overlay
    assert "get_context_updates" in jcode_overlay

    pi_home = Path(session["configs"]["pi_home"])
    pi_extension = (pi_home / "extensions" / "pawflow.js").read_text(
        encoding="utf-8")
    assert 'pi.registerTool({' in pi_extension
    assert '"before_agent_start"' in pi_extension
    assert '"agent_end"' in pi_extension

    hermes_home = Path(session["configs"]["hermes_home"])
    hermes_config = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    hermes_plugin = (
        hermes_home / "plugins" / "pawflow" / "__init__.py").read_text(
            encoding="utf-8")
    assert "mcp_servers:" in hermes_config and "- pawflow" in hermes_config
    assert 'ctx.register_hook("pre_llm_call"' in hermes_plugin
    assert 'ctx.register_hook("post_llm_call"' in hermes_plugin

    generated = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in result["configs"])
    assert "private-key" not in generated
    assert "gateway-key" not in generated


def test_session_launcher_builds_additional_harness_commands(tmp_path):
    launcher = _load_script(
        "mcp-session-launcher.py", "pawflow_mcp_additional_harness_launcher")
    relay_dir = tmp_path / "project"
    relay_dir.mkdir()
    entries = {
        name: {"command": "python", "args": ["bridge.py"], "cwd": str(relay_dir)}
        for name in ("opencode", "jcode", "pi", "hermes")
    }
    configs = {}
    for name in entries:
        home = tmp_path / f"{name}-home"
        home.mkdir()
        configs[f"{name}_home"] = str(home)
    session = tmp_path / "session.json"
    session.write_text(json.dumps({
        "name": "pawflow", "relay_dir": str(relay_dir),
        "entries": entries, "configs": configs,
    }), encoding="utf-8")

    opencode, env, _ = launcher.build_launch(
        session, "opencode", [], binary="opencode")
    assert opencode == ["opencode", str(relay_dir)]
    assert env["OPENCODE_CONFIG_DIR"] == configs["opencode_home"]
    assert env["OPENCODE_CONFIG"].endswith("opencode.json")

    jcode, env, _ = launcher.build_launch(session, "jcode", [], binary="jcode")
    assert jcode == ["jcode"]
    assert env["JCODE_HOME"] == configs["jcode_home"]

    pi, env, _ = launcher.build_launch(session, "pi", [], binary="pi")
    assert pi[:2] == ["pi", "--extension"]
    assert pi[2].endswith("extensions/pawflow.js")
    assert env["PI_CODING_AGENT_DIR"] == configs["pi_home"]

    hermes, env, _ = launcher.build_launch(
        session, "hermes", [], binary="hermes")
    assert hermes == ["hermes"]
    assert env["HERMES_HOME"] == configs["hermes_home"]


def test_session_launcher_builds_strict_per_instance_commands(tmp_path):
    launcher = _load_script(
        "mcp-session-launcher.py", "pawflow_mcp_session_launcher_test")
    relay_dir = tmp_path / "project"
    relay_dir.mkdir()
    cc_config = tmp_path / "claude.mcp.json"
    cc_config.write_text("{}", encoding="utf-8")
    cc_settings = tmp_path / "claude.settings.json"
    cc_settings.write_text("{}", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    agy_home = tmp_path / "agy-home"
    entry = {
        "command": "/usr/bin/python3",
        "args": ["bridge.py", "--profile", "profile.json"],
        "cwd": str(relay_dir),
    }
    session = tmp_path / "session.json"
    session.write_text(json.dumps({
        "name": "pawflow-work", "relay_dir": str(relay_dir),
        "entries": {"cc": entry, "codex": entry, "agy": entry},
        "configs": {
            "cc": str(cc_config), "cc_settings": str(cc_settings),
            "codex_home": str(codex_home), "agy_home": str(agy_home)},
    }), encoding="utf-8")

    cc, _env, cwd = launcher.build_launch(
        session, "cc", ["--model", "sonnet"], binary="claude")
    assert cc[:4] == [
        "claude", "--mcp-config", str(cc_config), "--strict-mcp-config"]
    assert cc[4:6] == ["--settings", str(cc_settings)]
    assert cc[-2:] == ["--model", "sonnet"]
    assert cwd == str(relay_dir)

    codex, codex_env, _cwd = launcher.build_launch(
        session, "codex", [], binary="codex")
    assert codex[:3] == ["codex", "-C", str(relay_dir)]
    override = codex[codex.index("-c") + 1]
    assert override.startswith("mcp_servers={")
    assert "pawflow-work" in override
    assert codex_env["CODEX_HOME"] == str(codex_home)

    agy, env, _cwd = launcher.build_launch(
        session, "agy", [], binary="agy")
    assert agy == ["agy"]
    assert env["HOME"] == str(agy_home)
    assert env["USERPROFILE"] == str(agy_home)


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
    controller = RelayController(
        Bridge(), tmp_path, "Codex", allow_service_tunnels=True)
    controller.connect()
    try:
        runtime_root = str((ROOT / "pawflow_relay").parent)
        assert captured["env"]["PAWFLOW_RELAY_RUNTIME_ROOT"] == runtime_root
        assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == runtime_root
        assert "relay-secret" not in " ".join(captured["argv"])
        assert str(tmp_path) not in " ".join(captured["argv"])
        assert "--allow-service-tunnels" in captured["argv"]
        assert controller.status()["allow_service_tunnels"] is True
    finally:
        controller.close()


def test_mcp_installer_downloads_checksum_verified_frpc(monkeypatch, tmp_path):
    installer = _load_script(
        "install-mcp-client.py", "pawflow_mcp_frpc_installer_test")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("frp_0.70.1_windows_amd64/frpc.exe", b"verified-frpc")
    payload = archive_buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.platform, "machine", lambda: "AMD64")
    monkeypatch.setitem(
        installer.FRP_ASSETS, ("windows", "amd64"),
        ("windows_amd64", "zip", digest))
    monkeypatch.setattr(
        installer.urllib.request, "urlopen",
        lambda *_args, **_kwargs: Response())

    binary = installer._ensure_frpc(tmp_path / "runtime")

    assert binary == tmp_path / "runtime" / "bin" / "frpc.exe"
    assert binary.read_bytes() == b"verified-frpc"


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
        f"{root}/client.py",
        f"{root}/hook.py",
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
    guide = (ROOT / "docs" / "MCP_CLIENT_INSTALLER.md").read_text(
        encoding="utf-8")

    assert "python scripts/build-mcp-client-installer.py --version" in workflow
    assert "dist/mcp-client-installers/*.zip" in workflow
    assert "dist/mcp-client-installers/*.tar.gz" in workflow
    assert "mcpClientZip" in site and "mcpClientTar" in site
    assert 'id="published-mcp-client"' in howto
    assert 'data-release-download="mcpClientZip"' in howto
    assert "MCP_CLIENT_INSTALLER.md" in howto
    assert "howtos.html#published-mcp-client" in docs_hub
    assert "never reads or writes the user's global" in guide
    assert "SESSION/mcp.json" in guide
    assert "session-bound command printed by the installer" in howto
    assert "preserves unrelated client settings" not in howto
