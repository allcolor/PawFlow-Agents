"""Provider-agnostic image downscaling for vision payloads.

Every image that reaches a model's vision input — user-uploaded attachments,
`see`/`screen` captures, materialised tool-result images — must fit within the
provider's pixel ceiling (2000px on the longest edge for the Anthropic vision
API, which is also what Claude Code / antigravity enforce when the agent reads
a file). Oversized images are rejected at read time, so we downscale them
*proactively* at ingestion rather than depending on any single provider to do
it. This module is the one shared implementation; callers should not re-roll
their own PIL resize.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Longest-edge ceiling (Anthropic vision API limit; also the cap Claude Code /
# antigravity apply when the agent reads an image file).
MAX_DIM = 2000
# Above this byte size we re-encode even when the dimensions already fit, to
# keep vision payloads (and context tokens) bounded.
MAX_BYTES = 1_000_000


def resize_image_for_vision(data: bytes, mime: str = "",
                            *, max_dim: int = MAX_DIM,
                            max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """Return ``(data, mime)`` downscaled to fit the vision limits.

    Resizes to ``max_dim`` on the longest edge and/or re-encodes to JPEG when
    the image is larger than ``max_bytes``. Returns the input unchanged when it
    already fits, when Pillow is unavailable, or when decoding fails (callers
    must tolerate an occasional oversized image rather than lose it).
    """
    if not data:
        return data, mime
    try:
        from PIL import Image
    except ImportError:
        return data, mime
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:
        logger.debug("image decode failed; leaving payload unchanged", exc_info=True)
        return data, mime

    if max(w, h) <= max_dim and len(data) <= max_bytes:
        return data, mime

    try:
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        out = buf.getvalue()
    except Exception:
        logger.warning("image resize failed; leaving payload unchanged", exc_info=True)
        return data, mime

    logger.info("resized image for vision: %dx%d (%d bytes) -> %dx%d (%d bytes)",
                w, h, len(data), img.size[0], img.size[1], len(out))
    return out, "image/jpeg"
