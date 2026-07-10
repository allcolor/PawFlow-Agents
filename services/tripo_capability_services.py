"""Native Tripo3D service (api.tripo3d.ai).

Tripo exposes a single task-based API: POST /v2/openapi/task with a task
`type` (text_to_model, image_to_model, animate_rig, animate_retarget,
texture_model, convert_model, stylize_model, ...), then GET
/v2/openapi/task/{id} until success, then download `output` URLs.

https://platform.tripo3d.ai/docs
"""

import http.client
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core import ServiceFactory, ServiceError
from services.base_capabilities import BaseImage3DService

logger = logging.getLogger(__name__)

_API_HOST = "api.tripo3d.ai"
_API_PREFIX = "/v2/openapi"

_MODEL_MIMES = {
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "fbx": "application/octet-stream",
    "obj": "model/obj",
    "usdz": "model/vnd.usdz+zip",
    "stl": "model/stl",
    "3mf": "model/3mf",
}

# Preset animations accepted by animate_retarget.
PRESET_ANIMATIONS = (
    "preset:idle", "preset:walk", "preset:run", "preset:dive",
    "preset:climb", "preset:jump", "preset:slash", "preset:shoot",
    "preset:hurt", "preset:fall", "preset:turn",
    "preset:quadruped:walk", "preset:hexapod:walk", "preset:octopod:walk",
    "preset:serpentine:march", "preset:aquatic:march",
)

_TEXT_KEYS = {
    "negative_prompt", "model_version", "face_limit", "texture", "pbr",
    "image_seed", "model_seed", "texture_seed", "texture_quality",
    "style", "auto_size", "quad", "compress", "generate_parts",
    "smart_low_poly",
}
_IMAGE_KEYS = {
    "model_version", "face_limit", "texture", "pbr", "model_seed",
    "texture_seed", "texture_quality", "texture_alignment", "style",
    "auto_size", "orientation", "quad", "compress", "generate_parts",
    "smart_low_poly",
}
_RIG_KEYS = {"rig_type", "spec"}
_RETARGET_KEYS = {"bake_animation", "export_with_geometry"}
_TEXTURE_KEYS = {
    "texture", "pbr", "model_seed", "texture_seed", "texture_quality",
    "texture_alignment", "part_names", "compress", "bake",
}


