"""Verify distribution contents using the repository's setuptools configuration."""

from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stale_manifest", [False, True])
def test_distribution_keeps_assets_and_excludes_generated_caches(tmp_path, stale_manifest):
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_config = "[tool.setuptools.packages.find]" + config.split(
        "[tool.setuptools.packages.find]", 1
    )[1]
    (tmp_path / "MANIFEST.in").write_text(
        (ROOT / "MANIFEST.in").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "pawflow-asset-fixture"\nversion = "0.0.1"\n'
        + package_config,
        encoding="utf-8",
    )
    assets = {
        "tasks/__init__.py": b"",
        "tasks/io/__init__.py": b"",
        "tasks/io/chat_ui/startup_optional.js": b"// startup fixture\n",
        "tasks/io/chat_ui/i18n/fr.json": b'{"hello":"bonjour"}',
        "tasks/io/chat_ui/templates/chat.html": b"<html></html>",
        "tasks/io/chat_ui/vendor/rxjs-LICENSE.txt": b"RxJS license fixture",
        "tasks/io/chat_ui/vendor/highlight-LICENSE.txt": b"highlight license fixture",
    }
    caches = [
        "tasks/io/chat_ui/graphify-out/cache/root.json",
        "tasks/io/chat_ui/vendor/graphify-out/cache/vendor.json",
        "services/graphify-out/cache/service.json",
    ]
    for name, content in {**assets, **dict.fromkeys(caches, b'{"generated":true}')}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if stale_manifest:
        manifest = tmp_path / "pawflow_asset_fixture.egg-info" / "SOURCES.txt"
        manifest.parent.mkdir()
        manifest.write_text("\n".join(caches) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel, = (tmp_path / "dist").glob("*.whl")
    sdist, = (tmp_path / "dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        assert not any("graphify-out" in Path(name).parts for name in archive.namelist())
        for name, content in assets.items():
            assert archive.read(name) == content
    with tarfile.open(sdist) as archive:
        members = {member.name.partition("/")[2]: member for member in archive.getmembers()}
        assert not any("graphify-out" in Path(name).parts for name in members)
        for name, content in assets.items():
            assert archive.extractfile(members[name]).read() == content
