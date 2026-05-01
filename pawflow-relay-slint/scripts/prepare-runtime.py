#!/usr/bin/env python3
"""Prepare the Python runtime bundled with PawFlow Relay Slint."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


RUNTIME_DIRS = [
    "pawflow_relay",
]

RUNTIME_FILES = [
    "tools/fs_actions.py",
    "scripts/generate-relay-image.py",
    "config/relay_image_catalog.json",
]


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prepare(repo_root: Path, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for rel in RUNTIME_DIRS:
        copy_tree(repo_root / rel, out_dir / rel)

    for rel in RUNTIME_FILES:
        copy_file(repo_root / rel, out_dir / rel)

    (out_dir / "README.txt").write_text(
        "PawFlow Relay Slint runtime. Keep this directory next to the native binary.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="PawFlow repository root",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "runtime",
        help="Runtime output directory",
    )
    args = parser.parse_args()
    prepare(args.repo_root.resolve(), args.out.resolve())
    print(f"Prepared PawFlow Relay Slint runtime at {args.out.resolve()}")


if __name__ == "__main__":
    main()