def _image_file_ref(image_url: str) -> Dict[str, Any]:
    """Build Tripo's {"type": <ext>, "url": ...} file reference."""
    path = urllib.parse.urlsplit(image_url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ("jpg", "png", "webp"):
        ext = "jpg"
    return {"type": ext, "url": image_url}


class Tripo3DService(BaseImage3DService):
    """Tripo3D native 3D generation / rigging / animation / retexture."""

    TYPE = "tripo3DGeneration"
    VERSION = "1.0.0"
    NAME = "Tripo3D"
    DESCRIPTION = ("Native Tripo3D API: text-to-3D, image-to-3D, rigging, "
                   "animation retargeting, retexture, convert and stylize "
                   "(api.tripo3d.ai).")
    CATEGORY = "3d"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": ("Tripo3D API key "
                                "(https://platform.tripo3d.ai)."),
            },
            "model_version": {
                "type": "string", "required": False, "default": "",
                "description": ("Default generation model version (e.g. "
                                "'v2.5-20250123'). Empty = Tripo's current "
                                "default."),
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
        self.model_version = self.config.get("model_version", "") or ""
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.timeout = int(self.config.get("timeout", 120))
        self.max_retries = int(self.config.get("max_retries", 3))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for the Tripo3D service")
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
                conn.request(method, _API_PREFIX + path,
                             body=body_bytes or None, headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read().decode("utf-8", errors="replace")
                resp_status = resp.status
            finally:
                conn.close()
            if resp_status < 500:
                break
            logger.warning("[TRIPO] %s %s attempt %d/%d got %d: %s",
                           method, path, attempt + 1, self.max_retries,
                           resp_status, resp_body[:200])
            time.sleep([2, 4, 8][min(attempt, 2)])
        if resp_status >= 400:
            raise ServiceError(
                f"Tripo API error ({resp_status}): {resp_body[:300]}")
        data = json.loads(resp_body) if resp_body.strip() else {}
        code = data.get("code", 0)
        if code not in (0, None):
            raise ServiceError(
                f"Tripo API error (code {code}): "
                f"{data.get('message') or json.dumps(data)[:300]}")
        return data

    def _create_task(self, payload: Dict[str, Any]) -> str:
        data = self._request_json("POST", "/task", payload)
        task_id = str((data.get("data") or {}).get("task_id") or "")
        if not task_id:
            raise ServiceError(
                f"Tripo task creation returned no id: "
                f"{json.dumps(data)[:300]}")
        logger.info("[TRIPO] %s task %s", payload.get("type"), task_id)
        return task_id

    def _poll_task(self, task_id: str) -> Dict[str, Any]:
        try:
            from services.tool_relay_service import current_cancel_event
            cancel_event = current_cancel_event()
        except Exception:
            cancel_event = None
        start = time.time()
        while True:
            data = self._request_json("GET", f"/task/{task_id}")
            task = data.get("data") or {}
            status = str(task.get("status") or "").lower()
            if status == "success":
                return task
            if status in ("failed", "cancelled", "banned", "expired",
                          "unknown"):
                msg = task.get("error_msg") or f"task {status}"
                raise ServiceError(f"Tripo task {task_id} failed: {msg}")
            if cancel_event is not None and cancel_event.is_set():
                raise ServiceError("Tripo polling cancelled by user")
            elapsed = int(time.time() - start)
            (logger.info if elapsed <= self.poll_interval else logger.debug)(
                "[TRIPO] poll %s (%ds): status=%s progress=%s",
                task_id, elapsed, status, task.get("progress"))
            time.sleep(self.poll_interval)

    @staticmethod
    def _download(url: str, fmt: str = "glb") -> Tuple[bytes, str]:
        req = urllib.request.Request(
            url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:  # nosec B310 - provider-returned media download URL.
            mime = _MODEL_MIMES.get(fmt, "application/octet-stream")
            return resp.read(), resp.headers.get("Content-Type") or mime

    @staticmethod
    def _filtered(kwargs: Dict[str, Any], allowed: set) -> Dict[str, Any]:
        return {k: v for k, v in kwargs.items()
                if k in allowed and v not in (None, "")}

    def _task_result(self, task: Dict[str, Any], fmt: str = "glb") -> dict:
        output = task.get("output") or {}
        url = (output.get("pbr_model") or output.get("model")
               or output.get("base_model") or "")
        if not url:
            raise ServiceError(
                f"Tripo task {task.get('task_id')} succeeded but returned "
                f"no model URL: {json.dumps(output)[:300]}")
        payload, content_type = self._download(url, fmt)
        return {"bytes": payload, "content_type": content_type,
                "source_url": url,
                "task_id": str(task.get("task_id") or ""),
                "thumbnail_url": output.get("rendered_image", "")}

    # ── Public capability methods ─────────────────────────────────────

    def generate_3d(self, prompt: str = "", image_url: str = "",
                    model: str = "", **kwargs) -> dict:
        """text_to_model or image_to_model."""
        self.ensure_connected()
        if not prompt and not image_url:
            raise ServiceError("generate_3d requires `prompt` or `image_url`.")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        version = (model or kwargs.pop("model_version", "")
                   or self.model_version)
        if image_url:
            payload: Dict[str, Any] = {
                "type": "image_to_model",
                "file": _image_file_ref(image_url),
            }
            payload.update(self._filtered(kwargs, _IMAGE_KEYS))
        else:
            payload = {"type": "text_to_model", "prompt": prompt}
            payload.update(self._filtered(kwargs, _TEXT_KEYS))
        if version:
            payload["model_version"] = version
        task_id = self._create_task(payload)
        task = self._poll_task(task_id)
        return self._task_result(task, fmt)

    def rig_3d(self, task_id: str = "", model_url: str = "",
               **kwargs) -> dict:
        """animate_rig on a previous Tripo generation task."""
        self.ensure_connected()
        if not task_id:
            raise ServiceError(
                "Tripo rigging requires `task_id` of a previous Tripo "
                "generation task (external model URLs are not supported — "
                "use the Meshy service to rig arbitrary GLB files).")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        payload: Dict[str, Any] = {
            "type": "animate_rig",
            "original_model_task_id": task_id,
            "out_format": fmt if fmt in ("glb", "fbx") else "glb",
        }
        payload.update(self._filtered(kwargs, _RIG_KEYS))
        rig_id = self._create_task(payload)
        task = self._poll_task(rig_id)
        return self._task_result(task, fmt)

    def animate_3d(self, rig_task_id: str = "", animation: str = "",
                   **kwargs) -> dict:
        """animate_retarget: apply preset animation(s) to a rigged model.

        `animation` is a Tripo preset such as 'preset:walk' (or several,
        comma-separated). See PRESET_ANIMATIONS.
        """
        self.ensure_connected()
        if not rig_task_id:
            raise ServiceError("animate_3d requires `rig_task_id` (the id of "
                               "a successful Tripo rigging task).")
        if not animation:
            raise ServiceError(
                "animate_3d requires `animation`, e.g. 'preset:walk'. "
                f"Presets: {', '.join(PRESET_ANIMATIONS)}")
        animations = [a.strip() for a in str(animation).split(",")
                      if a.strip()]
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        payload: Dict[str, Any] = {
            "type": "animate_retarget",
            "original_model_task_id": rig_task_id,
            "animation": animations[0] if len(animations) == 1 else animations,
            "out_format": fmt if fmt in ("glb", "fbx") else "glb",
        }
        payload.update(self._filtered(kwargs, _RETARGET_KEYS))
        anim_id = self._create_task(payload)
        task = self._poll_task(anim_id)
        return self._task_result(task, fmt)

    def retexture_3d(self, task_id: str = "", model_url: str = "",
                     prompt: str = "", image_url: str = "",
                     **kwargs) -> dict:
        """texture_model: regenerate textures on a previous Tripo task."""
        self.ensure_connected()
        if not task_id:
            raise ServiceError(
                "Tripo retexture requires `task_id` of a previous Tripo "
                "generation task (external model URLs are not supported — "
                "use the Meshy service to retexture arbitrary model files).")
        fmt = str(kwargs.pop("format", "") or "glb").lower()
        payload: Dict[str, Any] = {
            "type": "texture_model",
            "original_model_task_id": task_id,
        }
        if prompt:
            payload["text_prompt"] = prompt
        if image_url:
            payload["image_prompt"] = _image_file_ref(image_url)
        payload.update(self._filtered(kwargs, _TEXTURE_KEYS))
        retex_id = self._create_task(payload)
        task = self._poll_task(retex_id)
        return self._task_result(task, fmt)

    def convert_3d(self, task_id: str, format: str = "GLTF",
                   **kwargs) -> dict:
        """convert_model: export a previous Tripo task to another format."""
        self.ensure_connected()
        fmt = str(format or "GLTF").upper()
        payload: Dict[str, Any] = {
            "type": "convert_model",
            "original_model_task_id": task_id,
            "format": fmt,
        }
        for key in ("quad", "face_limit", "texture_size", "texture_format",
                    "pivot_to_center_bottom", "with_animation", "pack_uv",
                    "bake", "force_symmetry", "flatten_bottom"):
            if key in kwargs and kwargs[key] not in (None, ""):
                payload[key] = kwargs[key]
        conv_id = self._create_task(payload)
        task = self._poll_task(conv_id)
        return self._task_result(task, fmt.lower())

    def stylize_3d(self, task_id: str, style: str,
                   block_size: int = 80, **kwargs) -> dict:
        """stylize_model: lego / voxel / voronoi / minecraft."""
        self.ensure_connected()
        payload = {"type": "stylize_model",
                   "original_model_task_id": task_id,
                   "style": style, "block_size": block_size}
        style_id = self._create_task(payload)
        task = self._poll_task(style_id)
        return self._task_result(task)

    def get_model_info(self) -> dict:
        return {
            "provider": "tripo3d",
            "model_version": self.model_version or "(Tripo default)",
            "operations": {
                "generate_3d": "text_to_model / image_to_model",
                "rig_3d": "animate_rig on a previous Tripo task_id",
                "animate_3d": ("animate_retarget with preset animations "
                               "(rig_task_id required)"),
                "retexture_3d": "texture_model on a previous Tripo task_id",
                "convert_3d": "convert_model to GLTF/USDZ/FBX/OBJ/STL/3MF",
                "stylize_3d": "stylize_model (lego/voxel/voronoi/minecraft)",
            },
            "animations": list(PRESET_ANIMATIONS),
        }


ServiceFactory.register(Tripo3DService)
