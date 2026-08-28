import hashlib
import importlib.util
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build-pawflow-universal-installer.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("pawflow_universal_installer_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tag", "suffix"),
    [
        ("linux-x86_64", ".tar.gz"),
        ("win-amd64", ".zip"),
    ],
)
def test_portable_archive_contains_runtime_docs_schema_and_checksum(tmp_path, tag, suffix):
    builder = load_builder()
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "LICENSE").write_text("license\n", encoding="utf-8")
    (project / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (project / "docs" / "UNIVERSAL_INSTALLER.md").write_text("guide\n", encoding="utf-8")
    executable = tmp_path / ("pawflow-install.exe" if tag.startswith("win-") else "pawflow-install")
    executable.write_bytes(b"binary")
    executable.chmod(0o755)

    archive, checksum = builder.package_binary(
        executable,
        "1.2.3",
        tag,
        root=project,
        dist_root=tmp_path / "dist",
        build_root=tmp_path / "build",
    )

    assert archive.name.endswith(suffix)
    assert checksum.read_text(encoding="ascii") == (
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    if suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())
    else:
        with tarfile.open(archive, "r:gz") as handle:
            names = set(handle.getnames())
    prefix = f"pawflow-install-1.2.3-{tag}/"
    assert {
        prefix + executable.name,
        prefix + "LICENSE",
        prefix + "THIRD_PARTY_NOTICES.md",
        prefix + "UNIVERSAL_INSTALLER.md",
        prefix + "VERSION",
        prefix + "install-request.schema.json",
    } <= names


def test_pyinstaller_excludes_optional_developer_integrations(monkeypatch, tmp_path):
    builder = load_builder()
    calls = []

    monkeypatch.setattr(builder, "BUILD_ROOT", tmp_path / "build")
    monkeypatch.setitem(sys.modules, "PyInstaller", object())

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    assert builder.main(["--version", "1.2.3"]) == 1
    exclusions = {
        calls[0][index + 1]
        for index, argument in enumerate(calls[0])
        if argument == "--exclude-module"
    }
    assert exclusions == {"IPython", "pydantic.v1._hypothesis_plugin"}


def test_release_workflow_builds_universal_installer_on_three_platforms():
    workflow = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8"
    )

    assert "universal-installer:" in workflow
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in workflow
    assert "scripts/build-pawflow-universal-installer.py --version" in workflow
    assert "dist/pawflow-installers/pawflow-install-*.sha256" in workflow
    assert "needs: [bundled-pfps, install-zip, universal-installer," in workflow
