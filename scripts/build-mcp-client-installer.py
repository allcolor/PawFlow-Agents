#!/usr/bin/env python3
"""Build universal PawFlow MCP stdio client installer archives."""

from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = ROOT / "dist" / "mcp-client-installers"
BUILD_ROOT = ROOT / "build" / "mcp-client-installer"
TOOL_FILES = (
    "__init__.py",
    "_fs_edit.py",
    "_fs_grep.py",
    "_fs_paths.py",
    "_fs_read.py",
    "audio_capture.py",
    "fs_actions.py",
    "fs_common.py",
    "fs_exec.py",
    "fs_http.py",
    "fs_mcp.py",
    "fs_screen.py",
    "pawflow_relay_launcher.py",
    "screen_actions.py",
    "screen_actions_cua.py",
)
EXECUTABLES = {"install.sh", "install.py", "launcher.py"}


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Could not read project version from pyproject.toml")
    return match.group(1)


def _copy_runtime(layout: Path) -> None:
    runtime = layout / "runtime"
    package_target = runtime / "pawflow_relay"
    shutil.copytree(
        ROOT / "pawflow_relay",
        package_target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    tools_target = runtime / "tools"
    tools_target.mkdir(parents=True)
    for name in TOOL_FILES:
        source = ROOT / "tools" / name
        if not source.is_file():
            raise RuntimeError(f"Missing relay runtime tool: {source}")
        shutil.copy2(source, tools_target / name)


def create_layout(version: str) -> Path:
    layout = BUILD_ROOT / f"pawflow-mcp-client-{version}"
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    layout.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "install-mcp-client.py", layout / "install.py")
    shutil.copy2(ROOT / "scripts" / "mcp-client-launcher.py", layout / "launcher.py")
    shutil.copy2(ROOT / "scripts" / "install-mcp-client.sh", layout / "install.sh")
    shutil.copy2(ROOT / "scripts" / "install-mcp-client.cmd", layout / "install.cmd")
    shutil.copy2(ROOT / "scripts" / "install-mcp-client.ps1", layout / "install.ps1")
    shutil.copy2(ROOT / "docs" / "MCP_CLIENT_INSTALLER.md", layout / "README.md")
    shutil.copy2(ROOT / "LICENSE", layout / "LICENSE")
    (layout / "VERSION").write_text(version + "\n", encoding="utf-8")
    _copy_runtime(layout)
    for name in EXECUTABLES:
        path = layout / name
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return layout


def _relative(layout: Path, path: Path) -> str:
    return (Path(layout.name) / path.relative_to(layout)).as_posix()


def _zip_mode(path: Path) -> int:
    if path.name in EXECUTABLES:
        return 0o100755
    return 0o100644


def _write_zip(layout: Path, destination: Path) -> None:
    with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=9) as archive:
        for path in sorted(layout.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                _relative(layout, path),
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = _zip_mode(path) << 16
            archive.writestr(info, path.read_bytes())


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if info.isdir():
        info.mode = 0o755
    elif Path(info.name).name in EXECUTABLES:
        info.mode = 0o755
    else:
        info.mode = 0o644
    return info


def _write_tar(layout: Path, destination: Path) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                paths = [layout, *sorted(
                    layout.rglob("*"), key=lambda item: item.as_posix())]
                for path in paths:
                    arcname = (
                        layout.name if path == layout
                        else _relative(layout, path)
                    )
                    archive.add(
                        path,
                        arcname=arcname,
                        recursive=False,
                        filter=_tar_filter,
                    )


def build(version: str, out_dir: Path = DIST_ROOT) -> list[Path]:
    if not version or "/" in version or "\\" in version:
        raise ValueError("Version must be a non-empty filename-safe value")
    layout = create_layout(version)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"pawflow-mcp-client-{version}.zip"
    tar_path = out_dir / f"pawflow-mcp-client-{version}.tar.gz"
    _write_zip(layout, zip_path)
    _write_tar(layout, tar_path)
    return [zip_path, tar_path]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build universal PawFlow MCP client archives")
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--out-dir", type=Path, default=DIST_ROOT)
    args = parser.parse_args()
    artifacts = build(args.version, args.out_dir)
    print("PawFlow MCP client artifacts:")
    for artifact in artifacts:
        print(f"  {artifact}")


if __name__ == "__main__":
    main()
