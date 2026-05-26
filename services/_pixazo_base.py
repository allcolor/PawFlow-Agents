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

    @staticmethod
    def _short_url(url: str) -> str:
        """Trim a polling URL to its tail for compact log lines."""
        return url.rsplit("/", 1)[-1][:32] if url else url

    @staticmethod
    def _reshape_body(body: Dict[str, Any], shape: str) -> Dict[str, Any]:
        """Rewrite the body to match a provider's expected shape.

        Supported shapes:

        - ``flat`` (default): pass through unchanged. Suits Veo, Runway,
          Kling, most image gens.
        - ``content_array``: collapse `prompt` / `image_url` /
          `video_url` / `audio_url` into an OpenAI-style multimodal
          ``content`` array. Suits Seedance and any provider that
          inherited the ByteDance multimodal request schema.
        """
        if shape == "flat" or not isinstance(body, dict):
            return body
        if shape == "content_array":
            content = []
            for k in ("prompt", "text", "negative_prompt"):
                v = body.get(k)
                if v:
                    content.append({"type": "text", "text": str(v)})
            for k in ("image_url", "image", "input_image_url"):
                v = body.get(k)
                if v:
                    urls = v if isinstance(v, list) else [v]
                    for u in urls:
                        content.append({"type": "image_url",
                                         "image_url": {"url": u}})
            for k in ("video_url",):
                v = body.get(k)
                if v:
                    urls = v if isinstance(v, list) else [v]
                    for u in urls:
                        content.append({"type": "video_url",
                                         "video_url": {"url": u}})
            for k in ("audio_url",):
                v = body.get(k)
                if v:
                    urls = v if isinstance(v, list) else [v]
                    for u in urls:
                        content.append({"type": "audio_url",
                                         "audio_url": {"url": u}})
            if not content:
                # Nothing to wrap — leave body alone, the provider will
                # error clearly.
                return body
            # Drop the keys we just rewrote, keep all the model-specific
            # extras (duration, ratio, resolution, generate_audio, …).
            _consumed = {
                "prompt", "text", "negative_prompt",
                "image_url", "image", "input_image_url",
                "video_url", "audio_url",
            }
            rest = {k: v for k, v in body.items() if k not in _consumed}
            return {"content": content, **rest}
        # Unknown shape — log and pass through.
        logger.warning("[PIXAZO] unknown body_shape=%r — passing flat", shape)
        return body

    def _try_get_via_relay(self, url: str,
                            headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Route a GET through the first connected relay (Linux TCP
        stack → JA3 different from Windows Python → bypasses the
        Cloudflare managed challenge that the polling endpoint serves).

        Returns the parsed JSON dict on success, None when no relay is
        available so the caller falls back to direct urllib.

        Pixazo service has no user_id of its own — walk every live
        instance across every scope (global, user, conv) so a relay
        connected on any scope is usable.
        """
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            relay = None
            # Walk every live instance across every scope. We don't
            # know which user owns this Pixazo call, so any connected
            # relay does the job — they all share the same Docker NAT
            # egress that defeats the CF block.
            with reg._data_lock:
                instances = {sid: dict(svcs)
                             for sid, svcs in reg._live_instances.items()}
            for sid, svcs in instances.items():
                for service_id, candidate in svcs.items():
                    if getattr(candidate, "TYPE", "") != "relay":
                        continue
                    # _relay_pool is the FilesystemService's connected
                    # WS list — non-empty means the relay is online.
                    if getattr(candidate, "_relay_pool", []):
                        relay = candidate
                        break
                if relay:
                    break
            if not relay:
                logger.debug("[PIXAZO] no live relay across %d scope(s) — urllib",
                             len(instances))
                return None
        except Exception as e:
            logger.debug("[PIXAZO] relay lookup skipped: %s", e)
            return None

        try:
            logger.debug("[PIXAZO] GET via relay '%s' %s",
                          getattr(relay, "_service_id", "?"),
                          self._short_url(url))
            r = relay.http_fetch(url, method="GET", headers=headers,
                                  timeout=self.timeout)
        except Exception as e:
            logger.warning("[PIXAZO] relay GET failed (%s) — falling back to direct urllib", e)
            return None
        status = r.get("status", 0)
        body_bytes = r.get("body_bytes") or b""
        # Server may return gzip / br even when we didn't ask — decode
        # if Content-Encoding indicates compression.
        _enc = (r.get("headers") or {}).get("Content-Encoding", "").lower()
        if "gzip" in _enc:
            try:
                import gzip
                body_bytes = gzip.decompress(body_bytes)
            except Exception as ge:
                logger.warning("[PIXAZO] gzip decode failed: %s", ge)
        elif "br" in _enc:
            try:
                import brotli  # type: ignore
                body_bytes = brotli.decompress(body_bytes)
            except Exception as be:
                logger.warning("[PIXAZO] br decode failed: %s", be)
        elif "deflate" in _enc:
            try:
                import zlib
                body_bytes = zlib.decompress(body_bytes)
            except Exception as de:
                logger.warning("[PIXAZO] deflate decode failed: %s", de)
        body = body_bytes.decode("utf-8", errors="replace")
        logger.debug("[PIXAZO] relay GET %d enc=%r body[:120]=%r",
                      status, _enc, body[:120])
        if status >= 400:
            if "Just a moment" in body or "Un instant" in body:
                logger.warning("[PIXAZO] relay also got CF challenge — falling back")
                return None
            raise ServiceError(f"Pixazo poll error ({status}): {body[:300]}")
        return json.loads(body) if body.strip() else {}

    def _get_url(self, url: str) -> Dict[str, Any]:
        """GET an absolute Pixazo URL (used for `polling_url` follow-up).

        The URL is whatever the generate response put in `polling_url`
        — we never construct it ourselves.

        Pixazo's /v2/requests/status/ endpoint is behind a Cloudflare
        managed challenge that flags Python's TLS fingerprint on
        Windows native (verified empirically: same machine, WSL/Docker
        passes, Windows Python doesn't). When a relay service is
        connected we route the GET through it — the relay's Linux
        TCP stack produces a different JA3 that CF doesn't flag. The
        IP is the user's regardless; only the client fingerprint
        differs.
        """
        logger.debug("[PIXAZO] GET %s", self._short_url(url))
        headers = {
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": self._cf_ua or _BROWSER_UA,
        }
        if self._cf_cookie:
            headers["Cookie"] = f"cf_clearance={self._cf_cookie}"
        # Try via relay first if one is connected — defeats CF on
        # flagged Windows Python without any extra dependency.
        relay_result = self._try_get_via_relay(url, headers)
        if relay_result is not None:
            return relay_result
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured Pixazo API endpoint.
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
        with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310 - provider-returned media download URL.
            return r.read(), r.headers.get("Content-Type", default_mime)

    # ── Polling ────────────────────────────────────────────────────────

    def _poll(self, op: Dict[str, Any], request_id: str,
              *, polling_url: str = "") -> str:
        """Drive polling per convention until completion; return media URL.

        No timeout — waits forever. Cancellation comes from the active
        tool's cancel_event (set by tool_relay_service when the user
        clicks Kill); checked between polls so we don't keep hammering
        Pixazo after the user already stopped the tool.
        """
        output_path = op.get("output_path", "")
        url_field = op.get("url_field", "")
        id_field = op.get("id_field", "request_id")
        poll_endpoint = op.get("poll_endpoint", "")
        prediction_endpoint = op.get("prediction_endpoint", "")
        use_url = bool(polling_url)
        start = time.time()
        # Pulled lazily so direct service tests (which run outside a
        # tool dispatch) don't need to fake the thread-local.
        try:
            from services.tool_relay_service import current_cancel_event
            _cancel = current_cancel_event()
        except Exception:
            _cancel = None
        while True:
            if _cancel is not None and _cancel.is_set():
                raise ServiceError(
                    "Pixazo polling cancelled by user")
            time.sleep(self.poll_interval)
            if use_url:
                data = self._get_url(polling_url)
            elif prediction_endpoint:
                # Some models expose status on a separate endpoint with
                # a different id field (Runway, FireRed).
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
            # Polling is chatty (1 line every poll_interval s for the
            # whole job duration). Demoted to debug — INFO only fires
            # on terminal states and on the very first iteration so
            # the operator still sees that polling is alive without
            # 50+ lines per generation.
            (logger.info if elapsed <= self.poll_interval else logger.debug)(
                "[PIXAZO] Poll %s (%ds): status=%s",
                self._short_url(polling_url or poll_endpoint),
                elapsed, status)
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

        logger.info("[PIXAZO] %s/%s → %s",
                    model_id or self._model_id, op_name,
                    self._short_url(endpoint))
        # Some providers don't take the canonical {prompt, image_url, ...}
        # flat body. Per-op `body_shape` rewrites the body before POST
        # without changing caller signatures.
        body = self._reshape_body(body, op.get("body_shape", "flat"))
        # Stay call-compatible with patched _post(endpoint, body) in tests:
        # only pass multipart kwarg when actually needed.
        if multipart:
            data = self._post(endpoint, body, multipart=True)
        else:
            data = self._post(endpoint, body)
        logger.debug("[PIXAZO] Response: %s", json.dumps(data, default=str)[:300])

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
