#!/usr/bin/env python3
"""Build standalone PawFlow relay CLI installer artifacts."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess  # nosec B404
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "pawflow-relay-desktop" / "scripts" / "relay-bin-entry.py"
DIST_ROOT = ROOT / "dist" / "relay-cli-installers"
BUILD_ROOT = ROOT / "build" / "relay-cli-pyinstaller"

RUNTIME_TOOL_HIDDEN_IMPORTS = [
    "concurrent.futures",
    "difflib",
    "select",
    "selectors",
    "shlex",
    "signal",
    "urllib.error",
    "urllib.request",
    "uuid",
]


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, check=False)  # nosec B603
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def python_command() -> str:
    return os.environ.get("PAWFLOW_RELAY_PYTHON") or os.environ.get("PYTHON") or sys.executable


def project_version() -> str:
    override = os.environ.get("PAWFLOW_RELAY_VERSION") or os.environ.get("PAWFLOW_VERSION")
    if override:
        return override
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Could not read project version from pyproject.toml")
    return match.group(1)


def platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("arm64", "aarch64")
    if system == "darwin":
        system = "macos"
    elif system == "windows":
        system = "win"
    return f"{system}-{machine}"


def executable_name() -> str:
    return "pawflow-relay.exe" if platform.system().lower() == "windows" else "pawflow-relay"


def ensure_pyinstaller(python: str) -> None:
    check = _capture([python, "-m", "PyInstaller", "--version"])
    if check.returncode == 0:
        return
    print("PyInstaller is required to build the PawFlow relay binary.", file=sys.stderr)
    print(f"Install it for this Python: {python} -m pip install pyinstaller", file=sys.stderr)
    raise SystemExit(check.returncode or 1)


def build_binary(python: str, version: str) -> Path:
    ensure_pyinstaller(python)
    tag = platform_tag()
    bin_dir = DIST_ROOT / f"pawflow-relay-cli-{version}-{tag}" / "bin"
    exe_path = bin_dir / executable_name()
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if exe_path.exists():
        exe_path.unlink()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        python,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        "pawflow-relay",
        "--distpath",
        str(bin_dir),
        "--workpath",
        str(BUILD_ROOT),
        "--specpath",
        str(BUILD_ROOT),
        "--paths",
        str(ROOT),
        "--hidden-import",
        "pawflow_relay.manager_cli",
        "--hidden-import",
        "pawflow_relay.thread",
        "--hidden-import",
        "pawflow_relay.worker",
        "--hidden-import",
        "pawflow_cli.auth",
        str(ENTRY),
    ]
    for module in RUNTIME_TOOL_HIDDEN_IMPORTS:
        command[-1:-1] = ["--hidden-import", module]
    _run(command, env=env)
    if not exe_path.exists():
        raise RuntimeError(f"Relay binary was not produced: {exe_path}")
    if platform.system().lower() != "windows":
        exe_path.chmod(0o755)
    return exe_path


def write_install_scripts(layout: Path) -> None:
    (layout / "install.sh").write_text(
        """#!/usr/bin/env sh
set -eu
target="${PREFIX}/bin"
mkdir -p "$target"
cp "$(dirname "$0")/bin/pawflow-relay" "$target/pawflow-relay"
chmod 755 "$target/pawflow-relay"
echo "Installed pawflow-relay to $target/pawflow-relay"
""",
        encoding="utf-8",
    )
    (layout / "install.sh").chmod(0o755)
    (layout / "install.ps1").write_text(
        r"""$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\PawFlow Relay CLI"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Force -Path (Join-Path $PSScriptRoot "bin\pawflow-relay.exe") -Destination (Join-Path $InstallDir "pawflow-relay.exe")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($UserPath -split ';' | Where-Object { $_ -eq $InstallDir })) {
    [Environment]::SetEnvironmentVariable("Path", (($UserPath, $InstallDir) -ne '' -join ';'), "User")
}
Write-Host "Installed pawflow-relay to $InstallDir\pawflow-relay.exe"
Write-Host "Open a new terminal before running pawflow-relay from PATH."
""",
        encoding="utf-8",
    )
    (layout / "README.txt").write_text(
        """PawFlow Relay CLI

Run `pawflow-relay --help` after installing. This binary manages local PawFlow
client relays and shares the same relay state used by Relay Desktop.
""",
        encoding="utf-8",
    )


def create_archives(layout: Path, version: str) -> list[Path]:
    tag = platform_tag()
    archive_base = DIST_ROOT / f"pawflow-relay-cli-{version}-{tag}"
    zip_path = Path(f"{archive_base}.zip")
    tar_path = DIST_ROOT / f"pawflow-relay-cli-{version}-{tag}.tar.gz"
    artifacts: list[Path] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in layout.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(layout.parent))
    artifacts.append(zip_path)
    if platform.system().lower() != "windows":
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(layout, arcname=layout.name)
        artifacts.append(tar_path)
    return artifacts


def package(binary: Path, version: str) -> list[Path]:
    layout = binary.parents[1]
    write_install_scripts(layout)
    return create_archives(layout, version)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PawFlow relay CLI installer artifacts")
    parser.add_argument("--skip-binary", action="store_true", help="Package an existing relay CLI binary")
    parser.add_argument("--version", help="Override the version used in artifact names")
    args = parser.parse_args()

    version = args.version or project_version()
    tag = platform_tag()
    binary = DIST_ROOT / f"pawflow-relay-cli-{version}-{tag}" / "bin" / executable_name()
    if not args.skip_binary:
        binary = build_binary(python_command(), version)
    elif not binary.exists():
        raise SystemExit(f"Missing existing relay CLI binary: {binary}")

    artifacts = package(binary, version)
    print("PawFlow relay CLI artifacts:")
    for artifact in artifacts:
        print(f"  {artifact}")


if __name__ == "__main__":
    main()
