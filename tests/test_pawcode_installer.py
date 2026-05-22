"""Tests for PawCode standalone installer build metadata."""

from pathlib import Path
import py_compile


ROOT = Path(__file__).resolve().parents[1]


def test_pawcode_installer_scripts_are_declared():
    entry = ROOT / "scripts" / "pawcode-bin-entry.py"
    builder = ROOT / "scripts" / "build-pawcode-installer.py"

    assert entry.is_file()
    assert builder.is_file()
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


def test_pawcode_installer_scripts_compile():
    py_compile.compile(str(ROOT / "scripts" / "pawcode-bin-entry.py"), doraise=True)
    py_compile.compile(str(ROOT / "scripts" / "build-pawcode-installer.py"), doraise=True)


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

