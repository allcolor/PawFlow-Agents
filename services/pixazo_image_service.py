"""Pixazo image generation service — generic dispatcher over the Pixazo catalog.

Models and their operations (text_to_image, edit_image, ...) are declared
in `data/repository/configs/pixazo_catalog.json`. This module contains
ZERO model-specific code: every call resolves to (model, operation,
convention) and the convention drives the request/poll behavior.

Conventions:
  - "sync"          — POST returns the image URL directly in the body.
  - "legacy_poll"   — POST returns request_id; status is fetched by
                      POSTing {request_id} to the model's poll_endpoint.
  - "polling_url"   — POST returns an absolute polling_url; status is
                      fetched by GETing that URL. Completion payload
                      surfaces the URL under output.media_url[0].
"""

import http.client
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core import ServiceFactory, ServiceError
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)


# ── Catalog loading ─────────────────────────────────────────────────────


def _catalog_path() -> str:
    """Locate the Pixazo catalog JSON in the repository."""
    import core.paths as _p
    return str(_p.REPOSITORY_DIR / "configs" / "pixazo_catalog.json")


_CATALOG_CACHE: Optional[Dict[str, Any]] = None


def _load_catalog() -> Dict[str, Any]:
    """Read pixazo_catalog.json, cache for the process lifetime.

    The file is small (<10KB) and changes only on deployment, so a
    process-level cache is fine. Reset by clearing _CATALOG_CACHE.
    """
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


def _model_options() -> list:
    """Sorted list of known model ids — used to populate the select schema."""
    return sorted(_load_catalog().keys())


# ── Service ─────────────────────────────────────────────────────────────


_GATEWAY = "gateway.pixazo.ai"


