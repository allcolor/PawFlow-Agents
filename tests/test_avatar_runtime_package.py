"""Tests for the installable pawflow.avatar-runtime PFP package."""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
import types
from pathlib import Path

import jsonschema
import pytest

from core import pfp_package
from core.extension_repository import ExtensionRepository


ROOT = Path("packages/pawflow.avatar-runtime.pfpdir")
MANIFEST = ROOT / "pfp.json"
SCHEMA = ROOT / "content/repository/avatar.schema.json"
SYNTHETIC = ROOT / "content/avatars/synthetic.json"
UI = ROOT / "content/ui/extension.js"
HANDLER = ROOT / "content/handlers/avatar.py"
TOOL = ROOT / "content/tools/avatar-ui/main.py"

VENDOR_HASHES = {
    "avatar-vendor.js":
        "3aa4136ee2ee06fb922302aa86e51fdac9a463cbdd21868d62919a05dad5dc3a",
    "headworklet.min.js":
        "37ebeb1d4d7e41fca7d12bb8fb411f7ce6bb21a2589602dec18e0a48b343be55",
    "model-en-mixed.bin":
        "0358f68989b5861f9b7d18871b010fa6cbf88a53bda4954a954d8c548bbcf251",
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_avatar_package_is_extension_only_and_declares_all_runtime_objects():
    manifest = _json(MANIFEST)
    assert manifest["package"] == "pawflow.avatar-runtime"
    objects = {obj["id"]: obj for obj in manifest["objects"]}
    assert set(objects) == {
        "repository_type:avatar",
        "repository_resource:synthetic",
        "ui_extension:avatar-runtime",
        "tool:avatar-ui",
    }

    repository_type = objects["repository_type:avatar"]
    assert repository_type["resource_type"] == "pawflow.avatar"
    assert repository_type["contributions"] == "dependencies"
    assert repository_type["mutable"] is True

    extension = objects["ui_extension:avatar-runtime"]
    assert extension["assets"]["scripts"] == [
        "content/ui/avatar-vendor.js",
        "content/ui/extension.js",
    ]
    assert extension["assets"]["worklets"] == [{
        "id": "head-audio-worklet",
        "path": "content/ui/headworklet.min.js",
    }]
    assert {slot["slot"] for slot in extension["slots"]} >= {
        "header_actions",
        "conversation_stage",
        "resources_collection",
        "composer_accessory",
    }


def test_avatar_schema_accepts_fixture_and_requires_model_for_talkinghead():
    schema = _json(SCHEMA)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(_json(SYNTHETIC))

    invalid = _json(SYNTHETIC)
    invalid["renderer"] = "talkinghead"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)

    valid = _json(SYNTHETIC)
    valid["renderer"] = "talkinghead"
    valid["model"] = {"asset": "model", "format": "glb", "body": "F"}
    validator.validate(valid)


def test_avatar_vendor_artifacts_and_license_notices_are_pinned():
    ui_dir = ROOT / "content/ui"
    for name, expected in VENDOR_HASHES.items():
        assert hashlib.sha256((ui_dir / name).read_bytes()).hexdigest() == expected

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for revision in (
        "67a210b91486a42e58d38fd5682fbfc6754f67bd",
        "d3af5f9ff86ab6b2b1913d411a4e1922ec101953",
        "bd780a19e10d1cc5736a77946b04e08d658d5bf8",
    ):
        assert revision in notices
    for name in (
        "TalkingHead-LICENSE.txt",
        "HeadAudio-LICENSE.txt",
        "MotionEngine-LICENSE.txt",
        "Three-LICENSE.txt",
    ):
        assert (ROOT / "licenses" / name).is_file()


def test_avatar_vendor_build_recipe_pins_inputs_patches_and_hashes():
    recipe = Path("scripts/build-avatar-vendor.sh").read_text(encoding="utf-8")
    entry = Path("scripts/avatar_vendor/entry.js").read_text(encoding="utf-8")
    talkinghead_patch = Path(
        "scripts/avatar_vendor/talkinghead-pawflow.patch").read_text(
            encoding="utf-8")
    motion_patch = Path(
        "scripts/avatar_vendor/motionengine-no-facemirror.patch").read_text(
            encoding="utf-8")

    for revision in (
        "67a210b91486a42e58d38fd5682fbfc6754f67bd",
        "d3af5f9ff86ab6b2b1913d411a4e1922ec101953",
        "bd780a19e10d1cc5736a77946b04e08d658d5bf8",
    ):
        assert revision in recipe
        assert revision in entry
    assert "esbuild@0.25.9" in recipe
    assert "three@0.180.0" in recipe
    for digest in VENDOR_HASHES.values():
        assert digest in recipe
    assert "PAWFLOW_AVATAR_PLAYBACK_WORKLET_URL" in talkinghead_patch
    assert "FaceMirror is not bundled" in motion_patch


