"""Shared WaveSpeedAI dispatcher for catalog-driven media services.

WaveSpeed exposes a unified prediction API:

  - POST https://api.wavespeed.ai/api/v3/<model-endpoint>
  - GET  data.urls.get or /api/v3/predictions/{id}/result
  - read generated media URLs from data.outputs[]

Subclasses only declare CATEGORY and public methods. Model-specific
endpoints and parameters live in
``data/repository/configs/wavespeed_catalog.json``.
"""

import http.client
import json
import logging
import os
import ssl
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core import ServiceError
from core.base_service import BaseService


logger = logging.getLogger(__name__)

_API_HOST = "api.wavespeed.ai"
_API_PREFIX = "/api/v3"
_CATALOG_CACHE: Optional[Dict[str, Any]] = None


def _catalog_path() -> str:
    import core.paths as _p
    return str(_p.REPOSITORY_DIR / "configs" / "wavespeed_catalog.json")


def _load_catalog() -> Dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    path = _catalog_path()
    if not os.path.exists(path):
        raise ServiceError(f"WaveSpeed catalog not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _CATALOG_CACHE = data.get("models", data)
    return _CATALOG_CACHE


def models_for_category(category: str) -> List[str]:
    try:
        catalog = _load_catalog()
    except Exception:
        return []
    return sorted(k for k, v in catalog.items()
                  if v.get("category", "image") == category)


def _get_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def _extract_output_url(data: Dict[str, Any], output_path: str = "") -> str:
    if output_path:
        value = _get_path(data, output_path)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first
    for path in ("data.outputs.0", "outputs.0", "data.output", "output",
                 "data.url", "url", "data.audio", "audio", "data.video",
                 "video", "data.image", "image"):
        value = _get_path(data, path)
        if isinstance(value, str) and value:
            return value
    return ""


class _WaveSpeedBaseService(BaseService):
    """Base service for WaveSpeed catalog-backed providers."""

    CATEGORY = "image"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "WaveSpeedAI API key.",
            },
            "model": {
                "type": "string", "required": True,
                "default": self._default_model_for_category(),
                "description": (
                    f"WaveSpeed model id (see wavespeed_catalog.json, "
                    f"category={self.CATEGORY})."
                ),
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 3,
                "description": "Seconds between prediction status checks.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
            "max_retries": {
                "type": "integer", "required": False, "default": 3,
                "description": "Submit/poll retry count for transient 5xx responses.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self._model_id = (self.config.get("model")
                          or self._default_model_for_category())
        self.poll_interval = int(self.config.get("poll_interval", 3))
        self.timeout = int(self.config.get("timeout", 120))
        self.max_retries = int(self.config.get("max_retries", 3))

    def _default_model_for_category(self) -> str:
        defaults = {
            "image": "wavespeed-ai/flux-dev",
            "video": "wavespeed-ai/wan-2.2/t2v-480p",
            "audio": "wavespeed-ai/qwen3-tts/text-to-speech",
            "voice_clone": "wavespeed-ai/qwen3-tts/voice-clone",
            "3d": "wavespeed-ai/hunyuan3d-v3/text-to-3d",
            "upscale": "wavespeed-ai/image-upscaler",
            "try_on": "wavespeed-ai/ai-virtual-outfit-tryon",
            "lipsync": "wavespeed-ai/ltx-2.3/lipsync",
            "trainer": "wavespeed-ai/flux-dev-lora-trainer",
        }
        return defaults.get(self.CATEGORY, "")

    def _model(self, model_id: str = "") -> Dict[str, Any]:
        mid = (model_id or self._model_id or "").strip()
        catalog = _load_catalog()
        if mid not in catalog:
            raise ServiceError(
                f"Unknown WaveSpeed model '{mid}'. Known in "
                f"category={self.CATEGORY}: {models_for_category(self.CATEGORY)}")
        model = catalog[mid]
        category = model.get("category", "image")
        if category != self.CATEGORY:
            raise ServiceError(
                f"Model '{mid}' is category '{category}', not "
                f"'{self.CATEGORY}'. Use the matching WaveSpeed service.")
        return model

    def _op(self, op_name: str, model_id: str = "") -> Dict[str, Any]:
        model = self._model(model_id)
        ops = model.get("operations") or {}
        if op_name not in ops:
            raise ServiceError(
                f"Model '{model_id or self._model_id}' does not support "
                f"operation '{op_name}'. Supported: {sorted(ops.keys())}.")
        return ops[op_name]

    def _pick_op(self, candidates, *, model_id: str = "") -> str:
        ops = self._model(model_id).get("operations") or {}
        for candidate in candidates:
            if candidate in ops:
                return candidate
        raise ServiceError(
            f"Model '{model_id or self._model_id}' has none of "
            f"{list(candidates)}. Supported: {sorted(ops.keys())}.")

    def get_model_info(self) -> dict:
        try:
            catalog = _load_catalog()
        except Exception as e:
            return {"error": str(e)}
        model = catalog.get(self._model_id, {})
        ops = model.get("operations") or {}
        return {
            "model": self._model_id,
            "label": model.get("label", self._model_id),
            "category": model.get("category", self.CATEGORY),
            "operations": {
                name: {
                    "convention": op.get("convention", "prediction_poll"),
                    "params": op.get("params", {}),
                    "input_field": op.get("input_field", ""),
                    "output_path": op.get("output_path", "data.outputs.0"),
                }
                for name, op in ops.items()
            },
            "all_models": {
                k: v.get("label", k)
                for k, v in catalog.items()
                if v.get("category", "image") == self.CATEGORY
            },
        }

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for WaveSpeed service")
        self._model()
        return {"ready": True}

    def _close_connection(self):
        pass

    def _headers(self, body_bytes: bytes = b"") -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if body_bytes:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body_bytes))
        return headers

    @staticmethod
    def _api_path(endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            marker = f"{_API_HOST}{_API_PREFIX}"
            if marker in endpoint:
                return endpoint.split(marker, 1)[1]
            raise ServiceError(f"Unsupported WaveSpeed URL: {endpoint}")
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if endpoint.startswith(_API_PREFIX + "/"):
            return endpoint[len(_API_PREFIX):]
        return endpoint

    def _request_json(self, method: str, endpoint: str,
                      body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body_bytes = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = self._headers(body_bytes)
        path = _API_PREFIX + self._api_path(endpoint)
        ctx = ssl.create_default_context()
        resp_body = ""
        resp_status = 0
        for attempt in range(max(1, self.max_retries)):
            conn = http.client.HTTPSConnection(
                _API_HOST, timeout=self.timeout, context=ctx)
            try:
                conn.request(method, path, body=body_bytes or None,
                             headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read().decode("utf-8", errors="replace")
                resp_status = resp.status
            finally:
                conn.close()
            if resp_status < 500:
                break
            logger.warning("[WAVESPEED] %s %s attempt %d/%d got %d: %s",
                           method, endpoint, attempt + 1, self.max_retries,
                           resp_status, resp_body[:200])
            time.sleep([2, 4, 8][min(attempt, 2)])
        if resp_status >= 400:
            raise ServiceError(
                f"WaveSpeed API error ({resp_status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def _post(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("POST", endpoint, body)

    def _get(self, endpoint: str) -> Dict[str, Any]:
        return self._request_json("GET", endpoint, None)

    @staticmethod
    def _prediction_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        payload = data.get("data") if isinstance(data, dict) else {}
        return payload if isinstance(payload, dict) else data

    @staticmethod
    def _status(data: Dict[str, Any]) -> str:
        payload = _WaveSpeedBaseService._prediction_payload(data)
        return str(payload.get("status") or data.get("status") or "").lower()

    def _poll(self, first_response: Dict[str, Any], output_path: str = "") -> str:
        try:
            from services.tool_relay_service import current_cancel_event
            cancel_event = current_cancel_event()
        except Exception:
            cancel_event = None

        payload = self._prediction_payload(first_response)
        prediction_id = payload.get("id") or first_response.get("id")
        urls = payload.get("urls") or {}
        get_url = urls.get("get", "") if isinstance(urls, dict) else ""
        poll_endpoint = get_url or (f"/predictions/{prediction_id}/result"
                                    if prediction_id else "")
        if not poll_endpoint:
            raise ServiceError(
                f"WaveSpeed response has no prediction id: "
                f"{json.dumps(first_response)[:300]}")

        data = first_response
        start = time.time()
        while True:
            status = self._status(data)
            if status == "completed":
                url = _extract_output_url(data, output_path=output_path)
                if url:
                    return url
                raise ServiceError(
                    f"WaveSpeed completed but no output URL: "
                    f"{json.dumps(data)[:300]}")
            if status == "failed":
                payload = self._prediction_payload(data)
                msg = payload.get("error") or data.get("message") or str(data)[:200]
                raise ServiceError(f"WaveSpeed generation failed: {msg}")
            if cancel_event is not None and cancel_event.is_set():
                raise ServiceError("WaveSpeed polling cancelled by user")
            time.sleep(self.poll_interval)
            data = self._get(poll_endpoint)
            elapsed = int(time.time() - start)
            (logger.info if elapsed <= self.poll_interval else logger.debug)(
                "[WAVESPEED] poll %s (%ds): status=%s",
                prediction_id or poll_endpoint, elapsed, self._status(data))

    def _download_media(self, url: str,
                        *, default_mime: str = "application/octet-stream"
                        ) -> Tuple[bytes, str]:
        req = urllib.request.Request(
            url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read(), resp.headers.get("Content-Type", default_mime)

    def _invoke(self, op_name: str, body: Dict[str, Any],
                *, model_id: str = "") -> Dict[str, Any]:
        self.ensure_connected()
        op = self._op(op_name, model_id=model_id)
        endpoint = op.get("endpoint", "")
        if not endpoint:
            raise ServiceError(f"Operation '{op_name}' has no endpoint configured")
        logger.info("[WAVESPEED] %s/%s -> %s",
                    model_id or self._model_id, op_name, endpoint)
        data = self._post(endpoint, body)
        status = self._status(data)
        if status == "completed":
            url = _extract_output_url(
                data, output_path=op.get("output_path", "data.outputs.0"))
        else:
            url = self._poll(data, output_path=op.get("output_path", "data.outputs.0"))
        default_mime = {
            "video": "video/mp4",
            "audio": "audio/mpeg",
            "voice_clone": "audio/mpeg",
            "3d": "model/gltf-binary",
        }.get(self.CATEGORY, "image/png")
        media_bytes, content_type = self._download_media(
            url, default_mime=default_mime)
        return {"bytes": media_bytes, "content_type": content_type,
                "source_url": url}

    @staticmethod
    def _accepts(op: Dict[str, Any], name: str) -> bool:
        params = op.get("params") or {}
        return not params or name in params

    @staticmethod
    def _add_supported(body: Dict[str, Any], op: Dict[str, Any],
                       name: str, value: Any) -> None:
        if value not in (None, "") and _WaveSpeedBaseService._accepts(op, name):
            body[name] = value

    @staticmethod
    def _add_kwargs(body: Dict[str, Any], kwargs: Dict[str, Any]) -> None:
        internal = {
            "destination", "path", "service", "_service", "model",
            "reference_audio_bytes", "voice_id", "name",
        }
        for key, value in kwargs.items():
            if key not in body and key not in internal:
                body[key] = value
