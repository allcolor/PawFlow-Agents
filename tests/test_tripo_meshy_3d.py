"""Tests for the native Tripo3D and Meshy 3D services + 3D post-process tools."""

import json

import pytest

from core import ServiceError
from services.meshy_capability_services import Meshy3DService
from services.tripo_capability_services import Tripo3DService, _image_file_ref
from core.handlers._capability_3d_handlers import (
    Animate3DModelHandler, Retexture3DModelHandler, Rig3DModelHandler,
    _model_ext,
)


# ── Fakes ─────────────────────────────────────────────────────────────


def _meshy(monkeypatch, tasks, downloads=None):
    """Meshy service with scripted POST/GET responses.

    `tasks` maps a POST path to the terminal task object returned by the
    matching GET poll. Captures every request in `svc.calls`.
    """
    svc = Meshy3DService({"api_key": "k", "poll_interval": 0})
    svc._connection = {"ready": True}
    svc._connected = True
    svc.calls = []
    counter = {"n": 0}

    def fake_request(method, path, body=None):
        svc.calls.append((method, path, body))
        if method == "POST":
            counter["n"] += 1
            return {"result": f"task-{counter['n']}"}
        for prefix, task in tasks.items():
            if path.startswith(prefix):
                out = dict(task)
                out["id"] = path.rsplit("/", 1)[-1]
                return out
        raise AssertionError(f"unexpected GET {path}")

    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc, "_download",
        lambda url, fmt: (b"GLBDATA", "model/gltf-binary"))
    return svc


def _tripo(monkeypatch, terminal_task):
    svc = Tripo3DService({"api_key": "k", "poll_interval": 0})
    svc._connection = {"ready": True}
    svc._connected = True
    svc.calls = []
    counter = {"n": 0}

    def fake_request(method, path, body=None):
        svc.calls.append((method, path, body))
        if method == "POST":
            counter["n"] += 1
            return {"code": 0, "data": {"task_id": f"trip-{counter['n']}"}}
        task = dict(terminal_task)
        task["task_id"] = path.rsplit("/", 1)[-1]
        return {"code": 0, "data": task}

    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc, "_download",
        lambda url, fmt="glb": (b"GLBDATA", "model/gltf-binary"))
    return svc


_MESHY_MODEL_TASK = {
    "status": "SUCCEEDED",
    "model_urls": {"glb": "https://assets.meshy.ai/m.glb"},
    "thumbnail_url": "https://assets.meshy.ai/t.png",
}

_TRIPO_MODEL_TASK = {
    "status": "success",
    "output": {"pbr_model": "https://tripo-data/m.glb",
               "rendered_image": "https://tripo-data/r.png"},
}


# ── Meshy ─────────────────────────────────────────────────────────────


def test_meshy_text_to_3d_runs_preview_then_refine(monkeypatch):
    svc = _meshy(monkeypatch, {"/openapi/v2/text-to-3d": _MESHY_MODEL_TASK})
    r = svc.generate_3d(prompt="a monster mask", enable_pbr=True)
    posts = [c for c in svc.calls if c[0] == "POST"]
    assert len(posts) == 2
    assert posts[0][2]["mode"] == "preview"
    assert posts[0][2]["prompt"] == "a monster mask"
    assert "enable_pbr" not in posts[0][2]  # refine-only key
    assert posts[1][2]["mode"] == "refine"
    assert posts[1][2]["preview_task_id"] == "task-1"
    assert posts[1][2]["enable_pbr"] is True
    assert r["bytes"] == b"GLBDATA"
    assert r["task_id"]


