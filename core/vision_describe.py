"""Vision fallback — describe images through a vision-enabled llmConnection.

When an llmConnection has supports_vision=false and names a
vision_llm_service, image parts in its outbound messages are replaced by
detailed textual descriptions produced by that vision service, so
non-vision models can still act on screenshots, uploads, and tool-result
images (see, read, browser). Descriptions are cached by image content
hash — in memory and on disk — so each unique image is described once,
not once per turn.
"""

import base64
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v1"

DESCRIBE_PROMPT = (
    "You are the eyes of a text-only assistant. Describe this image "
    "exhaustively and factually so the assistant can act on it without "
    "seeing it.{dims}\n"
    "Include:\n"
    "- the overall layout and apparent purpose;\n"
    "- ALL visible text, verbatim;\n"
    "- every notable element or UI control (buttons, fields, links, menus, "
    "icons, images) with its approximate pixel coordinates as "
    "[x, y, width, height];\n"
    "- element states (focused, disabled, checked, selected) and colors;\n"
    "- anything unusual, truncated, or error-like.\n"
    "Be precise; do not speculate beyond what is visible."
)

# Bound the number of vision calls a single message-list pass may trigger
# (a video see() emits up to 5 frames; runaway contexts must not fan out).
_MAX_DESCRIBE_PER_PASS = 12

_MEM_CACHE_MAX = 512
_DISK_CACHE_MAX = 2000

_cache_lock = threading.Lock()
_mem_cache: "OrderedDict[str, str]" = OrderedDict()
_disk_loaded = False

# Recursion guard: the describe call itself runs through
# LLMConnectionService.complete — a misconfigured vision service chain
# (A -> B -> A) must not loop.
_tls = threading.local()


def _disk_cache_path() -> str:
    from core.paths import RUNTIME_DIR
    return str(RUNTIME_DIR / "vision_describe_cache.json")


def _load_disk_cache_locked() -> None:
    global _disk_loaded
    if _disk_loaded:
        return
    _disk_loaded = True
    try:
        with open(_disk_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and isinstance(value, str):
                    _mem_cache[key] = value
            while len(_mem_cache) > _MEM_CACHE_MAX:
                _mem_cache.popitem(last=False)
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("vision describe disk cache load failed", exc_info=True)


def _save_disk_cache_locked() -> None:
    try:
        path = _disk_cache_path()
        existing: Dict[str, str] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = {k: v for k, v in loaded.items()
                            if isinstance(k, str) and isinstance(v, str)}
        except Exception:  # nosec B110 - cache file may be absent/corrupt
            logger.debug("vision describe disk cache merge skipped", exc_info=True)
        existing.update(_mem_cache)
        if len(existing) > _DISK_CACHE_MAX:
            drop = len(existing) - _DISK_CACHE_MAX
            for key in list(existing.keys())[:drop]:
                existing.pop(key, None)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        os.replace(tmp, path)
    except Exception:
        logger.debug("vision describe disk cache save failed", exc_info=True)


def _cache_get(key: str) -> Optional[str]:
    with _cache_lock:
        _load_disk_cache_locked()
        value = _mem_cache.get(key)
        if value is not None:
            _mem_cache.move_to_end(key)
        return value


def _cache_put(key: str, value: str) -> None:
    with _cache_lock:
        _load_disk_cache_locked()
        _mem_cache[key] = value
        _mem_cache.move_to_end(key)
        while len(_mem_cache) > _MEM_CACHE_MAX:
            _mem_cache.popitem(last=False)
        _save_disk_cache_locked()


def resolve_vision_service(service_id: str, *, user_id: str = "",
                           conversation_id: str = "") -> Tuple[Any, str]:
    """Resolve a vision-enabled llmConnection. Returns (service, error)."""
    service_id = (service_id or "").strip()
    if not service_id:
        return None, "no vision_llm_service configured"
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            service_id, user_id=user_id, conv_id=conversation_id)
    except Exception as exc:
        return None, f"vision_llm_service '{service_id}' failed to resolve: {exc}"
    if not svc or getattr(svc, "TYPE", "") != "llmConnection":
        return None, f"vision_llm_service '{service_id}' is not an llmConnection service"
    client = svc.get_client() if hasattr(svc, "get_client") else None
    if not client or not getattr(client, "supports_vision", False):
        return None, f"vision_llm_service '{service_id}' does not have vision enabled"
    return svc, ""


def _image_dims(mime: str, b64: str) -> str:
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(b64))) as img:
            return f" The image is {img.width}x{img.height} pixels."
    except Exception:
        return ""


