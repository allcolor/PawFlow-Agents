"""Tests for the bundled pawflow.comfyui-operator PFP package."""

from __future__ import annotations

import json
from pathlib import Path

from core import pfp_package


ROOT = Path("packages/pawflow.comfyui-operator.pfpdir")
MANIFEST = ROOT / "pfp.json"
SKILL = ROOT / "content/skills/operate-comfyui/SKILL.md"


def test_comfyui_operator_manifest_ships_only_the_operator_skill():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["format"] == "pawflow.package.v1"
    assert manifest["package"] == "pawflow.comfyui-operator"
    assert manifest["version"] == "1.0.0"
    assert manifest["objects"] == [{
        "id": "skill:operate-comfyui",
        "type": "skill",
        "name": "operate-comfyui",
        "path": "content/skills/operate-comfyui/SKILL.md",
    }]


def test_comfyui_operator_skill_preserves_runtime_and_safety_boundaries():
    skill = SKILL.read_text(encoding="utf-8")

    assert "name: operate-comfyui" in skill
    assert "version: 1.0.0" in skill
    assert "local=true" in skill
    assert "relay_local=true" in skill
    assert "Never expose port 8188 directly to the public internet" in skill
    assert "Check /queue before submit" in skill
    assert "Treat /history/PROMPT_ID as authoritative" in skill
    assert "FileStore" in skill
    assert "scope: user" not in skill
    assert "created_at:" not in skill


def test_comfyui_operator_package_builds_as_a_verified_pfp(tmp_path):
    keypair = pfp_package.create_signing_key()
    output = tmp_path / "pawflow.comfyui-operator-1.0.0.pfp"

    built = pfp_package.build_pfp(
        str(ROOT), output_path=str(output), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"])

    assert plan["verified"] is True
    assert plan["package"] == "pawflow.comfyui-operator"
    assert plan["version"] == "1.0.0"
    assert [obj["id"] for obj in plan["objects"]] == [
        "skill:operate-comfyui"]
