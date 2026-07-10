"""Native Meshy AI 3D service (api.meshy.ai).

Task-based REST API: POST creates a task, GET polls it until SUCCEEDED,
then the output model file is downloaded. Covers the full Meshy OpenAPI
surface relevant to agents: text-to-3D (preview + refine), image-to-3D,
rigging, animation and retexture.

https://docs.meshy.ai/en/api
"""

import http.client
import json
import logging
import ssl
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core import ServiceFactory, ServiceError
from services.base_capabilities import BaseImage3DService

logger = logging.getLogger(__name__)

_API_HOST = "api.meshy.ai"

_MODEL_MIMES = {
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "fbx": "application/octet-stream",
    "obj": "model/obj",
    "usdz": "model/vnd.usdz+zip",
    "stl": "model/stl",
    "3mf": "model/3mf",
}

# Per-endpoint request fields accepted by the Meshy OpenAPI. Anything the
# agent passes outside these sets is dropped instead of causing a 400.
_PREVIEW_KEYS = {
    "model_type", "ai_model", "should_remesh", "topology",
    "target_polycount", "decimation_mode", "pose_mode", "art_style",
    "moderation", "target_formats", "alpha_thumbnail", "auto_size",
    "origin_at",
}
_REFINE_KEYS = {
    "enable_pbr", "hd_texture", "texture_prompt", "texture_image_url",
    "ai_model", "moderation", "remove_lighting", "target_formats",
    "alpha_thumbnail", "auto_size", "origin_at",
}
_IMAGE_KEYS = {
    "model_type", "ai_model", "should_texture", "enable_pbr", "hd_texture",
    "texture_prompt", "texture_image_url", "should_remesh", "topology",
    "target_polycount", "decimation_mode", "save_pre_remeshed_model",
    "pose_mode", "image_enhancement", "remove_lighting", "moderation",
    "target_formats", "auto_size", "alpha_thumbnail",
    "multi_view_thumbnails", "origin_at",
}
_RIG_KEYS = {"height_meters", "texture_image_url"}
_ANIMATE_KEYS = {"post_process"}
_RETEXTURE_KEYS = {
    "ai_model", "enable_original_uv", "enable_pbr", "hd_texture",
    "remove_lighting", "target_formats", "alpha_thumbnail",
}


