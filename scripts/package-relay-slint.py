#!/usr/bin/env python3
"""Build a local portable PawFlow Relay Slint package from the repo root."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SLINT_PACKAGER = REPO_ROOT / "pawflow-relay-slint" / "scripts" / "package-local.py"


def main() -> None:
    if not SLINT_PACKAGER.exists():
        raise SystemExit(f"Slint packager not found: {SLINT_PACKAGER}")

    args = sys.argv[1:]
    has_repo_root = "--repo-root" in args or any(arg.startswith("--repo-root=") for arg in args)
    if not has_repo_root:
        args = ["--repo-root", str(REPO_ROOT), *args]

    sys.argv = [str(SLINT_PACKAGER), *args]
    runpy.run_path(str(SLINT_PACKAGER), run_name="__main__")


if __name__ == "__main__":
    main()
