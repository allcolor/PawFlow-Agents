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
import mimetypes
import os
from pathlib import Path

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


def resize_image_path_for_vision(path, mime: str = "",
                                 *, max_dim: int = MAX_DIM,
                                 max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """Decode an image from disk and return a bounded vision payload.

    Large source files are never read wholesale before decoding.  The original
    bytes are read only when the file already fits ``max_bytes``; otherwise
    Pillow re-encodes the decoded image directly into the bounded output.
    """
    source = Path(path)
    size = source.stat().st_size
    try:
        from PIL import Image
    except ImportError:
        if size > max_bytes:
            raise ValueError("Pillow is required to process this large image")
        return source.read_bytes(), mime

    try:
        with Image.open(source) as opened:
            w, h = opened.size
            if max(w, h) <= max_dim and size <= max_bytes:
                return source.read_bytes(), mime
            image = opened.copy()
    except Exception as exc:
        if size > max_bytes:
            raise ValueError("Large image could not be decoded safely") from exc
        return source.read_bytes(), mime

    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        if w >= h:
            target = (max_dim, max(1, round(h * scale)))
        else:
            target = (max(1, round(w * scale)), max_dim)
        image = image.resize(target, Image.LANCZOS)
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    data = output.getvalue()
    logger.info("resized image path for vision: %dx%d (%d bytes) -> %dx%d (%d bytes)",
                w, h, size, image.size[0], image.size[1], len(data))
    return data, "image/jpeg"


def write_vision_image(out_dir, stem: str, data: bytes, *, mime: str = "",
                       filename: str = "") -> str:
    """Downscale ``data`` and write it as ``<stem><suffix>`` under ``out_dir``.

    Returns the file name that was written. Use this — not a bare
    ``write_bytes`` — for every image materialised on disk for an agent to read
    (``.pawflow_vision`` payloads for the CLI providers). Those agents open the
    file themselves, so an oversized image is rejected at *their* read time with
    the provider's 2000px error, and unlike the base64 paths nothing downscales
    it on the way in. The suffix follows the encoding actually written, so a
    re-encoded PNG lands as ``.jpg`` rather than lying about its content.
    """
    out, out_mime = resize_image_for_vision(data, mime)
    if out is not data:
        suffix = mimetypes.guess_extension(out_mime or "") or ".jpg"
    else:
        suffix = (Path(filename).suffix
                  or mimetypes.guess_extension(mime or "") or ".png")
    if suffix == ".jpe":
        suffix = ".jpg"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{stem}{suffix}"
    (out_dir / name).write_bytes(out)
    return name
