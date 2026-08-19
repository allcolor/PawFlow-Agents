#!/usr/bin/env python3
"""Build or verify PawFlow's official bundled PFP artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import pfp_package  # noqa: E402


BUNDLED_DIR = ROOT / "data" / "repository" / "packages" / "bundled"
INDEX_PATH = BUNDLED_DIR / "index.json"
PACKAGE_SPECS = (
    {
        "source": ROOT / "packages" / "pawflow.avatar-runtime.pfpdir",
        "category": "Media & AI",
        "tags": ["avatar", "realtime", "voice", "3d"],
    },
    {
        "source": ROOT / "packages" / "pawflow.avatar-helper.pfpdir",
        "category": "Media & AI",
        "tags": ["avatar", "helper", "ui", "agent"],
    },
    {
        "source": ROOT / "packages" / "pawflow.avatar-pack.starter.pfpdir",
        "category": "Media & AI",
        "tags": ["avatar", "starter", "3d", "model"],
    },
    {
        "source": ROOT / "packages" / "pawflow.comfyui-operator.pfpdir",
        "category": "Media & AI",
        "tags": ["comfyui", "self-hosted", "skill", "image", "video", "audio"],
    },
    {
        "source": ROOT / "packages" / "pawflow.pixazo-provider.pfpdir",
        "category": "Media providers",
        "tags": ["pixazo", "image", "video", "audio", "3d"],
    },
    {
        "source": ROOT / "packages" / "pawflow.wavespeed-provider.pfpdir",
        "category": "Media providers",
        "tags": ["wavespeed", "image", "video", "audio", "3d"],
    },
    {
        "source": ROOT / "packages" / "pawflow.kling-provider.pfpdir",
        "category": "Media providers",
        "tags": ["kling", "video"],
    },
)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_index() -> dict[str, Any]:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _generated_package_ids() -> set[str]:
    return {
        json.loads((spec["source"] / "pfp.json").read_text(encoding="utf-8"))["package"]
        for spec in PACKAGE_SPECS
    }


def build_catalog(output_dir: Path, *, private_key_env: str) -> dict[str, Any]:
    """Build official artifacts and return the complete bundled index."""
    if not os.environ.get(private_key_env):
        raise RuntimeError(f"Environment variable '{private_key_env}' is not set")
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_rows = []
    for spec in PACKAGE_SPECS:
        manifest = json.loads(
            (spec["source"] / "pfp.json").read_text(encoding="utf-8"))
        artifact_name = f"{manifest['package']}-{manifest['version']}.pfp"
        artifact_path = output_dir / artifact_name
        built = pfp_package.build_pfp(
            str(spec["source"]), output_path=str(artifact_path),
            private_key_env=private_key_env)
        plan = pfp_package.inspect_pfp(str(artifact_path))
        generated_rows.append({
            "package": plan["package"],
            "version": plan["version"],
            "description": plan["description"],
            "category": spec["category"],
            "artifact": artifact_name,
            "sha256": "sha256:" + hashlib.sha256(
                artifact_path.read_bytes()).hexdigest(),
            "package_size": built["package_size"],
            "developer_key": plan["developer"]["public_key"],
            "tags": spec["tags"],
            "objects": [obj["id"] for obj in plan["objects"]],
        })

    generated_ids = _generated_package_ids()
    retained = [
        row for row in _load_index().get("packages", [])
        if row.get("package") not in generated_ids
    ]
    return {
        "format": "pawflow.bundled-packages.v1",
        "packages": retained + generated_rows,
    }


def verify_catalog(directory: Path) -> list[str]:
    """Verify indexed files, hashes, sizes, signatures, and object metadata."""
    errors = []
    index = _load_index()
    generated_ids = _generated_package_ids()
    rows = {
        row.get("package"): row for row in index.get("packages", [])
        if row.get("package") in generated_ids
    }
    for package_id in sorted(generated_ids):
        row = rows.get(package_id)
        if not row:
            errors.append(f"missing catalog row: {package_id}")
            continue
        artifact = directory / str(row.get("artifact") or "")
        if not artifact.is_file():
            errors.append(f"missing artifact: {artifact.name}")
            continue
        payload = artifact.read_bytes()
        if row.get("package_size") != len(payload):
            errors.append(f"size mismatch: {artifact.name}")
        if row.get("sha256") != "sha256:" + hashlib.sha256(payload).hexdigest():
            errors.append(f"sha256 mismatch: {artifact.name}")
        try:
            plan = pfp_package.inspect_pfp(str(artifact))
        except Exception as exc:
            errors.append(f"signature verification failed for {artifact.name}: {exc}")
            continue
        if plan["package"] != package_id:
            errors.append(f"package mismatch: {artifact.name}")
        if row.get("developer_key") != plan["developer"].get("public_key"):
            errors.append(f"developer key mismatch: {artifact.name}")
        if row.get("objects") != [obj["id"] for obj in plan["objects"]]:
            errors.append(f"object list mismatch: {artifact.name}")
    return errors


def check_reproducible(private_key_env: str) -> list[str]:
    """Rebuild official packages and compare them byte-for-byte with Git."""
    errors = verify_catalog(BUNDLED_DIR)
    with tempfile.TemporaryDirectory(prefix="pawflow-pfp-check-") as tmp:
        rebuilt_dir = Path(tmp)
        rebuilt_index = build_catalog(
            rebuilt_dir, private_key_env=private_key_env)
        for spec in PACKAGE_SPECS:
            manifest = json.loads(
                (spec["source"] / "pfp.json").read_text(encoding="utf-8"))
            name = f"{manifest['package']}-{manifest['version']}.pfp"
            committed = BUNDLED_DIR / name
            rebuilt = rebuilt_dir / name
            if not committed.is_file() or committed.read_bytes() != rebuilt.read_bytes():
                errors.append(f"non-reproducible artifact: {name}")
        if INDEX_PATH.read_bytes() != _json_bytes(rebuilt_index):
            errors.append("bundled index is not reproducible")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--key-env", default="PAWFLOW_PFP_SIGNING_KEY")
    args = parser.parse_args()

    if args.build:
        index = build_catalog(BUNDLED_DIR, private_key_env=args.key_env)
        INDEX_PATH.write_bytes(_json_bytes(index))
        errors = verify_catalog(BUNDLED_DIR)
    elif args.check:
        errors = check_reproducible(args.key_env)
    else:
        errors = verify_catalog(BUNDLED_DIR)

    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Bundled PFP catalog verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