def test_meshy_text_to_3d_preview_only_when_refine_false(monkeypatch):
    svc = _meshy(monkeypatch, {"/openapi/v2/text-to-3d": _MESHY_MODEL_TASK})
    svc.generate_3d(prompt="a chair", refine=False)
    posts = [c for c in svc.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["mode"] == "preview"


def test_meshy_image_to_3d_payload(monkeypatch):
    svc = _meshy(monkeypatch, {"/openapi/v1/image-to-3d": _MESHY_MODEL_TASK})
    svc.generate_3d(image_url="https://x/pic.png", topology="quad",
                    should_texture=True, bogus_param="dropped")
    method, path, body = [c for c in svc.calls if c[0] == "POST"][0]
    assert path == "/openapi/v1/image-to-3d"
    assert body["image_url"] == "https://x/pic.png"
    assert body["topology"] == "quad"
    assert body["target_formats"] == ["glb"]
    assert "bogus_param" not in body


def test_meshy_rig_and_animate_chain(monkeypatch):
    rig_task = {
        "status": "SUCCEEDED",
        "result": {"rigged_character_glb_url": "https://a/r.glb",
                   "basic_animations": {"walking_glb_url": "https://a/w.glb"}},
    }
    anim_task = {
        "status": "SUCCEEDED",
        "result": {"animation_glb_url": "https://a/anim.glb"},
    }
    svc = _meshy(monkeypatch, {"/openapi/v1/rigging": rig_task,
                               "/openapi/v1/animations": anim_task})
    rig = svc.rig_3d(model_url="https://a/model.glb", height_meters=1.8)
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[1] == "/openapi/v1/rigging"
    assert post[2] == {"model_url": "https://a/model.glb",
                       "height_meters": 1.8}
    assert rig["task_id"] == "task-1"
    assert rig["basic_animations"]["walking_glb_url"]

    anim = svc.animate_3d(rig_task_id=rig["task_id"], animation="92", fps=24)
    post = [c for c in svc.calls if c[0] == "POST"][-1]
    assert post[2]["rig_task_id"] == "task-1"
    assert post[2]["action_id"] == 92
    assert post[2]["post_process"] == {"operation_type": "change_fps",
                                       "fps": 24}
    assert anim["bytes"] == b"GLBDATA"


def test_meshy_animate_rejects_non_numeric_action(monkeypatch):
    svc = _meshy(monkeypatch, {})
    with pytest.raises(ServiceError, match="numeric"):
        svc.animate_3d(rig_task_id="task-1", animation="preset:walk")


def test_meshy_retexture_prefers_task_id_and_image_style(monkeypatch):
    svc = _meshy(monkeypatch, {"/openapi/v1/retexture": _MESHY_MODEL_TASK})
    svc.retexture_3d(task_id="gen-1", prompt="lava",
                     image_url="https://x/style.png", enable_pbr=True)
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[2]["input_task_id"] == "gen-1"
    assert post[2]["image_style_url"] == "https://x/style.png"
    assert "text_style_prompt" not in post[2]
    assert post[2]["enable_pbr"] is True


def test_meshy_poll_failure_raises_with_message(monkeypatch):
    failed = {"status": "FAILED", "task_error": {"message": "no credits"}}
    svc = _meshy(monkeypatch, {"/openapi/v2/text-to-3d": failed})
    with pytest.raises(ServiceError, match="no credits"):
        svc.generate_3d(prompt="x", refine=False)


# ── Tripo ─────────────────────────────────────────────────────────────


def test_tripo_text_to_model_payload(monkeypatch):
    svc = _tripo(monkeypatch, _TRIPO_MODEL_TASK)
    r = svc.generate_3d(prompt="a red apple", texture=True, pbr=True,
                        bogus="dropped")
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[1] == "/task"
    assert post[2]["type"] == "text_to_model"
    assert post[2]["prompt"] == "a red apple"
    assert post[2]["texture"] is True
    assert "bogus" not in post[2]
    assert r["task_id"] == "trip-1"
    assert r["source_url"] == "https://tripo-data/m.glb"


def test_tripo_image_to_model_uses_file_ref(monkeypatch):
    svc = _tripo(monkeypatch, _TRIPO_MODEL_TASK)
    svc.generate_3d(image_url="https://x/photo.PNG?sig=1", model="v2.5-20250123")
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[2]["type"] == "image_to_model"
    assert post[2]["file"] == {"type": "png", "url": "https://x/photo.PNG?sig=1"}
    assert post[2]["model_version"] == "v2.5-20250123"


def test_tripo_rig_and_retarget_payloads(monkeypatch):
    svc = _tripo(monkeypatch, _TRIPO_MODEL_TASK)
    rig = svc.rig_3d(task_id="gen-9", format="fbx", rig_type="biped")
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[2] == {"type": "animate_rig",
                       "original_model_task_id": "gen-9",
                       "out_format": "fbx", "rig_type": "biped"}

    svc.animate_3d(rig_task_id=rig["task_id"],
                   animation="preset:walk, preset:run")
    post = [c for c in svc.calls if c[0] == "POST"][-1]
    assert post[2]["type"] == "animate_retarget"
    assert post[2]["original_model_task_id"] == rig["task_id"]
    assert post[2]["animation"] == ["preset:walk", "preset:run"]


def test_tripo_rig_requires_task_id(monkeypatch):
    svc = _tripo(monkeypatch, _TRIPO_MODEL_TASK)
    with pytest.raises(ServiceError, match="task_id"):
        svc.rig_3d(model_url="https://x/model.glb")


def test_tripo_retexture_payload(monkeypatch):
    svc = _tripo(monkeypatch, _TRIPO_MODEL_TASK)
    svc.retexture_3d(task_id="gen-1", prompt="gold plated", pbr=True)
    post = [c for c in svc.calls if c[0] == "POST"][0]
    assert post[2]["type"] == "texture_model"
    assert post[2]["original_model_task_id"] == "gen-1"
    assert post[2]["text_prompt"] == "gold plated"
    assert post[2]["pbr"] is True


def test_tripo_api_error_code_raises(monkeypatch):
    svc = Tripo3DService({"api_key": "k"})
    svc._connection = {"ready": True}
    svc._connected = True

    class FakeResp:
        status = 200

        def read(self):
            return json.dumps({"code": 2010, "message": "bad prompt"}).encode()

        headers = {}

    class FakeConn:
        def __init__(self, *a, **k): ...
        def request(self, *a, **k): ...
        def getresponse(self): return FakeResp()
        def close(self): ...

    monkeypatch.setattr("http.client.HTTPSConnection", FakeConn)
    with pytest.raises(ServiceError, match="2010"):
        svc.generate_3d(prompt="x")


def test_tripo_image_file_ref_extensions():
    assert _image_file_ref("https://a/x.jpeg")["type"] == "jpg"
    assert _image_file_ref("https://a/x.webp")["type"] == "webp"
    assert _image_file_ref("https://a/no-ext")["type"] == "jpg"


# ── Handlers ──────────────────────────────────────────────────────────


class _FakeRig3DService:
    TYPE = "tripo3DGeneration"

    def __init__(self):
        self.calls = []

    def rig_3d(self, **kwargs):
        self.calls.append(("rig_3d", kwargs))
        return {"bytes": b"GLB", "content_type": "model/gltf-binary",
                "source_url": "https://a/r.glb", "task_id": "rig-1"}

    def animate_3d(self, **kwargs):
        self.calls.append(("animate_3d", kwargs))
        return {"bytes": b"GLB", "content_type": "model/gltf-binary",
                "source_url": "https://a/a.glb", "task_id": "anim-1"}

    def retexture_3d(self, **kwargs):
        self.calls.append(("retexture_3d", kwargs))
        return {"bytes": b"GLB", "content_type": "model/gltf-binary",
                "source_url": "https://a/t.glb", "task_id": "retex-1"}


def _wire(handler, svc, monkeypatch):
    handler.set_service_resolver(lambda: (svc, ""))
    monkeypatch.setattr(
        "core.handlers._capability_base._write_result",
        lambda user_id, conv_id, dest, filename, payload, ct: {
            "file_id": "fid123"})
    return handler


def test_rig_handler_returns_task_id(monkeypatch):
    svc = _FakeRig3DService()
    h = _wire(Rig3DModelHandler(), svc, monkeypatch)
    out = h.execute({"task_id": "gen-1", "height_meters": 1.8})
    assert "fs://filestore/fid123/" in out
    assert "task_id: rig-1" in out
    name, kwargs = svc.calls[0]
    assert name == "rig_3d"
    assert kwargs["task_id"] == "gen-1"
    assert kwargs["height_meters"] == 1.8


def test_animate_handler_requires_args(monkeypatch):
    svc = _FakeRig3DService()
    h = _wire(Animate3DModelHandler(), svc, monkeypatch)
    assert "Error" in h.execute({"rig_task_id": "", "animation": ""})
    out = h.execute({"rig_task_id": "rig-1", "animation": "preset:walk"})
    assert "task_id: anim-1" in out


def test_retexture_handler_requires_style(monkeypatch):
    svc = _FakeRig3DService()
    h = _wire(Retexture3DModelHandler(), svc, monkeypatch)
    out = h.execute({"task_id": "gen-1"})
    assert "Error" in out and "prompt" in out
    out = h.execute({"task_id": "gen-1", "prompt": "lava style"})
    assert "task_id: retex-1" in out


def test_handlers_reject_service_without_method(monkeypatch):
    class NoRig:
        TYPE = "wavespeed3DGeneration"

    for cls in (Rig3DModelHandler, Animate3DModelHandler,
                Retexture3DModelHandler):
        h = cls()
        h.set_service_resolver(lambda: (NoRig(), ""))
        out = h.execute({"task_id": "x", "rig_task_id": "x",
                         "animation": "1", "prompt": "p"})
        assert "does not support" in out


def test_model_ext_from_content_type_and_url():
    assert _model_ext({"content_type": "model/gltf-binary"}) == "glb"
    assert _model_ext({"content_type": "application/octet-stream",
                       "source_url": "https://a/m.fbx?Expires=1"}) == "fbx"
    assert _model_ext({"content_type": "", "source_url": "https://a/m"}) == "glb"


def test_handler_schemas_registered():
    from core.tool_registry import create_default_registry
    reg = create_default_registry()
    names = {t.name for t in reg.list_tools()}
    assert {"rig_3d_model", "animate_3d_model",
            "retexture_3d_model"} <= names
