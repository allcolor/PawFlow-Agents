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
import os

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Positive int from env, else ``default`` (0/blank/garbage -> default)."""
    try:
        v = int(os.getenv(name, "") or 0)
        return v if v > 0 else default
    except ValueError:
        return default


# Longest-edge ceiling. The Anthropic vision API hard-rejects images above
# 2000px AND internally downscales anything above ~1568px on the longest edge
# for tokenisation, so 1568 is the largest size the model actually "sees" --
# bigger just spends tokens on pixels the API throws away. Default to that
# sweet spot (good detail for screenshots/text without waste); override via
# PAWFLOW_VISION_MAX_DIM for higher-detail needs. The value is clamped just
# below the 2000px provider reject so an override can never trip it.
_VISION_HARD_CAP = 1999
MAX_DIM = min(_env_int("PAWFLOW_VISION_MAX_DIM", 1568), _VISION_HARD_CAP)
# Above this byte size we re-encode even when the dimensions already fit, to
# keep vision payloads (and context tokens) bounded.
MAX_BYTES = _env_int("PAWFLOW_VISION_MAX_BYTES", 1_000_000)


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
            # Pin the longest edge exactly to max_dim and scale the other
            # proportionally. (int(w * max_dim/max(w,h)) can truncate the long
            # edge to max_dim-1 on float rounding, leaving the result a pixel
            # under the ceiling.)
            scale = max_dim / float(max(w, h))
            if w >= h:
                new_w, new_h = max_dim, max(1, round(h * scale))
            else:
                new_w, new_h = max(1, round(w * scale)), max_dim
            img = img.resize((new_w, new_h), Image.LANCZOS)
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
