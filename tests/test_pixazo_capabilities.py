"""Pixazo capability services (3D, upscale, try-on, lipsync, trainer).

Smoke tests only — asserts each service respects its category filter
and dispatches via the shared catalog machinery. HTTP is mocked via
monkey-patched `_post` / `_download_media` on the instance, same
pattern as test_pixazo_video_audio.py.

Catalog entries for these categories are added lazily in this module
so the tests stay self-contained and don't depend on doc ingestion.
"""

import pytest

from services._pixazo_base import _CATALOG_CACHE  # noqa: F401
import services._pixazo_base as _base
from services.pixazo_capability_services import (
    Pixazo3DService, PixazoUpscaleService, PixazoTryOnService,
    PixazoLipsyncService, PixazoTrainerService,
)


# Inject minimal catalog entries for each new category so the services
# can resolve a model without waiting for the real catalog to ship them.
@pytest.fixture(autouse=True)
def _inject_capability_catalog(monkeypatch):
    extra = {
        "hunyuan3d-test": {
            "label": "Hunyuan3D (test)",
            "category": "3d",
            "operations": {
                "image_to_3d": {
                    "endpoint": "/hunyuan3d/v1/generate",
                    "poll_endpoint": "/hunyuan3d/v1/status",
                    "convention": "polling_url",
                    "id_field": "request_id",
                    "input_field": "image_url",
                },
            },
        },
        "seedvr-test": {
            "label": "SeedVR Upscaler (test)",
            "category": "upscale",
            "operations": {
                "upscale": {
                    "endpoint": "/seedvr/v1/upscale",
                    "poll_endpoint": "/seedvr/v1/status",
                    "convention": "polling_url",
                    "id_field": "request_id",
                    "input_field": "image_url",
                },
            },
        },
        "fashn-test": {
            "label": "Fashn VTON (test)",
            "category": "try_on",
            "operations": {
                "try_on": {
                    "endpoint": "/fashn/v1/tryon",
                    "poll_endpoint": "/fashn/v1/status",
                    "convention": "polling_url",
                    "id_field": "request_id",
                    "person_field": "person_image",
                    "garment_field": "garment_image",
                },
            },
        },
        "omnihuman-test": {
            "label": "OmniHuman Lipsync (test)",
            "category": "lipsync",
            "operations": {
                "lipsync": {
                    "endpoint": "/omnihuman/v1/lipsync",
                    "poll_endpoint": "/omnihuman/v1/status",
                    "convention": "polling_url",
                    "id_field": "request_id",
                },
            },
        },
        "flux-lora-trainer-test": {
            "label": "Flux LoRA Trainer (test)",
            "category": "trainer",
            "operations": {
                "train": {
                    "endpoint": "/flux-lora/v1/train",
                    "poll_endpoint": "/flux-lora/v1/status",
                    "convention": "polling_url",
                    "id_field": "request_id",
                    "input_field": "image_data_url",
                },
            },
        },
    }
    # Merge into the real catalog for the test's lifetime.
    original = dict(_base._load_catalog())
    merged = {**original, **extra}
    monkeypatch.setattr(_base, "_CATALOG_CACHE", merged)


def _mk(cls, model: str):
    s = cls({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


def test_3d_dispatches_and_uses_input_field():
    s = _mk(Pixazo3DService, "hunyuan3d-test")
    captured = {}

    def _fake_post(ep, body):
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        return {"request_id": "r-1", "polling_url": "https://gw/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {"status": "completed", "output": "https://cdn/x.glb"}  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"GLB", "model/gltf-binary")  # type: ignore[assignment]

    out = s.generate_3d(image_url="https://src/in.png")
    assert out["bytes"] == b"GLB"
    assert captured["body"]["image_url"] == "https://src/in.png"


def test_upscale_dispatches_with_scale():
    s = _mk(PixazoUpscaleService, "seedvr-test")
    captured = {}

    def _fake_post(ep, body):
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        return {"request_id": "r-1", "polling_url": "https://gw/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {"status": "completed", "output": "https://cdn/up.png"}  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"PNG", "image/png")  # type: ignore[assignment]
    out = s.upscale(image_url="https://src/in.png", scale=4)
    assert out["bytes"] == b"PNG"
    assert captured["body"]["image_url"] == "https://src/in.png"
    assert captured["body"]["scale"] == 4


def test_tryon_dispatches_with_person_and_garment():
    s = _mk(PixazoTryOnService, "fashn-test")
    captured = {}

    def _fake_post(ep, body):
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        return {"request_id": "r-1", "polling_url": "https://gw/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {"status": "completed", "output": "https://cdn/tryon.png"}  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"T", "image/png")  # type: ignore[assignment]
    out = s.try_on(person_image="https://p/p.png",
                    garment_image="https://g/g.png")
    assert out["bytes"] == b"T"
    assert captured["body"]["person_image"] == "https://p/p.png"
    assert captured["body"]["garment_image"] == "https://g/g.png"


def test_lipsync_dispatches_with_audio_and_video():
    s = _mk(PixazoLipsyncService, "omnihuman-test")
    captured = {}

    def _fake_post(ep, body):
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        return {"request_id": "r-1", "polling_url": "https://gw/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {"status": "completed", "output": "https://cdn/ls.mp4"}  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]
    out = s.lipsync(video_url="https://v/v.mp4", audio_url="https://a/a.mp3")
    assert out["bytes"] == b"MP4"
    assert captured["body"]["video_url"] == "https://v/v.mp4"
    assert captured["body"]["audio_url"] == "https://a/a.mp3"


def test_trainer_returns_lora_url():
    s = _mk(PixazoTrainerService, "flux-lora-trainer-test")

    def _fake_post(ep, body):
        return {"request_id": "r-1", "polling_url": "https://gw/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {"status": "completed", "output": "https://cdn/lora.safetensors"}  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"LORA", "application/octet-stream")  # type: ignore[assignment]
    out = s.train(dataset_url="https://ds/data.zip",
                   base_model="flux-dev", steps=1000)
    assert out["lora_url"] == "https://cdn/lora.safetensors"
    assert out["status"] == "done"


def test_category_isolation_across_services():
    """Each service rejects models from other categories with a clear error."""
    with pytest.raises(Exception, match="category"):
        _mk(Pixazo3DService, "seedvr-test")._model()
    with pytest.raises(Exception, match="category"):
        _mk(PixazoUpscaleService, "fashn-test")._model()
    with pytest.raises(Exception, match="category"):
        _mk(PixazoTryOnService, "omnihuman-test")._model()
    with pytest.raises(Exception, match="category"):
        _mk(PixazoLipsyncService, "flux-lora-trainer-test")._model()
    with pytest.raises(Exception, match="category"):
        _mk(PixazoTrainerService, "hunyuan3d-test")._model()


def test_handlers_registered_in_default_registry():
    from core.tool_registry import create_default_registry
    names = {h.name for h in create_default_registry().list_tools()}
    assert {"generate_3d", "upscale_image", "try_on",
            "lipsync", "train_image_model"}.issubset(names)