def describe_image_b64(vision_svc, mime: str, b64: str, *,
                       user_id: str = "", conversation_id: str = "",
                       agent_name: str = "", prompt: str = "",
                       model: str = "", max_tokens: int = 1500) -> str:
    """Describe one base64 image via a vision llmConnection, with caching."""
    svc_id = getattr(vision_svc, "_service_id", "") or ""
    model = model or ""
    cache_key = hashlib.sha256(
        f"{_PROMPT_VERSION}|{svc_id}|{model}|{prompt}|{mime}|".encode()
        + b64.encode()
    ).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    full_prompt = prompt or DESCRIBE_PROMPT.format(dims=_image_dims(mime, b64))
    from core.llm_client import LLMMessage
    _prev_active = getattr(_tls, "active", False)
    _tls.active = True
    try:
        response = vision_svc.complete(
            [LLMMessage(
                role="user",
                content=[
                    {"type": "text", "text": full_prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
                conversation_id=conversation_id or "vision_describe",
            )],
            model=model or None,
            temperature=0,
            max_tokens=max_tokens,
            call_user_id=user_id,
            call_conversation_id=conversation_id,
            call_agent_name=agent_name,
        )
    finally:
        _tls.active = _prev_active
    description = (getattr(response, "content", "") or "").strip()
    if description:
        _cache_put(cache_key, description)
    return description


def _part_payload(part: Dict[str, Any], *, user_id: str,
                  conversation_id: str) -> Optional[Tuple[str, str, str]]:
    """Extract (mime, b64, label) from an image part, or None."""
    ptype = part.get("type", "")
    if ptype == "image_ref":
        file_id = str(part.get("file_id") or "").strip()
        if not file_id:
            return None
        try:
            from core.file_store import FileStore
            fname, data, content_type = FileStore.instance().get_required(
                file_id, user_id=user_id, conversation_id=conversation_id)
        except Exception:
            logger.debug("vision fallback: image_ref %s unavailable",
                         file_id, exc_info=True)
            return None
        mime = part.get("mime_type", content_type) or "image/png"
        label = part.get("filename") or fname or file_id
        return mime, base64.b64encode(data).decode("ascii"), str(label)
    if ptype == "image_url":
        image_url = part.get("image_url") or {}
        url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
        if url.startswith("data:") and ";base64," in url:
            header, b64 = url.split(";base64,", 1)
            mime = header[len("data:"):] or "image/png"
            return mime, b64, "inline image"
    return None


def apply_vision_fallback(messages: List[Any], vision_service_id: str, *,
                          source_service_id: str = "",
                          user_id: str = "", conversation_id: str = "",
                          agent_name: str = "") -> List[Any]:
    """Replace image parts with vision-service descriptions.

    Returns a transformed copy of `messages`; the input list and its
    messages are never mutated (the stored conversation keeps its image
    parts for future vision-enabled agents). On any failure the original
    part is kept (providers degrade it to a text link as before).
    """
    if getattr(_tls, "active", False):
        return messages
    if not any(isinstance(getattr(m, "content", None), list) and any(
            isinstance(p, dict) and p.get("type") in ("image_ref", "image_url")
            for p in m.content) for m in messages):
        return messages
    if source_service_id and vision_service_id == source_service_id:
        logger.warning("vision fallback: '%s' references itself; skipping",
                       source_service_id)
        return messages
    vision_svc, err = resolve_vision_service(
        vision_service_id, user_id=user_id, conversation_id=conversation_id)
    if not vision_svc:
        logger.warning("vision fallback disabled for '%s': %s",
                       source_service_id or "llm service", err)
        return messages

    described = 0
    truncated = False
    out: List[Any] = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if not (isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") in ("image_ref", "image_url")
                for p in content)):
            out.append(msg)
            continue
        new_parts: List[Dict[str, Any]] = []
        changed = False
        for part in content:
            if not (isinstance(part, dict)
                    and part.get("type") in ("image_ref", "image_url")):
                new_parts.append(part)
                continue
            if described >= _MAX_DESCRIBE_PER_PASS:
                truncated = True
                new_parts.append(part)
                continue
            payload = _part_payload(part, user_id=user_id,
                                    conversation_id=conversation_id)
            if not payload:
                new_parts.append(part)
                continue
            mime, b64, label = payload
            try:
                description = describe_image_b64(
                    vision_svc, mime, b64, user_id=user_id,
                    conversation_id=conversation_id, agent_name=agent_name)
            except Exception:
                logger.warning("vision fallback describe failed for %s",
                               label, exc_info=True)
                new_parts.append(part)
                continue
            if not description:
                new_parts.append(part)
                continue
            described += 1
            changed = True
            new_parts.append({
                "type": "text",
                "text": (
                    f"[Image: {label} — you cannot see images directly; a "
                    f"vision model described it as follows]\n{description}"
                ),
            })
        if changed:
            import copy
            new_msg = copy.copy(msg)
            new_msg.content = new_parts
            out.append(new_msg)
        else:
            out.append(msg)
    if truncated:
        logger.warning(
            "vision fallback: more than %d images in one pass; extra images "
            "were left as links", _MAX_DESCRIBE_PER_PASS)
    if described:
        logger.info("vision fallback: described %d image(s) via '%s'",
                    described, vision_service_id)
    return out
