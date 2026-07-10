"""3D post-processing tool handlers: rigging, animation, retexture.

Same provider-agnostic pattern as Generate3DHandler: a resolver wired by
AgentToolConfigMixin picks the active 3D service; the service must expose
the matching method (`rig_3d`, `animate_3d`, `retexture_3d` — implemented
by the native Tripo3D and Meshy services).
"""

import logging
import time
from typing import Any, Dict

from core.handlers._capability_base import (
    _SERVICE_ARG_NAMES,
    _CapabilityHandlerBase,
)

logger = logging.getLogger(__name__)

_MODEL_EXTS = {"model/gltf-binary": "glb", "model/gltf+json": "gltf",
               "model/obj": "obj", "model/vnd.usdz+zip": "usdz",
               "model/stl": "stl", "model/3mf": "3mf"}


def _model_ext(result: Dict[str, Any], fallback: str = "glb") -> str:
    ct = (result.get("content_type", "") or "").split(";")[0].strip()
    if ct in _MODEL_EXTS:
        return _MODEL_EXTS[ct]
    url = result.get("source_url", "") or ""
    tail = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if "." in tail:
        ext = tail.rsplit(".", 1)[-1].lower()
        if ext in _MODEL_EXTS.values() or ext == "fbx":
            return ext
    return fallback


def _with_task_id(message: str, result: Dict[str, Any]) -> str:
    task_id = str(result.get("task_id") or "")
    if task_id and "task_id:" not in message:
        message += f"\ntask_id: {task_id}"
    return message


class Rig3DModelHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "rig_3d_model"

    @property
    def description(self) -> str:
        return (
            "Rig (add an animation skeleton to) a humanoid 3D model using "
            "the active 3D service (Meshy, Tripo3D). Pass `task_id` (the "
            "vendor task id returned by generate_3d, preferred) or "
            "`model_url` (public/FileStore GLB URL — Meshy only). Returns "
            "the rigged model and a `task_id` to use with animate_3d_model. "
            "Saves the result to FileStore (default) or a filesystem "
            "service when `destination` + `path` are given."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Vendor task id of a previous generate_3d call on the same service"},
                "model_url": {"type": "string", "description": "GLB model URL (HTTP or fs://filestore/<id>/<name>) — Meshy only"},
                "height_meters": {"type": "number", "description": "Approximate character height in meters (Meshy, default 1.7)"},
                "format": {"type": "string", "description": "Output format: 'glb' (default) or 'fbx'"},
                "destination": {"type": "string", "description": "'filestore' (default) or relay service name"},
                "path": {"type": "string", "description": "Filename when saving to a filesystem service"},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no 3D service with rigging support available'}"
        if not hasattr(svc, "rig_3d"):
            return ("Error: the active 3D service does not support rigging "
                    "(use a Meshy or Tripo3D service)")
        task_id = str(arguments.get("task_id") or "").strip()
        model_url = self._rewrite(
            str(arguments.get("model_url") or "").strip(), service=svc)
        if not task_id and not model_url:
            return "Error: provide `task_id` or `model_url`"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "task_id",
                                   "model_url", *_SERVICE_ARG_NAMES)}
            r = svc.rig_3d(task_id=task_id, model_url=model_url, **kwargs)
            filename = arguments.get("path") or (
                f"rigged_{int(time.time())}.{_model_ext(r)}")
            destination = arguments.get("destination", "filestore")
            msg = self._persist(destination, filename, r, "Rigged 3D model")
            return _with_task_id(msg, r)
        except Exception as e:
            return f"Error rigging 3D model: {e}"


