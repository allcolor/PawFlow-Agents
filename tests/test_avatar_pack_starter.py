"""Tests for the independent PawFlow starter avatar pack."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import struct
from pathlib import Path

from core import pfp_package
from core.extension_repository import ExtensionRepository


ROOT = Path("packages/pawflow.avatar-pack.starter.pfpdir")
RUNTIME_ROOT = Path("packages/pawflow.avatar-runtime.pfpdir")
MODEL = ROOT / "content/models/pawflow-bot.glb"
EXPECTED_MODEL_SHA256 = (
    "2b7cc0dfb2bdb8cef9adaf63bde7550832dfda289306a70a191d7f1e4aa7c9b3")
REQUIRED_NODES = {
    "Armature", "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
    "LeftEye", "RightEye", "LeftShoulder", "LeftArm", "LeftForeArm",
    "LeftHand", "RightShoulder", "RightArm", "RightForeArm", "RightHand",
    "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase", "RightUpLeg",
    "RightLeg", "RightFoot", "RightToeBase",
}
REQUIRED_VISEMES = {
    "viseme_sil", "viseme_PP", "viseme_FF", "viseme_TH", "viseme_DD",
    "viseme_kk", "viseme_CH", "viseme_SS", "viseme_nn", "viseme_RR",
    "viseme_aa", "viseme_E", "viseme_I", "viseme_O", "viseme_U",
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _glb_json(path: Path):
    payload = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", payload)
    json_size, chunk_type = struct.unpack_from("<I4s", payload, 12)
    assert magic == b"glTF"
    assert version == 2
    assert total == len(payload)
    assert chunk_type == b"JSON"
    return json.loads(payload[20:20 + json_size])


def _build(source: Path, output: Path, private_key: str) -> str:
    return pfp_package.build_pfp(
        str(source), output_path=str(output),
        private_key=private_key)["path"]


def test_starter_model_has_required_renderer_contract_and_rebuilds(tmp_path):
    document = _glb_json(MODEL)
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest() == (
        EXPECTED_MODEL_SHA256)
    assert REQUIRED_NODES <= {node.get("name") for node in document["nodes"]}
    target_names = set(document["meshes"][3]["extras"]["targetNames"])
    assert REQUIRED_VISEMES <= target_names
    assert {
        "eyeBlinkLeft", "eyeBlinkRight", "jawOpen",
        "mouthSmileLeft", "mouthSmileRight",
    } <= target_names

    spec = importlib.util.spec_from_file_location(
        "pawflow_starter_avatar_builder",
        Path("scripts/build-starter-avatar.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    rebuilt = tmp_path / "pawflow-bot.glb"
    module.build(rebuilt)
    assert rebuilt.read_bytes() == MODEL.read_bytes()


def test_starter_pack_dependency_update_uninstall_and_reimport(tmp_path):
    keypair = pfp_package.create_signing_key()
    runtime_artifact = _build(
        RUNTIME_ROOT, tmp_path / "avatar-runtime.pfp",
        keypair["private_key"])
    pack_artifact = _build(
        ROOT, tmp_path / "avatar-pack.pfp", keypair["private_key"])

    blocked = pfp_package.inspect_pfp(pack_artifact, user_id="alice")
    resource = next(
        row for row in blocked["objects"]
        if row["id"] == "repository_resource:pawflow-bot")
    assert resource["status"] == "missing_dependency"
    assert resource["missing_dependencies"] == [{
        "package": "pawflow.avatar-runtime",
        "version": ">=0.1.0,<1.0.0",
    }]
    assert pfp_package.install_pfp(
        pack_artifact, user_id="alice", force=True)["installed"] == []

    assert pfp_package.install_pfp(
        runtime_artifact, user_id="alice", force=True)["ok"] is True
    assert pfp_package.install_pfp(
        pack_artifact, user_id="alice", force=True)["ok"] is True
    row = ExtensionRepository.instance().get(
        "pawflow.avatar", "pawflow-bot", user_id="alice", scope="user")
    assert row["document"]["renderer"] == "talkinghead"
    assert row["assets"][0]["id"] == "model"
    assert row["assets"][0]["sha256"] == "sha256:" + EXPECTED_MODEL_SHA256

    protected = pfp_package.uninstall_pfp(
        "pawflow.avatar-runtime", user_id="alice", scope="user")
    assert protected["ok"] is False
    assert "pawflow.avatar-pack.starter" in str(protected)

    update_root = tmp_path / "updated-pack.pfpdir"
    shutil.copytree(ROOT, update_root)
    manifest = _json(update_root / "pfp.json")
    manifest["version"] = "0.1.1"
    (update_root / "pfp.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    avatar = _json(update_root / "content/avatars/pawflow-bot.json")
    avatar["description"] = "Updated starter avatar"
    (update_root / "content/avatars/pawflow-bot.json").write_text(
        json.dumps(avatar, indent=2) + "\n", encoding="utf-8")
    update_artifact = _build(
        update_root, tmp_path / "avatar-pack-0.1.1.pfp",
        keypair["private_key"])
    assert pfp_package.install_pfp(
        update_artifact, user_id="alice", force=True,
        replace=True)["ok"] is True
    updated = ExtensionRepository.instance().get(
        "pawflow.avatar", "pawflow-bot", user_id="alice", scope="user")
    assert updated["document"]["description"] == "Updated starter avatar"

    assert pfp_package.uninstall_pfp(
        "pawflow.avatar-pack.starter", user_id="alice",
        scope="user", force=True)["ok"] is True
    assert ExtensionRepository.instance().get(
        "pawflow.avatar", "pawflow-bot",
        user_id="alice", scope="user") is None

    exported = tmp_path / "exported-avatar-pack.pfp"
    shutil.copyfile(pack_artifact, exported)
    assert pfp_package.install_pfp(
        str(exported), user_id="alice", force=True)["ok"] is True
    imported = ExtensionRepository.instance().get(
        "pawflow.avatar", "pawflow-bot", user_id="alice", scope="user")
    assert imported["document"]["title"] == "PawFlow Bot"

    assert pfp_package.uninstall_pfp(
        "pawflow.avatar-pack.starter", user_id="alice",
        scope="user", force=True)["ok"] is True
    assert pfp_package.uninstall_pfp(
        "pawflow.avatar-runtime", user_id="alice",
        scope="user", force=True)["ok"] is True
