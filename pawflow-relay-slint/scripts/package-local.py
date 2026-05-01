#!/usr/bin/env python3
"""Build a local portable PawFlow Relay Slint package."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "pawflow-relay-slint"


def platform_suffix() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def binary_name() -> str:
    return f"{APP_NAME}.exe" if platform.system().lower() == "windows" else APP_NAME


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def cargo_command() -> str:
    cargo = shutil.which("cargo")
    if cargo:
        return cargo

    if platform.system().lower() == "windows":
        raise SystemExit(
            "cargo was not found in Windows PATH. Install Rust for Windows with "
            "https://rustup.rs/, then reopen PowerShell. If you want a Linux "
            "artifact instead, run this packaging command inside WSL."
        )

    raise SystemExit("cargo was not found in PATH. Install Rust with https://rustup.rs/.")


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def package(repo_root: Path, release: bool) -> Path:
    project_root = repo_root / "pawflow-relay-slint"
    runtime_dir = project_root / "runtime"
    dist_dir = repo_root / "dist" / f"{APP_NAME}-{platform_suffix()}"
    cargo = cargo_command()

    run([
        sys.executable,
        "scripts/prepare-runtime.py",
        "--repo-root",
        str(repo_root),
        "--out",
        str(runtime_dir),
    ], project_root)

    cargo_cmd = [cargo, "build", "--locked"]
    profile_dir = "debug"
    if release:
        cargo_cmd.insert(2, "--release")
        profile_dir = "release"
    run(cargo_cmd, project_root)

    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    binary = project_root / "target" / profile_dir / binary_name()
    shutil.copy2(binary, dist_dir / binary.name)
    copy_tree(runtime_dir, dist_dir / "runtime")
    shutil.copy2(project_root / "README.md", dist_dir / "README.md")

    print(f"Packaged {dist_dir}")
    return dist_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="PawFlow repository root",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Build a debug package instead of release",
    )
    args = parser.parse_args()
    package(args.repo_root.resolve(), release=not args.debug)


if __name__ == "__main__":
    main()