class Animate3DModelHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "animate_3d_model"

    @property
    def description(self) -> str:
        return (
            "Apply an animation to a rigged 3D character using the active "
            "3D service (Meshy, Tripo3D). `rig_task_id` is the task_id "
            "returned by rig_3d_model on the same service. `animation` is "
            "provider-specific: a numeric Meshy action id from the Meshy "
            "animation library (e.g. '92'), or a Tripo preset such as "
            "'preset:walk', 'preset:run', 'preset:idle', 'preset:jump'. "
            "Saves the animated model to FileStore (default) or a "
            "filesystem service when `destination` + `path` are given."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rig_task_id": {"type": "string", "description": "task_id of a successful rig_3d_model call on the same service"},
                "animation": {"type": "string", "description": "Meshy numeric action id (e.g. '92') or Tripo preset (e.g. 'preset:walk'; comma-separate for several)"},
                "format": {"type": "string", "description": "Output format: 'glb' (default) or 'fbx'"},
                "fps": {"type": "integer", "description": "Optional target FPS post-process (Meshy: 24/25/30/60)"},
                "destination": {"type": "string", "description": "'filestore' (default) or relay service name"},
                "path": {"type": "string", "description": "Filename when saving to a filesystem service"},
            },
            "required": ["rig_task_id", "animation"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no 3D service with animation support available'}"
        if not hasattr(svc, "animate_3d"):
            return ("Error: the active 3D service does not support animation "
                    "(use a Meshy or Tripo3D service)")
        rig_task_id = str(arguments.get("rig_task_id") or "").strip()
        animation = str(arguments.get("animation") or "").strip()
        if not rig_task_id or not animation:
            return "Error: `rig_task_id` and `animation` are required"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "rig_task_id",
                                   "animation", *_SERVICE_ARG_NAMES)}
            r = svc.animate_3d(rig_task_id=rig_task_id, animation=animation,
                               **kwargs)
            filename = arguments.get("path") or (
                f"animated_{int(time.time())}.{_model_ext(r)}")
            destination = arguments.get("destination", "filestore")
            msg = self._persist(destination, filename, r, "Animated 3D model")
            return _with_task_id(msg, r)
        except Exception as e:
            return f"Error animating 3D model: {e}"


class Retexture3DModelHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "retexture_3d_model"

    @property
    def description(self) -> str:
        return (
            "Re-texture an existing 3D model with a new style using the "
            "active 3D service (Meshy, Tripo3D). Pass `task_id` (vendor "
            "task id from generate_3d, preferred) or `model_url` (public/"
            "FileStore model URL — Meshy only), plus `prompt` (text style) "
            "or `image_url` (style image). Saves the result to FileStore "
            "(default) or a filesystem service when `destination` + `path` "
            "are given."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Vendor task id of a previous generate_3d call on the same service"},
                "model_url": {"type": "string", "description": "Model URL (HTTP or fs://filestore/<id>/<name>) — Meshy only, .glb/.gltf/.obj/.fbx/.stl"},
                "prompt": {"type": "string", "description": "Text description of the desired texture style"},
                "image_url": {"type": "string", "description": "Style image URL to guide the texturing"},
                "format": {"type": "string", "description": "Output format: 'glb' (default), 'fbx', 'obj', 'usdz', 'stl'"},
                "destination": {"type": "string", "description": "'filestore' (default) or relay service name"},
                "path": {"type": "string", "description": "Filename when saving to a filesystem service"},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no 3D service with retexture support available'}"
        if not hasattr(svc, "retexture_3d"):
            return ("Error: the active 3D service does not support retexture "
                    "(use a Meshy or Tripo3D service)")
        task_id = str(arguments.get("task_id") or "").strip()
        model_url = self._rewrite(
            str(arguments.get("model_url") or "").strip(), service=svc)
        image_url = self._rewrite(
            str(arguments.get("image_url") or "").strip(), service=svc)
        prompt = str(arguments.get("prompt") or "").strip()
        if not task_id and not model_url:
            return "Error: provide `task_id` or `model_url`"
        if not prompt and not image_url:
            return "Error: provide `prompt` or `image_url` (style)"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "task_id",
                                   "model_url", "prompt", "image_url",
                                   *_SERVICE_ARG_NAMES)}
            r = svc.retexture_3d(task_id=task_id, model_url=model_url,
                                 prompt=prompt, image_url=image_url, **kwargs)
            filename = arguments.get("path") or (
                f"retextured_{int(time.time())}.{_model_ext(r)}")
            destination = arguments.get("destination", "filestore")
            msg = self._persist(destination, filename, r,
                                "Retextured 3D model")
            return _with_task_id(msg, r)
        except Exception as e:
            return f"Error retexturing 3D model: {e}"
