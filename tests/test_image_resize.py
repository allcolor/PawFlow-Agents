"""Tests for the shared provider-agnostic vision image downscaler."""

import io

import pytest

from core.image_resize import resize_image_for_vision, MAX_DIM

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(w: int, h: int, color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_oversized_image_is_downscaled_to_ceiling_and_jpeg():
    data = _png(4000, 2250)
    out, mime = resize_image_for_vision(data)

    assert mime == "image/jpeg"
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) == MAX_DIM
    # Aspect ratio preserved (4000:2250 == 16:9). At the default 1568px
    # ceiling that is 1568x882; recomputed from MAX_DIM so an env override
    # of PAWFLOW_VISION_MAX_DIM does not break the assertion.
    scale = MAX_DIM / 4000.0
    assert (w, h) == (MAX_DIM, round(2250 * scale))


def test_within_limits_is_returned_unchanged():
    data = _png(800, 600)
    out, mime = resize_image_for_vision(data, "image/png")

    assert out is data
    assert mime == "image/png"


def test_rgba_oversized_is_flattened_to_rgb_jpeg():
    buf = io.BytesIO()
    Image.new("RGBA", (3000, 100), (1, 2, 3, 128)).save(buf, format="PNG")
    out, mime = resize_image_for_vision(buf.getvalue())

    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"
    assert max(img.size) == MAX_DIM


def test_corrupt_bytes_are_returned_unchanged():
    data = b"not an image"
    out, mime = resize_image_for_vision(data, "image/png")

    assert out is data
    assert mime == "image/png"


def test_empty_is_noop():
    out, mime = resize_image_for_vision(b"", "image/png")
    assert out == b""
    assert mime == "image/png"


def test_attachment_ingestion_downscales_before_storing():
    """An oversized user attachment is resized at ingestion, so the stored
    copy every downstream path reads is already within the vision ceiling."""
    import base64
    from core.file_store import FileStore
    from tasks.ai.agent_context import AgentContextMixin

    oversized = _png(4000, 4000)
    attachments = [{
        "filename": "phone.png",
        "mime_type": "image/png",
        "data": base64.b64encode(oversized).decode("ascii"),
    }]

    # _build_user_content does not touch self; a bare instance is enough.
    content = AgentContextMixin._build_user_content(
        object.__new__(AgentContextMixin),
        "look at this", attachments,
        conversation_id="c_resize", user_id="u_resize")

    refs = [p for p in content if isinstance(p, dict) and p.get("type") == "image_ref"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["mime_type"] == "image/jpeg"

    _, raw, _ = FileStore.instance().get(ref["file_id"], user_id="u_resize")
    w, h = Image.open(io.BytesIO(raw)).size
    assert max(w, h) == MAX_DIM
    assert ref["size"] == len(raw)


def test_preuploaded_oversized_jpeg_is_restored_after_downscale():
    from core.file_store import FileStore
    from tasks.ai.agent_context import AgentContextMixin

    buf = io.BytesIO()
    Image.new("RGB", (4000, 4000), (10, 120, 200)).save(buf, format="JPEG")
    oversized = buf.getvalue()
    store = FileStore.instance()
    original_fid = store.store(
        "phone.jpg", oversized, "image/jpeg",
        user_id="u_resize_fid", conversation_id="c_resize_fid",
        category="attachment")
    attachments = [{
        "filename": "phone.jpg",
        "mime_type": "image/jpeg",
        "file_id": original_fid,
    }]

    content = AgentContextMixin._build_user_content(
        object.__new__(AgentContextMixin),
        "look at this", attachments,
        conversation_id="c_resize_fid", user_id="u_resize_fid")

    refs = [p for p in content if isinstance(p, dict) and p.get("type") == "image_ref"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["file_id"] != original_fid
    assert ref["mime_type"] == "image/jpeg"

    _, raw, _ = store.get(ref["file_id"], user_id="u_resize_fid")
    w, h = Image.open(io.BytesIO(raw)).size
    assert max(w, h) == MAX_DIM
    assert len(raw) == ref["size"]