class Meshy3DService(BaseImage3DService):
    """Meshy AI native 3D generation / rigging / animation / retexture."""

    TYPE = "meshy3DGeneration"
    VERSION = "1.0.0"
    NAME = "Meshy AI 3D"
    DESCRIPTION = ("Native Meshy AI API: text-to-3D, image-to-3D, rigging, "
                   "animation and retexture (api.meshy.ai).")
    CATEGORY = "3d"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Meshy API key (https://www.meshy.ai/api).",
            },
            "ai_model": {
                "type": "string", "required": False, "default": "latest",
                "description": ("Default Meshy AI model: 'meshy-5', "
                                "'meshy-6' or 'latest'."),
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 5,
                "description": "Seconds between task status checks.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds per request.",
            },
            "max_retries": {
                "type": "integer", "required": False, "default": 3,
                "description": "Retry count for transient 5xx responses.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.ai_model = self.config.get("ai_model", "latest") or "latest"
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.timeout = int(self.config.get("timeout", 120))
        self.max_retries = int(self.config.get("max_retries", 3))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for the Meshy service")
        return {"ready": True}

    def _close_connection(self):
        pass

    # ── HTTP plumbing ─────────────────────────────────────────────────

    def _request_json(self, method: str, path: str,
                      body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body_bytes = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if body_bytes:
            headers["Content-Type"] = "application/json"
        ctx = ssl.create_default_context()
        resp_body, resp_status = "", 0
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
            logger.warning("[MESHY] %s %s attempt %d/%d got %d: %s",
                           method, path, attempt + 1, self.max_retries,
                           resp_status, resp_body[:200])
            time.sleep([2, 4, 8][min(attempt, 2)])
        if resp_status >= 400:
            raise ServiceError(
                f"Meshy API error ({resp_status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def _create(self, path: str, body: Dict[str, Any]) -> str:
        data = self._request_json("POST", path, body)
        task_id = str(data.get("result") or "")
        if not task_id:
            raise ServiceError(
                f"Meshy task creation returned no id: {json.dumps(data)[:300]}")
        return task_id

    def _poll(self, path: str, task_id: str) -> Dict[str, Any]:
        try:
            from services.tool_relay_service import current_cancel_event
            cancel_event = current_cancel_event()
        except Exception:
            cancel_event = None
        start = time.time()
        while True:
            data = self._request_json("GET", f"{path}/{task_id}")
            status = str(data.get("status") or "").upper()
            if status == "SUCCEEDED":
                return data
            if status in ("FAILED", "CANCELED"):
                msg = ((data.get("task_error") or {}).get("message")
                       or f"task {status.lower()}")
                raise ServiceError(f"Meshy task {task_id} failed: {msg}")
            if cancel_event is not None and cancel_event.is_set():
                raise ServiceError("Meshy polling cancelled by user")
            elapsed = int(time.time() - start)
            (logger.info if elapsed <= self.poll_interval else logger.debug)(
                "[MESHY] poll %s (%ds): status=%s progress=%s",
                task_id, elapsed, status, data.get("progress"))
            time.sleep(self.poll_interval)

    @staticmethod
    def _download(url: str, fmt: str) -> Tuple[bytes, str]:
        req = urllib.request.Request(
            url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:  # nosec B310 - provider-returned media download URL.
            mime = _MODEL_MIMES.get(fmt, "application/octet-stream")
            return resp.read(), resp.headers.get("Content-Type") or mime

    @staticmethod
    def _filtered(kwargs: Dict[str, Any], allowed: set) -> Dict[str, Any]:
        return {k: v for k, v in kwargs.items()
                if k in allowed and v not in (None, "")}

    def _model_result(self, task: Dict[str, Any], fmt: str) -> dict:
        urls = task.get("model_urls") or {}
        url = urls.get(fmt) or urls.get("glb") or next(
            (u for u in urls.values() if u), "")
        if not url:
            raise ServiceError(
                f"Meshy task {task.get('id')} succeeded but returned no "
                f"model URL for format '{fmt}'")
        used_fmt = fmt if urls.get(fmt) else "glb"
        payload, content_type = self._download(url, used_fmt)
        return {"bytes": payload, "content_type": content_type,
                "source_url": url, "task_id": str(task.get("id") or ""),
                "thumbnail_url": task.get("thumbnail_url", "")}

    # ── Public capability methods ─────────────────────────────────────

    def generate_3d(self, prompt: str = "", image_url: str = "",
                    model: str = "", **kwargs) -> dict:
        """Text-to-3D (preview + refine) or image-to-3D."""
        self.ensure_connected()
        if not prompt and not image_url:
            raise ServiceError("generate_3d requires `prompt` or `image_url`.")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        ai_model = model or kwargs.pop("ai_model", "") or self.ai_model
        kwargs.setdefault("target_formats", [fmt])
        if image_url:
            body = {"image_url": image_url, "ai_model": ai_model}
            body.update(self._filtered(kwargs, _IMAGE_KEYS))
            task_id = self._create("/openapi/v1/image-to-3d", body)
            logger.info("[MESHY] image-to-3d task %s", task_id)
            task = self._poll("/openapi/v1/image-to-3d", task_id)
            return self._model_result(task, fmt)
        refine = str(kwargs.pop("refine", True)).strip().lower() not in (
            "0", "false", "no", "off")
        body = {"mode": "preview", "prompt": prompt, "ai_model": ai_model}
        body.update(self._filtered(kwargs, _PREVIEW_KEYS))
        preview_id = self._create("/openapi/v2/text-to-3d", body)
        logger.info("[MESHY] text-to-3d preview task %s", preview_id)
        task = self._poll("/openapi/v2/text-to-3d", preview_id)
        if refine:
            body = {"mode": "refine", "preview_task_id": preview_id,
                    "ai_model": ai_model}
            body.update(self._filtered(kwargs, _REFINE_KEYS))
            refine_id = self._create("/openapi/v2/text-to-3d", body)
            logger.info("[MESHY] text-to-3d refine task %s", refine_id)
            task = self._poll("/openapi/v2/text-to-3d", refine_id)
        return self._model_result(task, fmt)

    def rig_3d(self, task_id: str = "", model_url: str = "",
               **kwargs) -> dict:
        """Rig a humanoid model (from a Meshy task id or a public GLB URL)."""
        self.ensure_connected()
        if not task_id and not model_url:
            raise ServiceError("rig_3d requires `task_id` or `model_url`.")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        body: Dict[str, Any] = {}
        if task_id:
            body["input_task_id"] = task_id
        else:
            body["model_url"] = model_url
        body.update(self._filtered(kwargs, _RIG_KEYS))
        rig_id = self._create("/openapi/v1/rigging", body)
        logger.info("[MESHY] rigging task %s", rig_id)
        task = self._poll("/openapi/v1/rigging", rig_id)
        result = task.get("result") or {}
        url = (result.get(f"rigged_character_{fmt}_url")
               or result.get("rigged_character_glb_url", ""))
        if not url:
            raise ServiceError(
                f"Meshy rigging task {rig_id} succeeded but returned no "
                f"rigged model URL")
        payload, content_type = self._download(
            url, fmt if result.get(f"rigged_character_{fmt}_url") else "glb")
        return {"bytes": payload, "content_type": content_type,
                "source_url": url, "task_id": rig_id,
                "basic_animations": result.get("basic_animations") or {}}

    def animate_3d(self, rig_task_id: str = "", animation: str = "",
                   **kwargs) -> dict:
        """Apply an animation action to a rigged character.

        `animation` is a numeric Meshy `action_id` from the Meshy animation
        library (https://docs.meshy.ai/en/api/animation).
        """
        self.ensure_connected()
        if not rig_task_id:
            raise ServiceError("animate_3d requires `rig_task_id` (the id of "
                               "a successful rigging task).")
        try:
            action_id = int(str(animation).strip())
        except (TypeError, ValueError):
            raise ServiceError(
                "Meshy animations use a numeric `animation` action id from "
                "the Meshy animation library (e.g. 92). Got: "
                f"{animation!r}")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        body: Dict[str, Any] = {"rig_task_id": rig_task_id,
                                "action_id": action_id}
        fps = int(kwargs.pop("fps", 0) or 0)
        if fps:
            body["post_process"] = {"operation_type": "change_fps",
                                    "fps": fps}
        body.update(self._filtered(kwargs, _ANIMATE_KEYS))
        anim_id = self._create("/openapi/v1/animations", body)
        logger.info("[MESHY] animation task %s (action %d)", anim_id, action_id)
        task = self._poll("/openapi/v1/animations", anim_id)
        result = task.get("result") or {}
        url = (result.get(f"animation_{fmt}_url")
               or result.get("animation_glb_url", ""))
        if not url:
            raise ServiceError(
                f"Meshy animation task {anim_id} succeeded but returned no "
                f"animation URL")
        payload, content_type = self._download(
            url, fmt if result.get(f"animation_{fmt}_url") else "glb")
        return {"bytes": payload, "content_type": content_type,
                "source_url": url, "task_id": anim_id}

    def retexture_3d(self, task_id: str = "", model_url: str = "",
                     prompt: str = "", image_url: str = "",
                     **kwargs) -> dict:
        """Re-texture an existing model with a text or image style."""
        self.ensure_connected()
        if not task_id and not model_url:
            raise ServiceError("retexture_3d requires `task_id` or "
                               "`model_url`.")
        if not prompt and not image_url:
            raise ServiceError("retexture_3d requires `prompt` (text style) "
                               "or `image_url` (style image).")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        kwargs.setdefault("ai_model", self.ai_model)
        kwargs.setdefault("target_formats", [fmt])
        body: Dict[str, Any] = {}
        if task_id:
            body["input_task_id"] = task_id
        else:
            body["model_url"] = model_url
        if image_url:
            body["image_style_url"] = image_url
        else:
            body["text_style_prompt"] = prompt
        body.update(self._filtered(kwargs, _RETEXTURE_KEYS))
        retex_id = self._create("/openapi/v1/retexture", body)
        logger.info("[MESHY] retexture task %s", retex_id)
        task = self._poll("/openapi/v1/retexture", retex_id)
        return self._model_result(task, fmt)

    def get_model_info(self) -> dict:
        return {
            "provider": "meshy",
            "ai_model": self.ai_model,
            "operations": {
                "generate_3d": "text-to-3D (preview+refine) / image-to-3D",
                "rig_3d": "rig a humanoid model (task_id or GLB model_url)",
                "animate_3d": ("apply a Meshy library action_id to a rigged "
                               "character (rig_task_id required)"),
                "retexture_3d": "re-texture a model from a text/image style",
            },
            "formats": sorted(_MODEL_MIMES),
        }


ServiceFactory.register(Meshy3DService)
