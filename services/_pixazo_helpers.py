"""Stateless Pixazo catalog / URL / error helpers + HTTP constants.

Extracted from ``_pixazo_base`` to keep that module <=800 lines. Every name
here is re-exported by ``services._pixazo_base`` for backward compatibility
(invariant 1). The catalog cache lives here, so tests that need a merged
catalog must monkeypatch ``_CATALOG_CACHE`` on THIS module.
"""

import json
import os
from typing import Any, Dict, List, Optional

from core import ServiceError


_GATEWAY = "gateway.pixazo.ai"

# Pixazo fronts every endpoint with Cloudflare. The model-specific
# POST endpoints (/<model>/v1/...) are whitelisted and accept API
# traffic directly. The polling endpoint (/v2/requests/status/<id>)
# is NOT whitelisted and serves a managed challenge to any non-browser
# client — curl, Python, cloudscraper, curl_cffi all get 403 with the
# "Just a moment..." HTML, because the rejection is IP-based /
# challenge-based, not TLS/UA-based. Verified 2026-04-15.
#
# The documented escape hatch is the X-Webhook-URL header on the
# generate request: Pixazo POSTs the result to the given URL when
# done, bypassing the challenge-gated poll endpoint entirely. We use
# it whenever a webhook receiver is registered (see
# PixazoWebhookReceiver), and fall back to polling otherwise — which
# works on IPs Cloudflare doesn't flag.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _catalog_path() -> str:
    """Locate the Pixazo catalog JSON in the repository."""
    import core.paths as _p
    return str(_p.REPOSITORY_DIR / "configs" / "pixazo_catalog.json")


_CATALOG_CACHE: Optional[Dict[str, Any]] = None


def _load_catalog() -> Dict[str, Any]:
    """Read pixazo_catalog.json, cache for the process lifetime."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    path = _catalog_path()
    if not os.path.exists(path):
        raise ServiceError(f"Pixazo catalog not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    models = data.get("models") or {}
    if not models:
        raise ServiceError(f"Pixazo catalog has no models: {path}")
    _CATALOG_CACHE = models
    return models


def models_for_category(category: str) -> List[str]:
    """Sorted model ids in the given category — populates select schemas."""
    out = []
    for mid, m in _load_catalog().items():
        if m.get("category", "image") == category:
            out.append(mid)
    return sorted(out)


def _resolve_output_path(data: Any, path: str) -> str:
    """Follow a dotted path inside the response dict.

    Supports dict keys and list indices (``foo.bar[0].url``). Returns
    the empty string when any step is missing — the caller then falls
    back to generic URL extraction heuristics.
    """
    if not path or not isinstance(data, (dict, list)):
        return ""
    cursor: Any = data
    # Normalize foo[0] → foo.0 for a single-pass split
    norm = path.replace("[", ".").replace("]", "")
    for part in norm.split("."):
        if part == "":
            continue
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError):
                return ""
        elif isinstance(cursor, dict):
            cursor = cursor.get(part)
            if cursor is None:
                return ""
        else:
            return ""
    if isinstance(cursor, list):
        cursor = cursor[0] if cursor else ""
    return cursor if isinstance(cursor, str) else ""


def _extract_media_url(data: Any, *, output_path: str = "",
                        url_field: str = "") -> str:
    """Find a media URL inside a Pixazo response, trying every known shape.

    Priority:
      1. Explicit `output_path` (dotted) — lets the catalog declare
         exactly where the URL lives (e.g. ``output.video_url``).
      2. Explicit `url_field` (legacy flat field name).
      3. Generic fallbacks: common top-level fields, nested
         ``output.media_url[0]``, ``images[0].url``.
    """
    if not isinstance(data, dict):
        return ""
    if output_path:
        v = _resolve_output_path(data, output_path)
        if v:
            return v
    if url_field:
        v = data.get(url_field, "")
        if v:
            return v[0] if isinstance(v, list) else v
    for field in ("imageUrl", "videoUrl", "audioUrl", "output",
                  "image_url", "video_url", "audio_url", "url",
                  "image", "video", "audio"):
        v = data.get(field, "")
        if v and not isinstance(v, dict):
            return v[0] if isinstance(v, list) else v
    # Nested: output.{media_url, image_url, video_url, url, media}
    out = data.get("output")
    if isinstance(out, dict):
        for k in ("media_url", "image_url", "video_url", "audio_url",
                  "url", "media"):
            mu = out.get(k)
            if isinstance(mu, list) and mu:
                return mu[0]
            if isinstance(mu, str) and mu:
                return mu
    # images[0] / videos[0] / audios[0]
    for listkey in ("images", "videos", "audios", "media"):
        items = data.get(listkey) or []
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                for k in ("url", "image_url", "video_url", "audio_url"):
                    v = first.get(k)
                    if v:
                        return v
            if isinstance(first, str):
                return first
    return ""


def _error_in_response(data: Any) -> str:
    """Return a failure message if a Pixazo response body signals an error.

    The synchronous generate POST can come back 200 with a failed/error
    status (or an `error` field) instead of a media URL. In webhook mode
    we must surface that immediately rather than block waiting for a
    callback the provider will never send. Returns an empty string when
    the body carries no error signal.
    """
    if not isinstance(data, dict):
        return ""
    status = str(data.get("status") or data.get("state") or "").lower()
    if status in ("failed", "error", "cancelled", "canceled"):
        return (data.get("message") or data.get("error")
                or json.dumps(data, default=str)[:300])
    err = data.get("error")
    if err:
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err, default=str)[:300]
        return str(err)[:300]
    return ""
