"""Tests for PawCode standalone installer build metadata."""

from pathlib import Path
import importlib.util
import py_compile


ROOT = Path(__file__).resolve().parents[1]


def _load_pawcode_builder():
    path = ROOT / "scripts" / "build-pawcode-installer.py"
    spec = importlib.util.spec_from_file_location("pawcode_installer_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pawcode_installer_scripts_are_declared():
    entry = ROOT / "scripts" / "pawcode-bin-entry.py"
    builder = ROOT / "scripts" / "build-pawcode-installer.py"
    relay_builder = ROOT / "scripts" / "build-relay-cli-installer.py"

    assert entry.is_file()
    assert builder.is_file()
    assert relay_builder.is_file()
    text = builder.read_text(encoding="utf-8")
    assert "PyInstaller" in text
    assert "pawcode-bin-entry.py" in text
    assert "HIDDEN_IMPORTS" in text
    assert "pawflow_cli.stream_json" in text
    assert "pawflow_cli.commands.files" in text
    assert "install.ps1" in text
    assert "install.sh" in text
    assert "${PREFIX}" in text
    assert 'Path(f"{archive_base}.zip")' in text
    assert "dpkg-deb" in text
    assert "pkgbuild" in text
    assert "makensis" in text
    assert "dist" in text and "pawcode-installers" in text
    assert "pawflow-relay" in text
    assert "--version" in text

    relay_text = relay_builder.read_text(encoding="utf-8")
    assert "PyInstaller" in relay_text
    assert "relay-bin-entry.py" in relay_text
    assert "dist" in relay_text and "relay-cli-installers" in relay_text
    assert "pawflow-relay-cli" in relay_text
    assert "install.ps1" in relay_text
    assert "install.sh" in relay_text
    assert "--version" in relay_text
    assert "RUNTIME_TOOL_HIDDEN_IMPORTS" in relay_text
    assert "difflib" in relay_text
    assert "urllib.request" in relay_text


def test_pawcode_installer_scripts_compile():
    py_compile.compile(str(ROOT / "scripts" / "pawcode-bin-entry.py"), doraise=True)
    py_compile.compile(str(ROOT / "scripts" / "build-pawcode-installer.py"), doraise=True)
    py_compile.compile(str(ROOT / "scripts" / "build-relay-cli-installer.py"), doraise=True)


def test_pawcode_binary_copies_distribution_metadata(monkeypatch, tmp_path):
    builder = _load_pawcode_builder()
    builder.DIST_ROOT = tmp_path / "dist"
    builder.BUILD_ROOT = tmp_path / "build"
    builder.ENTRY = tmp_path / "pawcode-bin-entry.py"
    builder.ENTRY.write_text("pass\n", encoding="utf-8")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        binary = (
            builder.DIST_ROOT
            / f"pawcode-1.2.3-{builder.platform_tag()}"
            / "bin"
            / builder.executable_name()
        )
        binary.write_bytes(b"binary")

    monkeypatch.setattr(builder, "ensure_pyinstaller", lambda _python: None)
    monkeypatch.setattr(builder, "_module_available", lambda _python, _module: False)
    monkeypatch.setattr(builder, "_run", fake_run)

    builder.build_binary("python", "1.2.3")

    command = commands[0]
    metadata_flag = command.index("--copy-metadata")
    assert command[metadata_flag + 1] == "pawflow"
    exclusions = {
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--exclude-module"
    }
    assert exclusions == {"IPython", "pydantic.v1._hypothesis_plugin"}


def test_release_assets_workflow_publishes_all_installers():
    workflow = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(encoding="utf-8")

    assert "softprops/action-gh-release@v2" in workflow
    assert "scripts/build-pawflow-install-zip.sh" in workflow
    assert "scripts/build-pawcode-installer.py --version" in workflow
    assert "scripts/build-relay-cli-installer.py --version" in workflow
    assert "npm run ${{ matrix.npm_script }}" in workflow
    assert "vscode-extension" in workflow
    assert "node node_modules/@vscode/vsce/vsce package" in workflow
    assert "dist/vscode-installers/*.vsix" in workflow
    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "dist/pawflow-installers/*" in workflow
    assert "dist/pawcode-installers/*.zip" in workflow
    assert "dist/relay-cli-installers/*.zip" in workflow
    assert "dist/relay-desktop-installers/*.AppImage" in workflow
    assert "dist/relay-desktop-installers/*.tar.gz" in workflow
    assert "dist/relay-desktop-installers/*.exe" in workflow
    assert "dist/relay-desktop-installers/*.blockmap" not in workflow


def test_pawcode_docs_cover_standalone_installer():
    readme = (ROOT / "pawflow_cli" / "README.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "pawcode.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'pawcode = "pawflow_cli.app:main"' in pyproject
    assert "scripts/build-pawcode-installer.py" in readme
    assert "dist/pawcode-installers/" in readme
    assert "Standalone Installer Builds" in docs
    assert "python scripts/build-pawcode-installer.py" in docs
    assert "does not bundle or manage `pawflow-relay`" in docs