def test_avatar_package_builds_inspects_installs_and_uninstalls(tmp_path):
    keypair = pfp_package.create_signing_key()
    artifact = pfp_package.build_pfp(
        str(ROOT),
        output_path=str(tmp_path / "pawflow.avatar-runtime-0.1.0.pfp"),
        private_key=keypair["private_key"],
    )
    plan = pfp_package.inspect_pfp(artifact["path"], user_id="alice")
    assert plan["verified"] is True
    assert plan["package"] == "pawflow.avatar-runtime"
    assert [(row["id"], row["status"]) for row in plan["objects"]] == [
        ("repository_type:avatar", "new"),
        ("repository_resource:synthetic", "new"),
        ("ui_extension:avatar-runtime", "new"),
        ("tool:avatar-ui", "new"),
    ]

    installed = pfp_package.install_pfp(
        artifact["path"], user_id="alice", force=True)
    assert installed["ok"] is True
    stored = ExtensionRepository.instance().get(
        "pawflow.avatar", "synthetic", user_id="alice", scope="user")
    assert stored["document"]["renderer"] == "synthetic"
    extensions = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")
    assert [row["package"] for row in extensions] == [
        "pawflow.avatar-runtime"]

    removed = pfp_package.uninstall_pfp(
        "pawflow.avatar-runtime", user_id="alice", scope="user", force=True)
    assert removed["ok"] is True
    assert pfp_package.resolve_repository_type(
        "pawflow.avatar", user_id="alice") is None
    assert pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user") == []


class _RepositoryFake:
    def __init__(self):
        self.calls = []

    def list(self, resource_type):
        self.calls.append(("list", resource_type))
        return [{"name": "synthetic"}]

    def get(self, resource_type, name):
        self.calls.append(("get", resource_type, name))
        return {"name": name}

    def create(self, resource_type, name, document):
        self.calls.append(("create", resource_type, name, document))
        return {"name": name, "document": document}

    def update(self, resource_type, name, document):
        self.calls.append(("update", resource_type, name, document))
        return {"name": name, "document": document}

    def delete(self, resource_type, name):
        self.calls.append(("delete", resource_type, name))
        return {"name": name}


class _PfpFake:
    def __init__(self, payload):
        self.payload = payload
        self.repository = _RepositoryFake()
        self.results = []

    def result(self, value):
        self.results.append(value)


def _run_with_pfp(monkeypatch, path: Path, pfp):
    module = types.ModuleType("pawflow")
    module.pfp = pfp
    monkeypatch.setitem(sys.modules, "pawflow", module)
    runpy.run_path(str(path), run_name="__main__")


@pytest.mark.parametrize(
    ("action", "arguments", "expected"),
    [
        ("avatar.list", {}, ("list", "pawflow.avatar")),
        ("avatar.get", {"name": "synthetic"},
         ("get", "pawflow.avatar", "synthetic")),
        ("avatar.create", {"name": "custom", "document": {"title": "Custom"}},
         ("create", "pawflow.avatar", "custom", {"title": "Custom"})),
        ("avatar.update", {"name": "custom", "document": {"title": "Changed"}},
         ("update", "pawflow.avatar", "custom", {"title": "Changed"})),
        ("avatar.delete", {"name": "custom"},
         ("delete", "pawflow.avatar", "custom")),
    ],
)
def test_avatar_handler_dispatches_repository_calls(
        monkeypatch, action, arguments, expected):
    fake = _PfpFake({"action": action, "arguments": arguments})
    _run_with_pfp(monkeypatch, HANDLER, fake)
    assert fake.repository.calls == [expected]
    assert len(fake.results) == 1


def test_avatar_handler_rejects_missing_required_name(monkeypatch):
    fake = _PfpFake({"action": "avatar.get", "arguments": {}})
    with pytest.raises(ValueError, match="name is required"):
        _run_with_pfp(monkeypatch, HANDLER, fake)


class _SemanticFake:
    def __init__(self):
        self.calls = []

    def list(self, package):
        self.calls.append(("list", package))
        return []

    def get(self, package, node):
        self.calls.append(("get", package, node))
        return {"id": node}

    def invoke(self, package, node, action, arguments):
        self.calls.append(("invoke", package, node, action, arguments))
        return {"ok": True}


def test_avatar_semantic_tool_invokes_only_package_node(monkeypatch):
    semantic = _SemanticFake()
    fake = _PfpFake({
        "arguments": {
            "operation": "invoke",
            "action": "select",
            "arguments": {"name": "synthetic"},
        }
    })
    fake.browser = types.SimpleNamespace(semantic=semantic)
    _run_with_pfp(monkeypatch, TOOL, fake)
    assert semantic.calls == [(
        "invoke",
        "pawflow.avatar-runtime",
        "pawflow.avatar-runtime:stage.avatar",
        "select",
        {"name": "synthetic"},
    )]
    assert fake.results == [{"ok": True}]


def test_avatar_browser_runtime_wires_lazy_media_semantics_and_teardown():
    source = UI.read_text(encoding="utf-8")
    for marker in (
        "pawflow.register(PACKAGE_ID",
        "pfp.semantic.register",
        "media_track_subscribed",
        "media_track_unsubscribed",
        "media_audio_frame",
        "new vendor.TalkingHead",
        "new vendor.HeadAudio",
        "new vendor.MotionEngine",
        "audioWorklet.addModule",
        "createMediaStreamSource",
        "createBufferSource",
        "state.head.dispose()",
        "audioContext.close()",
        "pfp.on('shutdown'",
    ):
        assert marker in source
    load_selected = source.split("function loadSelected()", 1)[1].split(
        "function selectAvatar", 1)[0]
    assert load_selected.index("if (!state.visible)") < load_selected.index(
        "return loadTalkingHead(row, token)")
    assert "brunette.glb" not in source
    assert "https://" not in _json(MANIFEST).__str__()
