"""Pixazo image service — catalog-driven dispatch over (model, op, convention).

Covers:
  - JSON catalog loading.
  - text_to_image dispatch for sync, legacy_poll, polling_url conventions.
  - edit_image dispatch (nano-banana convention='polling_url').
  - polling_url branch: POST returns absolute URL → GET it.
  - legacy_poll branch: POST request_id → POST {request_id} to poll_endpoint.
  - URL extraction from output.media_url[0] (new convention).
  - Unknown model / unknown operation error paths.
"""

from unittest.mock import patch

import pytest

from services.pixazo_image_service import (
    PixazoImageService, _load_catalog, _CATALOG_CACHE,
)
import services.pixazo_image_service as _svc_mod


def _svc(model: str = "nano-banana-pro") -> PixazoImageService:
    s = PixazoImageService({"api_key": "xxx", "model": model,
                             "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


# ── Catalog ─────────────────────────────────────────────────────────────


def test_catalog_loads_and_contains_known_models():
    catalog = _load_catalog()
    assert "nano-banana" in catalog
    assert "nano-banana-2" in catalog
    assert "nano-banana-pro" in catalog
    assert "sdxl" in catalog
    assert "flux-dev" in catalog


def test_nano_banana_has_edit_image_op():
    catalog = _load_catalog()
    nb = catalog["nano-banana"]
    assert "edit_image" in nb["operations"]
    assert nb["operations"]["edit_image"]["convention"] == "polling_url"
    assert nb["operations"]["edit_image"]["input_field"] == "image_urls"


def test_nano_banana_pro_uses_polling_url():
    catalog = _load_catalog()
    op = catalog["nano-banana-pro"]["operations"]["text_to_image"]
    assert op["convention"] == "polling_url"
    assert op["endpoint"] == "/nano-banana-pro-async/v1/nano-banana-pro-text-to-image"


def test_sdxl_is_sync():
    catalog = _load_catalog()
    op = catalog["sdxl"]["operations"]["text_to_image"]
    assert op["convention"] == "sync"
    assert op["url_field"] == "imageUrl"


def test_flux_dev_is_legacy_poll():
    catalog = _load_catalog()
    op = catalog["flux-dev"]["operations"]["text_to_image"]
    assert op["convention"] == "legacy_poll"
    assert op["poll_endpoint"] == "/flux-dev-polling/dev/getFluxDevStatus"


# ── Convention dispatch ────────────────────────────────────────────────


def test_text_to_image_sync_returns_url_inline():
    s = _svc("sdxl")
    s._post = lambda ep, body: {"imageUrl": "https://cdn/x.png"}  # type: ignore[assignment]
    s._download_image = lambda u: (b"PNG", "image/png")  # type: ignore[assignment]
    out = s.generate(prompt="hi", width=256, height=256)
    assert out["image_bytes"] == b"PNG"
    assert out["source_url"] == "https://cdn/x.png"


def test_text_to_image_polling_url_follows_url():
    """nano-banana-pro: POST → polling_url → GET → output.media_url[0]."""
    s = _svc("nano-banana-pro")
    s._post = lambda ep, body: {  # type: ignore[assignment]
        "request_id": "rid",
        "status": "QUEUED",
        "polling_url": "https://gw/v2/requests/status/rid",
    }
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed",
        "output": {"media_url": ["https://cdn/done.png"]},
    }
    s._download_image = lambda u: (b"BYTES", "image/png")  # type: ignore[assignment]
    out = s.generate(prompt="robot")
    assert out["image_bytes"] == b"BYTES"
    assert out["source_url"] == "https://cdn/done.png"


def test_text_to_image_legacy_poll():
    """flux-dev: POST → request_id → POST poll_endpoint → imageUrl."""
    s = _svc("flux-dev")
    posts = []

    def _fake_post(ep, body):
        posts.append((ep, body))
        if ep.endswith("/textToImage"):
            return {"requestId": "rid-1"}
        # polling
        return {"status": "completed", "imageUrl": "https://cdn/legacy.png"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_image = lambda u: (b"FLX", "image/png")  # type: ignore[assignment]
    out = s.generate(prompt="x")
    assert out["source_url"] == "https://cdn/legacy.png"
    assert posts[0][0] == "/flux-dev/v1/dev/textToImage"
    assert posts[1][0] == "/flux-dev-polling/dev/getFluxDevStatus"
    assert posts[1][1]["request_id"] == "rid-1" or posts[1][1]["requestId"] == "rid-1"


def test_polling_status_uppercase_treated_as_completed():
    """Pixazo returns 'COMPLETED' uppercase — `.lower()` must normalize it."""
    s = _svc("nano-banana-pro")
    s._post = lambda ep, body: {  # type: ignore[assignment]
        "request_id": "rid",
        "polling_url": "https://gw/v2/requests/status/rid",
    }
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "COMPLETED",
        "output": {"media_url": ["https://cdn/upper.png"]},
    }
    s._download_image = lambda u: (b"OK", "image/png")  # type: ignore[assignment]
    assert s.generate(prompt="x")["source_url"] == "https://cdn/upper.png"


def test_legacy_poll_in_progress_then_completed():
    s = _svc("nano-banana")
    posts = []

    def _fake_post(ep, body):
        posts.append(ep)
        if ep.endswith("generateTextToImageRequest"):
            return {"request_id": "rid"}
        if len(posts) <= 2:
            return {"status": "PROCESSING"}
        return {"status": "completed", "imageUrl": "https://cdn/nb.png"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_image = lambda u: (b"NB", "image/png")  # type: ignore[assignment]
    assert s.generate(prompt="x")["source_url"] == "https://cdn/nb.png"


# ── edit_image ─────────────────────────────────────────────────────────


def test_edit_image_dispatches_to_edit_op():
    """edit_image hits the edit endpoint with image_urls in body."""
    s = _svc("nano-banana")
    captured = {}

    def _fake_post(ep, body):
        captured["ep"] = ep
        captured["body"] = body
        return {"request_id": "rid",
                "polling_url": "https://gw/v2/requests/status/rid"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed",
        "output": {"media_url": ["https://cdn/edited.png"]},
    }
    s._download_image = lambda u: (b"E", "image/png")  # type: ignore[assignment]

    out = s.edit_image(prompt="add hat",
                        image_urls=["https://src/in.png"],
                        num_images=2, output_format="png")
    assert out["source_url"] == "https://cdn/edited.png"
    assert captured["ep"] == "/nano-banana/v1/nano-banana/generateEditImageRequest"
    assert captured["body"]["image_urls"] == ["https://src/in.png"]
    assert captured["body"]["num_images"] == 2


def test_edit_image_requires_image_urls():
    s = _svc("nano-banana")
    with pytest.raises(Exception, match="image_urls"):
        s.edit_image(prompt="x", image_urls=[])


def test_edit_image_unsupported_on_model_without_op():
    s = _svc("sdxl")  # sdxl has no edit_image op
    with pytest.raises(Exception, match="does not support operation 'edit_image'"):
        s.edit_image(prompt="x", image_urls=["http://i/p.png"])


# ── Errors ─────────────────────────────────────────────────────────────


def test_unknown_model_errors_clearly():
    s = PixazoImageService({"api_key": "k", "model": "does-not-exist"})
    with pytest.raises(Exception, match="Unknown Pixazo model"):
        s._model()


def test_get_model_info_lists_operations_and_all_models():
    s = _svc("nano-banana")
    info = s.get_model_info()
    assert info["model"] == "nano-banana"
    assert "text_to_image" in info["operations"]
    assert "edit_image" in info["operations"]
    assert info["operations"]["edit_image"]["input_field"] == "image_urls"
    assert "sdxl" in info["all_models"]


# ── URL extraction ─────────────────────────────────────────────────────


def test_extract_url_from_nested_output_media_url():
    url = PixazoImageService._extract_image_url(
        {"output": {"media_url": ["https://cdn/x.png"]}})
    assert url == "https://cdn/x.png"


def test_extract_url_from_legacy_imageUrl_field():
    url = PixazoImageService._extract_image_url({"imageUrl": "https://cdn/y.png"})
    assert url == "https://cdn/y.png"


def test_extract_url_from_configured_url_field():
    url = PixazoImageService._extract_image_url(
        {"customField": "https://cdn/z.png"}, url_field="customField")
    assert url == "https://cdn/z.png"
