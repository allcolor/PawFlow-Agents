"""Server-side helpers for token-free stale-screen click guards."""

import base64
import hashlib
import hmac
import io
import time
from typing import Any, Dict, Optional, Sequence, Tuple


SCREENSHOT_TTL_SECONDS = 5 * 60
SCREENSHOT_TTL_KEYS = ("screenshot_ttl_seconds", "webchat_screenshot_ttl_seconds")
_ROUTE_DIGEST_LENGTH = 16
_MAX_GUARD_DIMENSION = 512


def screen_route_key(service: Any, local: bool) -> str:
    """Return a stable identity for one relay display target."""
    service_id = (getattr(service, "_service_id", "")
                  or getattr(service, "service_id", "")
                  or type(service).__name__)
    return f"{service_id}|local={int(bool(local))}"


def _route_digest(route_key: str) -> str:
    return hashlib.sha256(route_key.encode("utf-8")).hexdigest()[:_ROUTE_DIGEST_LENGTH]


def store_screen_capture(
        image_bytes: bytes, *, user_id: str, conversation_id: str,
        route_key: str) -> Tuple[str, str]:
    """Store an original screenshot and return ``(url, opaque_revision)``."""
    if not user_id or not conversation_id:
        raise ValueError("screen capture requires user_id and conversation_id")
    if not image_bytes:
        raise ValueError("screen capture is empty")

    from core.file_store import FileStore
    from core.file_ttl import resolve_ttl_seconds

    filename = f"screenshot_{time.time_ns()}.png"
    file_id = FileStore.instance().store(
        filename, image_bytes, "image/png",
        user_id=user_id,
        conversation_id=conversation_id,
        ttl=resolve_ttl_seconds(
            conversation_id=conversation_id,
            conv_keys=SCREENSHOT_TTL_KEYS,
            env_key="PAWFLOW_SCREENSHOT_TTL_SECONDS",
            default=SCREENSHOT_TTL_SECONDS),
        category="screenshot")
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    revision = f"{file_id}:{image_digest}:{_route_digest(route_key)}"
    return f"fs://filestore/{file_id}/{filename}", revision


def _parse_revision(revision: str) -> Tuple[str, str, str]:
    parts = revision.split(":")
    if len(parts) != 3:
        raise ValueError("invalid expected_screen_revision; take a new screenshot")
    file_id, image_digest, route_digest = parts
    if (not file_id or len(image_digest) != 64
            or any(c not in "0123456789abcdef" for c in image_digest)
            or len(route_digest) != _ROUTE_DIGEST_LENGTH
            or any(c not in "0123456789abcdef" for c in route_digest)):
        raise ValueError("invalid expected_screen_revision; take a new screenshot")
    return file_id, image_digest, route_digest


def _guard_box(image_size: Tuple[int, int], x: int, y: int,
               target_bbox: Optional[Sequence[Any]]) -> Tuple[int, int, int, int]:
    image_width, image_height = image_size
    if not (0 <= x < image_width and 0 <= y < image_height):
        raise ValueError("click coordinates are outside the referenced screenshot")

    if target_bbox is None:
        left, top, right, bottom = x - 64, y - 48, x + 64, y + 48
    else:
        if (not isinstance(target_bbox, (list, tuple))
                or len(target_bbox) != 4):
            raise ValueError("target_bbox must be [x, y, width, height]")
        try:
            box_x, box_y, box_width, box_height = (
                int(value) for value in target_bbox)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "target_bbox must contain four integers") from exc
        if box_width <= 0 or box_height <= 0:
            raise ValueError("target_bbox width and height must be positive")
        if not (box_x <= x < box_x + box_width
                and box_y <= y < box_y + box_height):
            raise ValueError("click coordinates must be inside target_bbox")
        margin = max(16, min(48, max(box_width, box_height) // 4))
        left = box_x - margin
        top = box_y - margin
        right = box_x + box_width + margin
        bottom = box_y + box_height + margin

    left = max(0, left)
    top = max(0, top)
    right = min(image_width, right)
    bottom = min(image_height, bottom)
    if right - left > _MAX_GUARD_DIMENSION:
        left = max(0, min(
            x - _MAX_GUARD_DIMENSION // 2,
            image_width - _MAX_GUARD_DIMENSION))
        right = min(image_width, left + _MAX_GUARD_DIMENSION)
    if bottom - top > _MAX_GUARD_DIMENSION:
        top = max(0, min(
            y - _MAX_GUARD_DIMENSION // 2,
            image_height - _MAX_GUARD_DIMENSION))
        bottom = min(image_height, top + _MAX_GUARD_DIMENSION)
    if right <= left or bottom <= top:
        raise ValueError("target region is empty")
    return left, top, right, bottom


def prepare_click_guard(
        revision: str, *, user_id: str, conversation_id: str,
        route_key: str, x: int, y: int,
        target_bbox: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """Resolve a revision into a private reference crop for the relay."""
    if not revision:
        raise ValueError(
            "expected_screen_revision is required for click actions; "
            "take a new screen screenshot first")
    file_id, expected_digest, expected_route = _parse_revision(revision)
    if not hmac.compare_digest(expected_route, _route_digest(route_key)):
        raise ValueError(
            "expected_screen_revision belongs to another relay/display; "
            "take a new screenshot")

    from core.file_store import FileStore

    _filename, image_bytes, _content_type = FileStore.instance().get_required(
        file_id, user_id=user_id, conversation_id=conversation_id)
    actual_digest = hashlib.sha256(image_bytes).hexdigest()
    if not hmac.compare_digest(expected_digest, actual_digest):
        raise ValueError("screen revision integrity check failed")

    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as source:
        source.load()
        left, top, right, bottom = _guard_box(
            source.size, int(x), int(y), target_bbox)
        crop = source.convert("RGB").crop((left, top, right, bottom))
    output = io.BytesIO()
    crop.save(output, format="PNG")
    return {
        "revision": revision,
        "region": {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        },
        "expected_image": base64.b64encode(output.getvalue()).decode("ascii"),
    }
