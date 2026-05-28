#!/usr/bin/env python3
"""Build a standalone PawCode binary and current-platform installer artifacts."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess  # nosec B404
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "pawcode-bin-entry.py"
DIST_ROOT = ROOT / "dist" / "pawcode-installers"
BUILD_ROOT = ROOT / "build" / "pawcode-pyinstaller"

HIDDEN_IMPORTS = [
    "core",
    "pawflow_cli.api",
    "pawflow_cli.app",
    "pawflow_cli.auth",
    "pawflow_cli.config",
    "pawflow_cli.secure_store",
    "pawflow_cli.stream_events",
    "pawflow_cli.stream_json",
    "pawflow_cli.ui.renderer",
    "pawflow_cli.commands.conversation",
    "pawflow_cli.commands.files",
    "pawflow_cli.commands.session",
]

COLLECT_SUBMODULES = ["cryptography", "prompt_toolkit", "rich", "PIL"]


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
    return os.environ.get("PAWCODE_PYTHON") or os.environ.get("PYTHON") or sys.executable


def project_version() -> str:
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
    return "pawcode.exe" if platform.system().lower() == "windows" else "pawcode"


def _module_available(python: str, module: str) -> bool:
    script = (
        "import importlib.util, sys; "
        f"sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    )
    return _capture([python, "-c", script]).returncode == 0


def ensure_pyinstaller(python: str) -> None:
    check = _capture([python, "-m", "PyInstaller", "--version"])
    if check.returncode == 0:
        return
    print("PyInstaller is required to build the PawCode binary.", file=sys.stderr)
    print(f"Install it for this Python: {python} -m pip install pyinstaller", file=sys.stderr)
    raise SystemExit(check.returncode or 1)


def build_binary(python: str, version: str) -> Path:
    ensure_pyinstaller(python)
    tag = platform_tag()
    bin_dir = DIST_ROOT / f"pawcode-{version}-{tag}" / "bin"
    work_dir = BUILD_ROOT / tag
    exe_path = bin_dir / executable_name()

    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.rmtree(bin_dir, ignore_errors=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        python,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        "pawcode",
        "--distpath",
        str(bin_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--paths",
        str(ROOT),
    ]
    for hidden in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", hidden])
    for module in COLLECT_SUBMODULES:
        if _module_available(python, module):
            command.extend(["--collect-submodules", module])
    command.append(str(ENTRY))

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(ROOT)
    )
    _run(command, env=env)

    if not exe_path.exists():
        raise RuntimeError(f"PawCode binary was not produced: {exe_path}")
    if os.name != "nt":
        exe_path.chmod(exe_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe_path


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_install_scripts(layout: Path, version: str) -> None:
    readme = f"""PawCode {version}

PawCode is the terminal client for PawFlow. It is chat-only and does not start,
stop, or package a filesystem relay. Use pawflow-relay or the webchat resource
panel for relay lifecycle.

Install on Linux/macOS:
  ./install.sh

Install on Windows PowerShell:
  powershell -ExecutionPolicy Bypass -File .\\install.ps1

After installation:
  pawcode --version
  pawcode auth login --server http://localhost:19990
  pawcode --server http://localhost:19990 --dir .
"""
    (layout / "README.txt").write_text(readme, encoding="utf-8")
    write_executable(
        layout / "install.sh",
        """#!/usr/bin/env sh
set -eu
prefix="${PREFIX}"
target="$prefix/bin"
mkdir -p "$target"
cp "$(dirname "$0")/bin/pawcode" "$target/pawcode"
chmod 755 "$target/pawcode"
echo "Installed pawcode to $target/pawcode"
""",
    )
    (layout / "install.ps1").write_text(
        """$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\\PawCode"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Force -Path (Join-Path $PSScriptRoot "bin\\pawcode.exe") -Destination (Join-Path $InstallDir "pawcode.exe")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($UserPath) { $Parts = $UserPath -split ';' | Where-Object { $_ } }
if ($Parts -notcontains $InstallDir) {
    $NewPath = (($Parts + $InstallDir) -join ';')
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
}
Write-Host "Installed pawcode to $InstallDir\\pawcode.exe"
Write-Host "Open a new terminal before running pawcode from PATH."
""",
        encoding="utf-8",
    )


def create_archives(layout: Path, version: str) -> list[Path]:
    tag = platform_tag()
    archive_base = DIST_ROOT / f"pawcode-{version}-{tag}"
    zip_path = Path(f"{archive_base}.zip")
    tar_path = DIST_ROOT / f"pawcode-{version}-{tag}.tar.gz"
    artifacts: list[Path] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in layout.rglob("*"):
            if item.is_file():
                zf.write(item, item.relative_to(layout.parent))
    artifacts.append(zip_path)

    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(layout, arcname=layout.name)
    artifacts.append(tar_path)
    return artifacts


def deb_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine


def build_deb(binary: Path, version: str) -> Path | None:
    if platform.system().lower() != "linux" or not shutil.which("dpkg-deb"):
        return None
    tag = platform_tag()
    root = BUILD_ROOT / f"deb-{tag}"
    shutil.rmtree(root, ignore_errors=True)
    (root / "DEBIAN").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    shutil.copy2(binary, root / "usr" / "bin" / "pawcode")
    (root / "usr" / "bin" / "pawcode").chmod(0o755)
    control = f"""Package: pawcode