class PixazoImageService(BaseImageGenerationService):
    TYPE = "pixazoImageGeneration"
    VERSION = "3.0.0"
    NAME = "Pixazo Image Generation"
    DESCRIPTION = ("Generate or edit images via Pixazo API. Supports any "
                   "model declared in pixazo_catalog.json with any operation "
                   "(text_to_image, edit_image, ...).")

    def get_parameter_schema(self) -> dict:
        try:
            options = _model_options()
        except Exception:
            options = []
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "select", "required": False, "default": "sdxl",
                "options": options,
                "description": "Pixazo model id (see pixazo_catalog.json).",
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 5,
                "description": "Polling interval in seconds (for async models).",
            },
            "max_retries": {
                "type": "integer", "required": False, "default": 5,
                "description": "Max retries on 5xx errors (cold start).",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.max_retries = int(self.config.get("max_retries", 5))
        self._model_id = self.config.get("model", "sdxl")

    # ── Catalog accessors ──────────────────────────────────────────────

    def _model(self) -> Dict[str, Any]:
        catalog = _load_catalog()
        if self._model_id not in catalog:
            raise ServiceError(
                f"Unknown Pixazo model '{self._model_id}'. Known: "
                f"{sorted(catalog.keys())}")
        return catalog[self._model_id]

    def _op(self, op_name: str) -> Dict[str, Any]:
        m = self._model()
        ops = m.get("operations") or {}
        if op_name not in ops:
            raise ServiceError(
                f"Model '{self._model_id}' does not support operation "
                f"'{op_name}'. Supported: {sorted(ops.keys())}.")
        return ops[op_name]

    def get_model_info(self) -> dict:
        """Surface model + operations metadata (used by `get_image_model_info` tool)."""
        try:
            catalog = _load_catalog()
        except Exception as e:
            return {"error": str(e)}
        m = catalog.get(self._model_id, {})
        ops = m.get("operations") or {}
        return {
            "model": self._model_id,
            "label": m.get("label", self._model_id),
            "category": m.get("category", "image"),
            "operations": {
                name: {
                    "convention": op.get("convention", ""),
                    "params": op.get("params", {}),
                    "input_field": op.get("input_field", ""),
                }
                for name, op in ops.items()
            },
            "all_models": {k: v.get("label", k) for k, v in catalog.items()},
        }

    # ── Connection (lazy — Pixazo is a stateless HTTPS API) ─────────────

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo service")
        # Validate the model/operation declaration upfront so config errors
        # surface at service start rather than at first call.
        self._model()
        return {"ready": True}

    def _close_connection(self):
        pass

    # ── HTTP primitives ────────────────────────────────────────────────

    def _make_headers(self, body_bytes: bytes) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(body_bytes)),
        }

    def _post(self, endpoint: str, body_dict: Dict[str, Any]) -> Dict[str, Any]:
        """POST to Pixazo gateway with retry on 5xx."""
        json_body = json.dumps(body_dict).encode("utf-8")
        headers = self._make_headers(json_body)
        ctx = ssl.create_default_context()
        resp_body = ""
        for attempt in range(self.max_retries):
            conn = http.client.HTTPSConnection(
                _GATEWAY, timeout=self.timeout, context=ctx)
            conn.request("POST", endpoint, body=json_body, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8", errors="replace")
            conn.close()
            if resp.status < 500:
                break
            delay = [3, 5, 8, 10][min(attempt, 3)]
            logger.warning("[PIXAZO] Attempt %d/%d got %d: %s, retrying in %ds...",
                           attempt + 1, self.max_retries, resp.status,
                           resp_body[:200], delay)
            time.sleep(delay)
        if resp.status >= 400:
            raise ServiceError(f"Pixazo API error ({resp.status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def _get_url(self, url: str) -> Dict[str, Any]:
        """GET an absolute Pixazo URL (used for `polling_url` follow-up)."""
        req = urllib.request.Request(url, method="GET", headers={
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ServiceError(f"Pixazo poll error ({e.code}): {body[:300]}")
        return json.loads(body) if body.strip() else {}

    def _download_image(self, url: str) -> Tuple[bytes, str]:
        """Fetch image bytes from a public CDN URL — no Pixazo auth needed."""
        req = urllib.request.Request(
            url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(), r.headers.get("Content-Type", "image/png")

    # ── URL extraction ─────────────────────────────────────────────────

    @staticmethod
    def _extract_image_url(data: Any, url_field: str = "") -> str:
        """Find an image URL inside a Pixazo response, trying every known shape.

        Tries (in order): the operation's configured `url_field`,
        common top-level fields, nested `output.media_url[0]` (new
        polling_url convention), and `images[0].(url|image_url)`.
        """
        if not isinstance(data, dict):
            return ""
        if url_field:
            v = data.get(url_field, "")
            if v:
                return v[0] if isinstance(v, list) else v
        for field in ("imageUrl", "output", "image_url", "url", "image"):
            v = data.get(field, "")
            if v and not isinstance(v, dict):
                return v[0] if isinstance(v, list) else v
        # Nested: output.media_url[0] (nano-banana-pro / nano-banana-2)
        out = data.get("output")
        if isinstance(out, dict):
            mu = out.get("media_url") or out.get("image_url") or out.get("url")
            if isinstance(mu, list) and mu:
                return mu[0]
            if isinstance(mu, str) and mu:
                return mu
        # images[0]
        images = data.get("images") or []
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                return first.get("url", "") or first.get("image_url", "")
            if isinstance(first, str):
                return first
        return ""

    # ── Polling ────────────────────────────────────────────────────────

    def _poll(self, op: Dict[str, Any], request_id: str,
              polling_url: str = "") -> str:
        """Drive polling per the operation's convention until completion.

        No timeout — waits forever. Cancellation is via the agent loop's
        interrupt path (raises AgentCancelled), per the project rule
        "no arbitrary timeouts".
        """
        url_field = op.get("url_field", "")
        id_field = op.get("id_field", "request_id")
        poll_endpoint = op.get("poll_endpoint", "")
        use_url = bool(polling_url)
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            if use_url:
                data = self._get_url(polling_url)
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
                u = self._extract_image_url(data, url_field=url_field)
                if u:
                    return u
                raise ServiceError(
                    f"Pixazo completed but no image URL: {json.dumps(data)[:300]}")
            if status in ("failed", "error"):
                msg = data.get("message", "") or data.get("error", "") or str(data)[:200]
                raise ServiceError(f"Pixazo generation failed: {msg}")
            # Some models omit status when ready
            if not status:
                u = self._extract_image_url(data, url_field=url_field)
                if u:
                    return u

    # ── Generic operation dispatch ─────────────────────────────────────

    def _invoke(self, op_name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Run one operation end-to-end: POST → (sync return | poll) → download."""
        self.ensure_connected()
        op = self._op(op_name)
        endpoint = op.get("endpoint", "")
        if not endpoint:
            raise ServiceError(f"Operation '{op_name}' has no endpoint configured")
        convention = op.get("convention", "sync")
        url_field = op.get("url_field", "")
        id_field = op.get("id_field", "request_id")

        logger.info("[PIXAZO] %s/%s (%s) → POST %s",
                    self._model_id, op_name, convention, endpoint)
        data = self._post(endpoint, body)
        logger.info("[PIXAZO] Response: %s", json.dumps(data)[:300])

        if convention == "sync":
            url = self._extract_image_url(data, url_field=url_field)
            if not url:
                raise ServiceError(
                    f"No image URL in sync response: {json.dumps(data)[:300]}")
        else:
            # Resolve request_id from any known field name.
            request_id = ""
            for f in (id_field, "request_id", "requestId", "id", "taskId"):
                v = data.get(f, "")
                if v:
                    request_id = v
                    break
            if not request_id:
                # Some endpoints return the URL inline even on async — handle it.
                url = self._extract_image_url(data, url_field=url_field)
                if not url:
                    raise ServiceError(
                        f"No request_id and no URL in response: "
                        f"{json.dumps(data)[:300]}")
            else:
                polling_url = data.get("polling_url", "") if convention == "polling_url" else ""
                url = self._poll(op, request_id, polling_url=polling_url)

        image_bytes, content_type = self._download_image(url)
        return {"image_bytes": image_bytes, "content_type": content_type,
                "source_url": url}

    # ── Public ops ─────────────────────────────────────────────────────

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 width: int = 1024, height: int = 1024, steps: int = 20,
                 **kwargs) -> dict:
        """Text-to-image — calls operation 'text_to_image' on the active model."""
        if not prompt:
            raise ServiceError("No prompt provided")
        body: Dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if width and height:
            body["width"] = max(256, int(width))
            body["height"] = max(256, int(height))
            body["size"] = f"{body['width']}x{body['height']}"
        if steps:
            body["num_steps"] = max(1, min(50, int(steps)))
            body["num_inference_steps"] = body["num_steps"]
        body["guidance_scale"] = kwargs.get("guidance_scale", 5)
        body["seed"] = kwargs.get("seed", int(time.time()) % 1_000_000)
        body["num_images"] = 1
        body["output_format"] = kwargs.get("output_format", "png")
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        return self._invoke("text_to_image", body)

    def edit_image(self, prompt: str = "", image_urls=None, **kwargs) -> dict:
        """Edit one or more source images per the prompt.

        Calls operation 'edit_image' on the active model. The op's
        `input_field` declares where the source URLs go in the body
        (defaults to 'image_urls').
        """
        if not prompt:
            raise ServiceError("No prompt provided")
        if not image_urls:
            raise ServiceError("edit_image requires at least one source URL "
                               "in `image_urls`.")
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        op = self._op("edit_image")
        input_field = op.get("input_field", "image_urls")
        body: Dict[str, Any] = {
            "prompt": prompt,
            input_field: list(image_urls),
            "num_images": int(kwargs.get("num_images", 1)),
            "output_format": kwargs.get("output_format", "png"),
        }
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        return self._invoke("edit_image", body)


ServiceFactory.register(PixazoImageService)
