#!/usr/bin/env python3
"""Build one platform-specific PawFlow universal installer executable."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess  # nosec B404
import sys
import tarfile
import zipfile
from pathlib import Path

from pawflow_installer.models import InstallRequest

ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = ROOT / "dist" / "pawflow-installers"
BUILD_ROOT = ROOT / "build" / "pawflow-universal-installer"


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Could not read project version from pyproject.toml")
    return match.group(1)


def platform_tag() -> str:
    system = {"darwin": "macos", "windows": "win"}.get(
        platform.system().lower(), platform.system().lower()
    )
    machine = platform.machine().lower()
    return f"{system}-{machine}"


def package_binary(
    executable: Path,
    version: str,
    tag: str,
    *,
    root: Path = ROOT,
    dist_root: Path = DIST_ROOT,
    build_root: Path = BUILD_ROOT,
) -> tuple[Path, Path]:
    """Create a documented portable archive and its external SHA-256 manifest."""
    bundle_name = f"pawflow-install-{version}-{tag}"
    bundle_dir = build_root / "bundle" / bundle_name
    shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True)
    shutil.copy2(executable, bundle_dir / executable.name)
    shutil.copy2(root / "LICENSE", bundle_dir / "LICENSE")
    shutil.copy2(root / "THIRD_PARTY_NOTICES.md", bundle_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(
        root / "docs" / "UNIVERSAL_INSTALLER.md",
        bundle_dir / "UNIVERSAL_INSTALLER.md",
    )
    (bundle_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (bundle_dir / "install-request.schema.json").write_text(
        json.dumps(InstallRequest.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    dist_root.mkdir(parents=True, exist_ok=True)
    if tag.startswith("win-"):
        archive = dist_root / f"{bundle_name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_file():
                    handle.write(path, path.relative_to(bundle_dir.parent))
    else:
        archive = dist_root / f"{bundle_name}.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(bundle_dir, arcname=bundle_name)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name(f"{archive.name}.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the PawFlow universal CLI/GUI installer"
    )
    parser.add_argument("--version")
    args = parser.parse_args(argv)
    version = args.version or project_version()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is required for this build.", file=sys.stderr)
        return 1
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    binary_dist = BUILD_ROOT / "pyinstaller-dist"
    binary_dist.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        "pawflow-install",
        "--distpath",
        str(binary_dist),
        "--workpath",
        str(BUILD_ROOT),
        "--specpath",
        str(BUILD_ROOT),
        "--paths",
        str(ROOT),
        "--hidden-import",
        "pawflow_installer.frontends.gui",
        "--collect-submodules",
        "keyring.backends",
        # Pydantic's PyInstaller hook collects this test-only plugin, which then
        # pulls Hypothesis extras and unrelated ML stacks such as Torch/CUDA.
        "--exclude-module",
        "pydantic.v1._hypothesis_plugin",
        # Rich conditionally integrates with IPython, whose hook recursively
        # collects scientific and ML packages that the installer never uses.
        "--exclude-module",
        "IPython",
        "--add-data",
        f"{ROOT / 'scripts' / 'install-pawflow.sh'}{';' if platform.system() == 'Windows' else ':'}scripts",
        "--add-data",
        f"{ROOT / 'scripts' / 'install-pawflow.ps1'}{';' if platform.system() == 'Windows' else ':'}scripts",
        "--add-data",
        f"{ROOT / 'scripts' / 'doctor-pawflow.sh'}{';' if platform.system() == 'Windows' else ':'}scripts",
        "--add-data",
        f"{ROOT / 'scripts' / 'doctor-pawflow.ps1'}{';' if platform.system() == 'Windows' else ':'}scripts",
        str(ROOT / "pawflow_installer" / "__main__.py"),
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)  # nosec B603
    if result.returncode:
        return result.returncode

    executable = binary_dist / (
        "pawflow-install.exe" if platform.system() == "Windows" else "pawflow-install"
    )
    if not executable.is_file():
        print(f"PyInstaller did not create {executable}", file=sys.stderr)
        return 1
    smoke = subprocess.run(  # nosec B603
        [str(executable), "--help"],
        cwd=ROOT,
        check=False,
    )
    if smoke.returncode:
        print("Packaged pawflow-install --help smoke failed.", file=sys.stderr)
        return smoke.returncode
    archive, checksum = package_binary(executable, version, platform_tag())
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