Version: {version}
Section: devel
Priority: optional
Architecture: {deb_architecture()}
Maintainer: PawFlow Contributors
Description: Terminal client for PawFlow conversations
 PawCode is the terminal chat frontend for PawFlow. Relay lifecycle is managed
 by pawflow-relay or PawFlow webchat resources.
"""
    (root / "DEBIAN" / "control").write_text(control, encoding="utf-8")
    out = DIST_ROOT / f"pawcode_{version}_{deb_architecture()}.deb"
    _run(["dpkg-deb", "--build", str(root), str(out)])
    return out


def build_pkg(binary: Path, version: str) -> Path | None:
    if platform.system().lower() != "darwin" or not shutil.which("pkgbuild"):
        return None
    tag = platform_tag()
    root = BUILD_ROOT / f"pkg-{tag}"
    shutil.rmtree(root, ignore_errors=True)
    (root / "usr" / "local" / "bin").mkdir(parents=True)
    shutil.copy2(binary, root / "usr" / "local" / "bin" / "pawcode")
    (root / "usr" / "local" / "bin" / "pawcode").chmod(0o755)
    out = DIST_ROOT / f"pawcode-{version}-{tag}.pkg"
    _run([
        "pkgbuild",
        "--root",
        str(root),
        "--identifier",
        "org.allcolor.pawcode",
        "--version",
        version,
        "--install-location",
        "/",
        str(out),
    ])
    return out


def write_nsis_script(layout: Path, version: str) -> Path:
    out = DIST_ROOT / f"pawcode-{version}-{platform_tag()}-setup.exe"
    nsi = BUILD_ROOT / "pawcode-installer.nsi"
    nsi.parent.mkdir(parents=True, exist_ok=True)
    exe = (layout / "bin" / "pawcode.exe").as_posix()
    nsi.write_text(
        f'''OutFile "{out.as_posix()}"
InstallDir "$LOCALAPPDATA\\Programs\\PawCode"
RequestExecutionLevel user

Section "Install"
  SetOutPath "$INSTDIR"
  File /oname=pawcode.exe "{exe}"
  ReadRegStr $0 HKCU "Environment" "Path"
  StrCmp $0 "" 0 +2
    WriteRegExpandStr HKCU "Environment" "Path" "$INSTDIR"
  StrCmp $0 "" +2 0
    WriteRegExpandStr HKCU "Environment" "Path" "$0;$INSTDIR"
  SendMessage 0xffff 0x001A 0 "STR:Environment" /TIMEOUT=5000
  WriteUninstaller "$INSTDIR\\uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\\pawcode.exe"
  Delete "$INSTDIR\\uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
''',
        encoding="utf-8",
    )
    return nsi


def build_nsis(layout: Path, version: str) -> Path | None:
    if platform.system().lower() != "windows" or not shutil.which("makensis"):
        return None
    nsi = write_nsis_script(layout, version)
    _run(["makensis", str(nsi)])
    return DIST_ROOT / f"pawcode-{version}-{platform_tag()}-setup.exe"


def package(binary: Path, version: str, native: bool) -> list[Path]:
    layout = binary.parents[1]
    write_install_scripts(layout, version)
    artifacts = create_archives(layout, version)
    if native:
        for maybe in (build_deb(binary, version), build_pkg(binary, version), build_nsis(layout, version)):
            if maybe is not None:
                artifacts.append(maybe)
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PawCode binary and installer artifacts")
    parser.add_argument("--skip-binary", action="store_true",
                        help="Package an existing dist/pawcode-installers binary")
    parser.add_argument("--no-native", action="store_true",
                        help="Skip native .deb/.pkg/NSIS packaging")
    args = parser.parse_args()

    version = project_version()
    tag = platform_tag()
    binary = DIST_ROOT / f"pawcode-{version}-{tag}" / "bin" / executable_name()
    if not args.skip_binary:
        binary = build_binary(python_command(), version)
    elif not binary.exists():
        raise SystemExit(f"Missing existing PawCode binary: {binary}")

    artifacts = package(binary, version, native=not args.no_native)
    print("PawCode artifacts:")
    for artifact in artifacts:
        print(f"  {artifact}")


if __name__ == "__main__":
    main()

