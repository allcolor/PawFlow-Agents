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
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v2"

 # Images larger than this (in either dimension) are downscaled before being
 # sent to the vision model.  Large screenshots (e.g. a full YouTube homepage
 # at 1280x800, ~900 KB PNG) can overwhelm smaller vision models and cause
 # 500 / timeout errors.  1024px is a safe ceiling that preserves text
 # legibility while keeping the base64 payload manageable.
_MAX_IMAGE_DIM = 1024

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
    "RULES:\n"
    "- Output ONLY the final description. No preamble, no process narration, "
    "no 'I see', 'Let me', 'Looking at', no meta-commentary.\n"
    "- Never guess: if a text element, URL, date, or label is illegible or "
    "unclear, write [illegible] instead of inventing it.\n"
    "- Be precise; do not speculate beyond what is visible."
)

# Bound the number of vision calls a single message-list pass may trigger
# (a video see() emits up to 5 frames; runaway contexts must not fan out).
_MAX_DESCRIBE_PER_PASS = 12

_MEM_CACHE_MAX = 512
_DISK_CACHE_MAX = 2000

_cache_lock = threading.Lock()
_mem_cache: "OrderedDict[str, str]" = OrderedDict()
_disk_loaded = False

# Single-flight: when several workers describe the SAME image in parallel
# (a multi-image pass or duplicated image_refs), only one performs the
# network call; the others wait on the same key's Event and then read the
# fresh cache entry. Prevents stampede duplicate API calls for one image.
_inflight: "Dict[str, threading.Event]" = {}
_inflight_lock = threading.Lock()

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


def _downscale_b64(mime: str, b64: str) -> tuple:
    """Downscale an image so neither dimension exceeds _MAX_IMAGE_DIM.

    Returns (mime, b64) — the original pair if no resize is needed or if
    PIL is unavailable.  Output format is JPEG (quality 85) for photos and
    PNG for images with transparency, to keep the payload small.
    """
    try:
        import io
        from PIL import Image
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
            if w <= _MAX_IMAGE_DIM and h <= _MAX_IMAGE_DIM:
                return mime, b64
            scale = _MAX_IMAGE_DIM / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.convert("RGBA") if img.mode == "RGBA" else img.convert("RGB")
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            if resized.mode == "RGBA":
                resized.save(buf, format="PNG")
                out_mime = "image/png"
            else:
                resized.save(buf, format="JPEG", quality=85)
                out_mime = "image/jpeg"
            return out_mime, base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        logger.debug("vision fallback: downscale failed, using original", exc_info=True)
        return mime, b64


