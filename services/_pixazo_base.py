"""Shared Pixazo dispatcher — generic HTTP / poll / download logic.

`_PixazoBaseService` is the engine every `PixazoXxxService` (image,
video, audio, 3D, upscale, try-on, lipsync, trainer) extends. It
reads `data/repository/configs/pixazo_catalog.json` and drives the
three supported conventions end-to-end:

  - "sync"          — POST returns the media URL directly in the body.
  - "legacy_poll"   — POST returns request_id; status is fetched by
                      POSTing {request_id} to the model's poll_endpoint.
  - "polling_url"   — POST returns an absolute polling_url; status is
                      fetched by GETing that URL. Completion payload
                      surfaces the URL at the op-configurable
                      `output_path` (default: output.media_url[0]).

The base service is category-agnostic: subclasses just declare
`CATEGORY` to filter which catalog models they expose and pick a
public method name (generate / generate_video / generate_audio…).

All model-specific behaviour lives in the catalog JSON, not here —
adding a provider never requires Python code if the provider's API
matches one of the three conventions.
"""

import http.client
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core import ServiceError
from core.base_service import BaseService

logger = logging.getLogger(__name__)


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


class _PixazoBaseService(BaseService):
    """Generic Pixazo catalog dispatcher. Subclass per category."""

    # Override in subclass: "image", "video", "audio", "3d", "upscale",
    # "try_on", "lipsync", "trainer". Drives model option filtering.
    CATEGORY: str = "image"

    # Standard parameters shared by every Pixazo service. Subclasses can
    # extend via `_extra_parameter_schema()`.
    def get_parameter_schema(self) -> dict:
        try:
            options = models_for_category(self.CATEGORY)
        except Exception:
            options = []
        base = {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "select", "required": False,
                "default": options[0] if options else "",
                "options": options,
                "description": (
                    f"Pixazo {self.CATEGORY} model id "
                    f"(see pixazo_catalog.json, category={self.CATEGORY})."
                ),
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 5,
                "description": "Polling interval in seconds (for async models).",
            },
            "max_retries": {
                "type": "integer", "required": False, "default": 5,
                "description": "Max retries on 5xx errors (cold start).",
            },
            "cf_clearance": {
                "type": "string", "required": False, "default": "",
                "sensitive": True,
                "description": (
                    "Cloudflare clearance cookie (cf_clearance value). "
                    "Required when Pixazo's polling endpoint blocks "
                    "your IP — extract from a browser session "
                    "(F12 → Application → Cookies → gateway.pixazo.ai) "
                    "and paste here. Bound to the User-Agent below + "
                    "your IP, ~30 min TTL. Leave empty to skip — POSTs "
                    "still work, only polling fails on flagged IPs."
                ),
            },
            "cf_user_agent": {
                "type": "string", "required": False, "default": "",
                "description": (
                    "Exact browser User-Agent the cf_clearance cookie "
                    "was minted for (copy from the same browser request "
                    "headers). Mandatory when cf_clearance is set."
                ),
            },
        }
        base.update(self._extra_parameter_schema())
        return base

    def _extra_parameter_schema(self) -> dict:
        return {}

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.max_retries = int(self.config.get("max_retries", 5))
        self._model_id = self.config.get("model", "")
        # Cloudflare bypass — opt-in. When set, sent on every request
        # so the polling endpoint stops returning 'Just a moment...'.
        # cf_user_agent is the exact UA the cookie was minted for —
        # cf_clearance is HMAC'd to it on Cloudflare's side.
        self._cf_cookie = (self.config.get("cf_clearance", "") or "").strip()
        self._cf_ua = (self.config.get("cf_user_agent", "") or "").strip()

    # ── Catalog accessors ─────────────────────────────────────────────

    def _model(self, model_id: str = "") -> Dict[str, Any]:
        """Resolve a model entry. Per-call `model_id` overrides config default."""
        mid = (model_id or self._model_id or "").strip()
        catalog = _load_catalog()
        if mid not in catalog:
            raise ServiceError(
                f"Unknown Pixazo model '{mid}'. Known in "
                f"category={self.CATEGORY}: {models_for_category(self.CATEGORY)}")
        m = catalog[mid]
        _cat = m.get("category", "image")
        if _cat != self.CATEGORY:
            raise ServiceError(
                f"Model '{mid}' is category '{_cat}', not "
                f"'{self.CATEGORY}'. Use the matching Pixazo service.")
        return m

    def _op(self, op_name: str, model_id: str = "") -> Dict[str, Any]:
        m = self._model(model_id)
        ops = m.get("operations") or {}
        if op_name not in ops:
            raise ServiceError(
                f"Model '{model_id or self._model_id}' does not support operation "
                f"'{op_name}'. Supported: {sorted(ops.keys())}.")
        return ops[op_name]

    def get_model_info(self) -> dict:
        """Surface model + operations metadata for tool discovery."""
        try:
            catalog = _load_catalog()
        except Exception as e:
            return {"error": str(e)}
        m = catalog.get(self._model_id, {})
        ops = m.get("operations") or {}
        return {
            "model": self._model_id,
            "label": m.get("label", self._model_id),
            "category": m.get("category", self.CATEGORY),
            "operations": {
                name: {
                    "convention": op.get("convention", ""),
                    "params": op.get("params", {}),
                    "input_field": op.get("input_field", ""),
                    "output_path": op.get("output_path", ""),
                }
                for name, op in ops.items()
            },
            "all_models": {
                k: v.get("label", k)
                for k, v in catalog.items()
                if v.get("category", "image") == self.CATEGORY
            },
        }

    # ── Connection ─────────────────────────────────────────────────────

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo service")
        self._model()  # fail fast on misconfigured model
        return {"ready": True}

    def _close_connection(self):
        pass

    # ── HTTP primitives ────────────────────────────────────────────────

    def _make_headers(self, body_bytes: bytes,
                      *, multipart_boundary: str = "") -> Dict[str, str]:
        if multipart_boundary:
            ctype = f"multipart/form-data; boundary={multipart_boundary}"
        else:
            ctype = "application/json"
        h = {
            "Content-Type": ctype,
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(body_bytes)),
            "User-Agent": self._cf_ua or _BROWSER_UA,
        }
        if self._cf_cookie:
            h["Cookie"] = f"cf_clearance={self._cf_cookie}"
        return h

    @staticmethod
    def _encode_multipart(fields: Dict[str, Any]) -> Tuple[bytes, str]:
        """Build a tiny multipart/form-data body.

        Only string fields are supported — sufficient for Pixazo's
        describe/edit/remix endpoints where the payload is a handful
        of short text fields plus an image URL. For file uploads,
        callers should use the image_urls convention instead.
        """
        import uuid as _uuid
        boundary = f"pawflowPixazoBoundary{_uuid.uuid4().hex}"
        lines = []
        for name, value in fields.items():
            lines.append(f"--{boundary}".encode())
            lines.append(
                f'Content-Disposition: form-data; name="{name}"'.encode())
            lines.append(b"")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            lines.append(str(value).encode("utf-8"))
        lines.append(f"--{boundary}--".encode())
        lines.append(b"")
        return b"\r\n".join(lines), boundary

    def _post(self, endpoint: str, body: Dict[str, Any],
              *, multipart: bool = False) -> Dict[str, Any]:
        """POST to Pixazo gateway with retry on 5xx."""
        if multipart:
            body_bytes, boundary = self._encode_multipart(body)
            headers = self._make_headers(body_bytes, multipart_boundary=boundary)
        else:
            body_bytes = json.dumps(body).encode("utf-8")
            headers = self._make_headers(body_bytes)
        ctx = ssl.create_default_context()
        resp_body = ""
        resp_status = 0
        for attempt in range(self.max_retries):
            conn = http.client.HTTPSConnection(
                _GATEWAY, timeout=self.timeout, context=ctx)
            conn.request("POST", endpoint, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8", errors="replace")
            resp_status = resp.status
            conn.close()
            if resp_status < 500:
                break
            delay = [3, 5, 8, 10][min(attempt, 3)]
            logger.warning("[PIXAZO] Attempt %d/%d got %d: %s, retrying in %ds...",
                           attempt + 1, self.max_retries, resp_status,
                           resp_body[:200], delay)
            time.sleep(delay)
        if resp_status >= 400:
            raise ServiceError(f"Pixazo API error ({resp_status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def _get_url(self, url: str) -> Dict[str, Any]:
        """GET an absolute Pixazo URL (used for `polling_url` follow-up).

        The URL is whatever the generate response put in `polling_url`
        — we never construct it ourselves. Verified at info-log level
        for debugging when Cloudflare blocks the poll.

        On flagged IPs Pixazo's /v2/requests/status/ sits behind a
        managed challenge. Setting `cf_clearance` + `cf_user_agent`
        on the service config makes us look like the user's browser
        and gets through — see service config description for how to
        extract the cookie.
        """
        logger.info("[PIXAZO] GET %s", url)
        headers = {
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": self._cf_ua or _BROWSER_UA,
        }
        if self._cf_cookie:
            headers["Cookie"] = f"cf_clearance={self._cf_cookie}"
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 403 and "Just a moment" in body:
                raise ServiceError(
                    f"Pixazo poll blocked by Cloudflare ({e.code}). "
                    f"URL: {url}. This is a Pixazo misconfiguration — "
                    f"/v2/requests/status/ sits behind a managed "
                    f"challenge that blocks non-browser clients even "
                    f"with a valid API key. Workaround: use the "
                    f"webhook delivery (X-Webhook-URL header on "
                    f"generate) instead of polling, or retry from a "
                    f"different network.")
            raise ServiceError(f"Pixazo poll error ({e.code}): {body[:300]}")
        return json.loads(body) if body.strip() else {}

    def _download_media(self, url: str,
                        *, default_mime: str = "application/octet-stream"
                        ) -> Tuple[bytes, str]:
        """Fetch bytes from a public CDN URL — no Pixazo auth needed."""
        req = urllib.request.Request(
            url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read(), r.headers.get("Content-Type", default_mime)

    # ── Polling ────────────────────────────────────────────────────────

    def _poll(self, op: Dict[str, Any], request_id: str,
              *, polling_url: str = "") -> str:
        """Drive polling per convention until completion; return media URL.

        No timeout — waits forever. Cancellation is via the agent loop's
        interrupt path (raises AgentCancelled), per the project rule
        "no arbitrary timeouts".
        """
        output_path = op.get("output_path", "")
        url_field = op.get("url_field", "")
        id_field = op.get("id_field", "request_id")
        poll_endpoint = op.get("poll_endpoint", "")
        prediction_endpoint = op.get("prediction_endpoint", "")
        use_url = bool(polling_url)
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            if use_url:
                data = self._get_url(polling_url)
            elif prediction_endpoint:
                # Some models expose status on a separate endpoint with
                # a different id field (Runway, FireRed, Sora).
                data = self._post(prediction_endpoint, {
                    id_field: request_id,
                    "request_id": request_id,
                    "requestId": request_id,
                    "prediction_id": request_id,
                })
            else:
                data = self._post(poll_endpoint, {
                    id_field: request_id,
                    "request_id": request_id,
                    "requestId": request_id,
                })
            status = (data.get("status", "") or "").lower()
            elapsed = int(time.time() - start)
            logger.info("[PIXAZO] Poll %s (%ds): status=%s",
                        polling_url or poll_endpoint, elapsed, status)
            if status in ("completed", "done", "success", "ready"):
                u = _extract_media_url(
                    data, output_path=output_path, url_field=url_field)
                if u:
                    return u
                raise ServiceError(
                    f"Pixazo completed but no media URL: {json.dumps(data)[:300]}")
            if status in ("failed", "error"):
                msg = data.get("message", "") or data.get("error", "") or str(data)[:200]
                raise ServiceError(f"Pixazo generation failed: {msg}")
            if not status:
                # Some models omit status when ready
                u = _extract_media_url(
                    data, output_path=output_path, url_field=url_field)
                if u:
                    return u

    # ── Generic operation dispatch ─────────────────────────────────────

    def _invoke(self, op_name: str, body: Dict[str, Any],
                *, model_id: str = "") -> Dict[str, Any]:
        """Run one operation end-to-end: POST → (sync | poll) → download.

        `model_id` overrides the service's default model for this call —
        lets a single PixazoXxxService dispatch every catalog model in
        its category without spinning up one service per model.

        Returns {bytes, content_type, source_url} — the category-level
        public API (generate, generate_video, …) aliases `bytes` to
        `image_bytes` / `video_bytes` / `audio_bytes` for caller clarity.
        """
        self.ensure_connected()
        op = self._op(op_name, model_id=model_id)
        endpoint = op.get("endpoint", "")
        if not endpoint:
            raise ServiceError(f"Operation '{op_name}' has no endpoint configured")
        convention = op.get("convention", "sync")
        output_path = op.get("output_path", "")
        url_field = op.get("url_field", "")
        id_field = op.get("id_field", "request_id")
        status_url_field = op.get("status_url_field", "polling_url")
        multipart = bool(op.get("multipart_form_data", False))

        logger.info("[PIXAZO] %s/%s (%s) → POST %s",
                    model_id or self._model_id, op_name, convention, endpoint)
        # Stay call-compatible with patched _post(endpoint, body) in tests:
        # only pass multipart kwarg when actually needed.
        if multipart:
            data = self._post(endpoint, body, multipart=True)
        else:
            data = self._post(endpoint, body)
        logger.info("[PIXAZO] Response: %s", json.dumps(data, default=str)[:300])

        if convention == "sync":
            url = _extract_media_url(
                data, output_path=output_path, url_field=url_field)
            if not url:
                raise ServiceError(
                    f"No media URL in sync response: {json.dumps(data)[:300]}")
        else:
            request_id = ""
            for f in (id_field, "request_id", "requestId", "id",
                      "taskId", "task_id", "video_id", "prediction_id"):
                v = data.get(f, "")
                if v:
                    request_id = v
                    break
            if not request_id:
                # Some endpoints return the URL inline even on async.
                url = _extract_media_url(
                    data, output_path=output_path, url_field=url_field)
                if not url:
                    raise ServiceError(
                        f"No request_id and no URL in response: "
                        f"{json.dumps(data)[:300]}")
            else:
                polling_url = ""
                if convention == "polling_url":
                    polling_url = data.get(status_url_field, "") or ""
                url = self._poll(op, request_id, polling_url=polling_url)

        default_mime = {
            "video": "video/mp4",
            "audio": "audio/mpeg",
            "3d": "model/gltf-binary",
        }.get(self.CATEGORY, "image/png")
        # Image subclass exposes a category-named convenience
        # `_download_image(url) → (bytes, mime)`; use it when defined so
        # a subclass can intercept the download (e.g. to apply a custom
        # User-Agent). Falls through to _download_media otherwise.
        _dl = getattr(self, "_download_image", None)
        if callable(_dl) and self.CATEGORY == "image":
            content_bytes, content_type = _dl(url)
        else:
            content_bytes, content_type = self._download_media(
                url, default_mime=default_mime)
        return {"bytes": content_bytes, "content_type": content_type,
                "source_url": url}