def describe_image_b64(vision_svc, mime: str, b64: str, *,
                       user_id: str = "", conversation_id: str = "",
                       agent_name: str = "", prompt: str = "",
                       model: str = "", max_tokens: int = 1024) -> str:
    """Describe one base64 image via a vision llmConnection, with caching."""
    svc_id = getattr(vision_svc, "_service_id", "") or ""
    model = model or ""
    # Per-service override: the vision service's own config can raise (or
    # lower) the output budget. Verbose models (GPT-5.6 Luna) need more room
    # than the 1024-token default; cheap models stay fast at the default.
    svc_config = getattr(vision_svc, "config", {}) or {}
    if isinstance(svc_config, dict) and svc_config.get("vision_max_tokens"):
        max_tokens = int(svc_config["vision_max_tokens"])
    cache_key = hashlib.sha256(
        f"{_PROMPT_VERSION}|{svc_id}|{model}|{prompt}|{mime}|{max_tokens}|".encode()
        + b64.encode()
    ).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Join any in-flight describe for this exact image+prompt+budget.
    # The leader performs the call; followers wait on its Event (WITHOUT
    # holding _inflight_lock, or the leader's finally could never pop/set
    # and every waiter would deadlock) and then read the fresh cache entry.
    # A failed leader is re-attempted by the next follower so one bad
    # provider response does not silently drop every waiter.
    with _inflight_lock:
        _leader_ev = _inflight.get(cache_key)
        if _leader_ev is None:
            _leader_ev = threading.Event()
            _inflight[cache_key] = _leader_ev
            _is_leader = True
        else:
            _is_leader = False
    if not _is_leader:
        _leader_ev.wait()
        _cached2 = _cache_get(cache_key)
        if _cached2 is not None:
            return _cached2
        # Leader failed (no cache entry) — promote ourselves to leader.
        with _inflight_lock:
            _leader_ev = threading.Event()
            _inflight[cache_key] = _leader_ev

    def _do_describe():
        """Downscale + call the vision service; returns the description."""
        scaled_mime, scaled_b64 = _downscale_b64(mime, b64)
        full_prompt = prompt or DESCRIBE_PROMPT.format(
            dims=_image_dims(scaled_mime, scaled_b64))
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
                         "image_url": {"url": f"data:{scaled_mime};base64,{scaled_b64}"}},
                    ],
                    conversation_id=conversation_id or "vision_describe",
                )],
                model=model or None,
                temperature=None,
                max_tokens=max_tokens,
                call_user_id=user_id,
                call_conversation_id=conversation_id,
                call_agent_name=agent_name,
            )
        finally:
            _tls.active = _prev_active
        description = (getattr(response, "content", "") or "").strip()
        if not description:
            # Reasoning models (gpt-5.x, o-series) may put all output in
            # reasoning_content when max_tokens is too low. Use it as fallback.
            reasoning = (getattr(response, "thinking", "") or "").strip()
            if reasoning:
                description = reasoning
        if description:
            _cache_put(cache_key, description)
        return description

    try:
        return _do_describe()
    finally:
        # Always release waiters, including on exception, or a follower
        # would block forever on a failed leader.
        with _inflight_lock:
            _ev_owned = _inflight.pop(cache_key, None)
        if _ev_owned is not None:
            _ev_owned.set()


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
        logger.info("[vision-fallback] skipping: recursion guard active")
        return messages
    if not any(isinstance(getattr(m, "content", None), list) and any(
            isinstance(p, dict) and p.get("type") in ("image_ref", "image_url")
            for p in m.content) for m in messages):
        logger.info("[vision-fallback] skipping: no image_ref/image_url parts found")
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
    logger.info("[vision-fallback] proceeding: vision_svc=%s, describing images...",
                getattr(vision_svc, "_service_id", "") or type(vision_svc).__name__)

    # A — fenetre du tour courant : seules les images du message utilisateur
    # courant et des messages produits apres lui (tool results du tour) sont
    # decrites. Les images des messages PLUS ANCIENS ont deja ete decrites —
    # leur description est PERSISTEE dans le contexte (persistance B plus
    # bas) — donc les re-decrire re-soumettrait l'image au vision a chaque
    # tour : ca gonfle le prompt et, apres un redemarrage (cache froid),
    # refait des appels reseau pour rien. Elles sont remplacees par un
    # placeholder court SANS aucun appel vision.
    # Quand aucun message ne porte le marqueur de tour courant (poller,
    # synthese d'interruption, tests directs), tout est decrit comme avant :
    # un fallback sans fenetre ne doit jamais perdre d'information.
    _current_start = -1
    for _mi, _m in enumerate(messages):
        if getattr(_m, "_pawflow_current_user_message", False):
            _current_start = _mi
            break

    # Collect every image part to describe, honouring the per-pass budget.
    # The network calls run in parallel afterwards, so a message carrying
    # several images (video frames, multiple screenshots) is not serialized.
    jobs = []  # (msg_idx, part_idx, payload|placeholder)
    described = 0
    truncated = False
    for msg_idx, msg in enumerate(messages):
        content = getattr(msg, "content", None)
        if not (isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") in ("image_ref", "image_url")
                for p in content)):
            continue
        _is_history = _current_start >= 0 and msg_idx < _current_start
        for part_idx, part in enumerate(content):
            if not (isinstance(part, dict)
                    and part.get("type") in ("image_ref", "image_url")):
                continue
            if _is_history:
                # Tour precedent : deja decrit / description persistee — ne
                # JAMAIS re-soumettre cette image au vision.
                jobs.append((msg_idx, part_idx, "history"))
                continue
            if described >= _MAX_DESCRIBE_PER_PASS:
                truncated = True
                jobs.append((msg_idx, part_idx, None))
                continue
            payload = _part_payload(part, user_id=user_id,
                                    conversation_id=conversation_id)
            if not payload:
                described += 1
                jobs.append((msg_idx, part_idx, "unavailable"))
                continue
            mime, b64, label = payload
            jobs.append((msg_idx, part_idx, (mime, b64, label)))
            described += 1

    def _describe(job):
        """Describe one collected image; returns (job, outcome)."""
        msg_idx, part_idx, payload = job
        if payload == "history":
            # Ancien message du contexte — deja decrit, jamais re-soumis.
            return job, payload
        if payload is None or payload == "unavailable":
            return job, payload
        mime, b64, label = payload
        try:
            description = describe_image_b64(
                vision_svc, mime, b64, user_id=user_id,
                conversation_id=conversation_id, agent_name=agent_name)
        except Exception:
            logger.warning("vision fallback describe failed for %s",
                           label, exc_info=True)
            return job, ("error", label)
        if not description:
            return job, ("empty", label)
        return job, ("ok", label, description)

    results = {}
    to_describe = []
    for _j in jobs:
        if isinstance(_j[2], tuple):
            to_describe.append(_j)
        else:
            # Non-network outcomes ("history" placeholder, truncated None,
            # "unavailable") are resolved directly — the executor only maps
            # real describe payloads.
            results[(_j[0], _j[1])] = _j[2]
    with ThreadPoolExecutor(max_workers=min(4, len(to_describe) or 1)) as ex:
        for job, outcome in ex.map(_describe, to_describe):
            results[(job[0], job[1])] = outcome

    # Rebuild messages: replace image parts with their description (or a
    # placeholder) without mutating the stored originals.
    out: List[Any] = []
    _persist: Dict[int, List[tuple]] = {}  # msg_idx -> [(part_idx, label, desc)]
    for msg_idx, msg in enumerate(messages):
        content = getattr(msg, "content", None)
        if not (isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") in ("image_ref", "image_url")
                for p in content)):
            out.append(msg)
            continue
        new_parts: List[Dict[str, Any]] = []
        changed = False
        for part_idx, part in enumerate(content):
            if not (isinstance(part, dict)
                    and part.get("type") in ("image_ref", "image_url")):
                new_parts.append(part)
                continue
            outcome = results.get((msg_idx, part_idx), "unavailable")
            changed = True
            if outcome == "history":
                new_parts.append({
                    "type": "text",
                    "text": (
                        "[Image: previously described in context — see the "
                        "description above.]"
                    ),
                })
            elif outcome is None:
                new_parts.append({
                    "type": "text",
                    "text": (
                        "[Image: skipped — too many images in this pass; "
                        "image not described.]"
                    ),
                })
            elif outcome == "unavailable":
                new_parts.append({
                    "type": "text",
                    "text": (
                        "[Image: could not be loaded from tool result; "
                        "image data unavailable.]"
                    ),
                })
            elif outcome[0] == "error":
                new_parts.append({
                    "type": "text",
                    "text": (
                        f"[Image: {outcome[1]} — vision model was unavailable; "
                        f"image could not be described.]"
                    ),
                })
            elif outcome[0] == "empty":
                new_parts.append({
                    "type": "text",
                    "text": (
                        f"[Image: {outcome[1]} — vision model returned no "
                        f"description; image could not be described.]"
                    ),
                })
            else:
                _, label, description = outcome
                _persist.setdefault(msg_idx, []).append(
                    (part_idx, label, description))
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

    # B — persister la description dans le contexte : une image decrite est
    # remplacee par sa description PERSISTEE (attachment marque described),
    # donc elle ne sera JAMAIS re-soumise au vision aux tours suivants, ni
    # apres un redemarrage serveur (le cache memoire/disk disparait). L'UI
    # garde l'attachment (l'image reste affichee) ; le contexte LLM charge la
    # description a la place de l'image_ref (_content_with_attachment_refs).
    if _persist and conversation_id:
        try:
            _targets = []  # (msg_id, attachments)
            for _mi in sorted(_persist):
                _msg = messages[_mi]
                _mid = getattr(_msg, "msg_id", "") or ""
                if not _mid or getattr(_msg, "role", "") != "user":
                    continue
                if getattr(_msg, "_vision_persisted", False):
                    continue  # deja persiste ce tour — pas de re-ecriture I/O
                _descs = {pi: d for (pi, _l, d) in _persist[_mi]}
                _content_parts = getattr(_msg, "content", None)
                if not isinstance(_content_parts, list):
                    continue
                _new_atts = []
                for _pi, _p in enumerate(_content_parts):
                    if not isinstance(_p, dict):
                        continue
                    _pt = _p.get("type", "")
                    if _pt == "image_ref":
                        _new_atts.append({
                            "file_id": _p.get("file_id", ""),
                            "filename": _p.get("filename", "image"),
                            "mime_type": _p.get("mime_type", "image/png"),
                            "size": _p.get("size", 0),
                            "described": True,
                            "description": _descs.get(_pi, ""),
                        })
                    elif _pt == "file_ref":
                        _new_atts.append({
                            "file_id": _p.get("file_id", ""),
                            "filename": _p.get("filename", "file"),
                            "mime_type": _p.get("mime_type", "application/octet-stream"),
                            "size": _p.get("size", 0),
                        })
                if _new_atts:
                    _targets.append((_mid, _new_atts))
                    _msg._vision_persisted = True
            if _targets:
                from core.conversation_store import ConversationStore
                _store = ConversationStore.instance()
                for _mid, _atts in _targets:
                    _store.patch_message(
                        conversation_id, _mid, attachments=_atts)
                    logger.info(
                        "[vision-fallback] persisted description for msg_id=%s "
                        "(%d image attachment(s))",
                        _mid, len([a for a in _atts if a.get("described")]))
        except Exception:
            logger.debug(
                "vision fallback: description persistence failed", exc_info=True)

    if truncated:
        logger.warning(
            "vision fallback: more than %d images in one pass; extra images "
            "were left as links", _MAX_DESCRIBE_PER_PASS)
    if described:
        logger.info("vision fallback: described %d image(s) via '%s'",
                    described, vision_service_id)
    return out
