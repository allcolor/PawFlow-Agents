"""Voice-clone cache layer.

Stores user-visible voice clone entries as `voice_clones` resources in
the scoped repository (scope=user), and optionally caches rendered TTS
audio in the FileStore to make idempotent re-renders free.

Data model for a voice_clones entry:
{
    "name":                  str   # user-friendly, unique per user scope
    "provider":              str   # "fish_audio", "elevenlabs", ...
    "provider_version":      str   # "v1" — bump on API contract change
    "voice_id":              str   # provider-opaque; "" for zero-shot
    "ref_audio_hash":        str   # sha256 hex of raw reference audio bytes
    "ref_audio_fid":         str   # FileStore file_id of the ref audio
    "ref_audio_filename":    str
    "ref_audio_content_type":str
    "ref_audio_size":        int
    "reference_text":        str   # transcription of the sample (zero-shot)
    "language":              str
    "preview_fid":           str   # optional short preview audio
    "created_at":            float
    "last_used_at":          float
}
"""

import hashlib
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from core.repository import ScopedRepository, SCOPE_USER

logger = logging.getLogger(__name__)

_RTYPE = "voice_clones"

# Detect ffmpeg once at import — used for content-stable hashing.
_FFMPEG = shutil.which("ffmpeg")

# Accept letters, digits, dash, underscore — map everything else to "_".
_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def safe_name(name: str) -> str:
    """Return a filesystem-safe version of a user-supplied voice name."""
    cleaned = _NAME_RE.sub("_", (name or "").strip())
    cleaned = cleaned.strip("_") or "voice"
    return cleaned[:80]


def _normalize_pcm(audio_bytes: bytes) -> Optional[bytes]:
    """Decode audio to PCM s16le 16 kHz mono via ffmpeg.

    Returns the raw PCM bytes or None if ffmpeg is unavailable or the
    input cannot be decoded (test payloads, non-audio blobs). Callers
    MUST treat None as "fall back to raw hashing".
    """
    if not _FFMPEG or not audio_bytes:
        return None
    try:
        proc = subprocess.run(
            [_FFMPEG, "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0",
             "-f", "s16le", "-ac", "1", "-ar", "16000",
             "pipe:1"],
            input=audio_bytes, capture_output=True, timeout=30, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("voice_clone_cache._normalize_pcm: %s", e)
        return None


def hash_audio(audio_bytes: bytes) -> str:
    """Compute a stable content hash for a reference audio blob.

    When ffmpeg is available and the payload is decodable, the bytes are
    first decoded to PCM s16le 16 kHz mono so that different encodings of
    the same source (same sample saved at another bitrate, ID3 tags
    changed, …) hash to the same value. This lifts cache hit-rate
    dramatically in practice.

    If ffmpeg is absent, or the payload cannot be decoded (tests, raw
    blobs), we fall back to SHA-256 of the raw bytes. Behaviour is
    deterministic within a given deployment — deploy a server that has
    ffmpeg installed (the relay-dev image already does).
    """
    pcm = _normalize_pcm(audio_bytes)
    if pcm is not None:
        return hashlib.sha256(pcm).hexdigest()
    return hashlib.sha256(audio_bytes).hexdigest()


def find_by_hash(user_id: str, provider: str,
                 ref_audio_hash: str,
                 provider_version: str = "") -> Optional[Dict[str, Any]]:
    """Find an existing voice clone for this (user, provider, audio hash).

    When ``provider_version`` is non-empty, entries whose stored version
    does not match are treated as stale (different API contract) and
    skipped — the handler will re-clone against the current provider
    version. When ``provider_version`` is empty, the check is disabled
    (callers that don't care about versioning still hit the cache).

    Returns the entry dict or None.
    """
    if not user_id or not provider or not ref_audio_hash:
        return None
    repo = ScopedRepository.instance()
    for entry in repo.list(_RTYPE, SCOPE_USER, user_id=user_id):
        if entry.get("provider") != provider:
            continue
        if entry.get("ref_audio_hash") != ref_audio_hash:
            continue
        if provider_version and entry.get("provider_version") != provider_version:
            continue
        return entry
    return None


def get_by_name(user_id: str, name: str) -> Optional[Dict[str, Any]]:
    """Fetch a voice clone entry by its name in the user scope."""
    if not user_id or not name:
        return None
    return ScopedRepository.instance().get(
        _RTYPE, safe_name(name), SCOPE_USER, user_id=user_id)


def list_for_user(user_id: str) -> List[Dict[str, Any]]:
    """Return every voice clone owned by this user."""
    if not user_id:
        return []
    return ScopedRepository.instance().list(
        _RTYPE, SCOPE_USER, user_id=user_id)


def save(user_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a voice clone entry in user scope.

    `entry` must contain `name`. Other fields are persisted as-is.
    """
    if not user_id:
        raise ValueError("voice_clone_cache.save: user_id is required")
    name = safe_name(entry.get("name") or "")
    if not name:
        raise ValueError("voice_clone_cache.save: name is required")
    entry = dict(entry)
    entry["name"] = name
    entry.setdefault("created_at", time.time())
    entry["last_used_at"] = time.time()

    repo = ScopedRepository.instance()
    existing = repo.get(_RTYPE, name, SCOPE_USER, user_id=user_id)
    if existing is None:
        return repo.create(_RTYPE, name, SCOPE_USER, entry, user_id=user_id)
    return repo.update(_RTYPE, name, SCOPE_USER, entry, user_id=user_id)


def delete(user_id: str, name: str) -> bool:
    """Delete a voice clone entry from user scope. Returns True if removed."""
    if not user_id or not name:
        return False
    return ScopedRepository.instance().delete(
        _RTYPE, safe_name(name), SCOPE_USER, user_id=user_id)


def touch(user_id: str, name: str) -> None:
    """Update `last_used_at` for an entry without changing other fields."""
    if not user_id or not name:
        return
    repo = ScopedRepository.instance()
    existing = repo.get(_RTYPE, safe_name(name), SCOPE_USER, user_id=user_id)
    if existing is None:
        return
    try:
        repo.update(_RTYPE, safe_name(name), SCOPE_USER,
                    {"last_used_at": time.time()}, user_id=user_id)
    except KeyError:
        pass


# ---- Rendered TTS cache (final audio per (voice, text, lang)) ---------

_TTS_CATEGORY = "voice_clone_tts"


def tts_cache_key(ref_audio_hash: str, text: str, language: str = "",
                  provider: str = "") -> str:
    """Compute a stable hash for a rendered TTS result."""
    h = hashlib.sha256()
    h.update(provider.encode("utf-8"))
    h.update(b"|")
    h.update(ref_audio_hash.encode("utf-8"))
    h.update(b"|")
    h.update(language.encode("utf-8"))
    h.update(b"|")
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()


def tts_find(user_id: str, conversation_id: str, cache_key: str) -> Optional[str]:
    """Look up a cached rendered TTS audio file for this user.

    Returns the FileStore file_id or None. Only entries owned by this user
    are considered. conversation_id is only used to let FileStore's access
    check pass — the hit does not need to be in the current conversation.
    """
    if not user_id or not cache_key:
        return None
    try:
        from core.file_store import FileStore
        store = FileStore.instance()
        for entry in store.list_by_category(_TTS_CATEGORY):
            if entry.get("user_id") != user_id:
                continue
            if entry.get("voice_cache_key") == cache_key:
                # list_by_category returns 'id', not 'file_id'
                return entry.get("id") or entry.get("file_id")
    except Exception as e:
        logger.debug("voice_clone_cache.tts_find: %s", e)
    return None


def tts_store(user_id: str, conversation_id: str,
              cache_key: str,
              filename: str, audio_bytes: bytes,
              content_type: str = "audio/mpeg",
              ref_audio_hash: str = "") -> str:
    """Cache a rendered TTS audio file. Returns the FileStore file_id.

    `ref_audio_hash` is stored alongside so `cascade_delete` can purge
    every rendered output produced for this voice when the user removes
    the voice clone resource.
    """
    if not user_id or not conversation_id:
        raise ValueError("tts_store: user_id and conversation_id required")
    from core.file_store import FileStore
    store = FileStore.instance()
    file_id = store.store(
        filename=filename,
        content=audio_bytes,
        content_type=content_type,
        conversation_id=conversation_id,
        user_id=user_id,
        ttl=0,
        category=_TTS_CATEGORY,
    )
    # Tag the entry with the cache key so `tts_find` can lookup by key,
    # and with the ref-audio hash so `cascade_delete` can find siblings.
    try:
        _tag_tts_entry(store, file_id, cache_key, ref_audio_hash)
    except Exception as e:
        logger.debug("voice_clone_cache.tts_store tag: %s", e)
    return file_id


def tts_store_file(user_id: str, conversation_id: str,
                   cache_key: str,
                   filename: str, source_path: str,
                   content_type: str = "audio/mpeg",
                   ref_audio_hash: str = "") -> str:
    """Cache a rendered TTS audio file from a local path. Returns file_id."""
    if not user_id or not conversation_id:
        raise ValueError("tts_store_file: user_id and conversation_id required")
    from core.file_store import FileStore
    store = FileStore.instance()
    file_id = store.store_file(
        filename=filename,
        source_path=source_path,
        content_type=content_type,
        conversation_id=conversation_id,
        user_id=user_id,
        ttl=0,
        category=_TTS_CATEGORY,
    )
    try:
        _tag_tts_entry(store, file_id, cache_key, ref_audio_hash)
    except Exception as e:
        logger.debug("voice_clone_cache.tts_store_file tag: %s", e)
    return file_id


def _tag_tts_entry(store, file_id: str, cache_key: str,
                   ref_audio_hash: str = "") -> None:
    with store._store_lock:
        store._ensure_loaded()
        e = store._entries.get(file_id)
        if e is not None:
            e["voice_cache_key"] = cache_key
            if ref_audio_hash:
                e["voice_ref_hash"] = ref_audio_hash
    store._save_index()


def _purge_ref_audio(user_id: str, file_id: str) -> bool:
    """Delete the reference-audio FileStore entry owned by `user_id`."""
    if not user_id or not file_id:
        return False
    try:
        from core.file_store import FileStore
        return FileStore.instance().delete(file_id, user_id=user_id)
    except Exception as e:
        logger.debug("voice_clone_cache._purge_ref_audio: %s", e)
        return False


def _purge_tts_cache(user_id: str, ref_audio_hash: str) -> int:
    """Drop every rendered-TTS cache entry keyed on this voice's hash.

    The TTS cache key is derived from (provider, ref_audio_hash, language,
    text). We cannot match on the hash alone — but we CAN match on a
    prefix stored alongside the file's metadata (voice_cache_key). Since
    keys are opaque SHA-256 hexes we instead recompute and match via the
    auxiliary tag `voice_ref_hash` written below.
    Returns the number of files deleted.
    """
    if not user_id or not ref_audio_hash:
        return 0
    try:
        from core.file_store import FileStore
        store = FileStore.instance()
        victims: List[str] = []
        for entry in store.list_by_category(_TTS_CATEGORY):
            if entry.get("user_id") != user_id:
                continue
            if entry.get("voice_ref_hash") != ref_audio_hash:
                continue
            fid = entry.get("id") or entry.get("file_id")
            if fid:
                victims.append(fid)
        for fid in victims:
            store.delete(fid, user_id=user_id)
        return len(victims)
    except Exception as e:
        logger.debug("voice_clone_cache._purge_tts_cache: %s", e)
        return 0


def cascade_delete(user_id: str, name: str, service) -> Dict[str, Any]:
    """Delete a voice clone fully: provider + ref audio + TTS cache + entry.

    `service` is the active BaseVoiceCloneService instance (or None). When
    the entry was registered with a persistent `voice_id` (paradigm A),
    `service.delete_voice_id` is called so the quota is freed upstream.

    Returns a dict summarising what was removed:
        {"entry": bool, "voice_id": bool, "ref_audio": bool,
         "tts_cached": int}
    """
    result = {"entry": False, "voice_id": False,
              "ref_audio": False, "tts_cached": 0}
    entry = get_by_name(user_id, name)
    if entry is None:
        return result

    raw_voice_id = entry.get("voice_id") or ""
    if isinstance(raw_voice_id, dict):
        voice_id = str(raw_voice_id.get("voice_id") or raw_voice_id.get("id") or "")
    else:
        voice_id = str(raw_voice_id or "")
    if voice_id and service is not None:
        try:
            ok = bool(service.delete_voice_id(voice_id))
        except Exception as e:
            logger.warning(
                "cascade_delete: provider delete_voice_id(%s) failed: %s",
                voice_id, e)
            ok = False
        result["voice_id"] = ok

    ref_fid = entry.get("ref_audio_fid") or ""
    if ref_fid:
        result["ref_audio"] = _purge_ref_audio(user_id, ref_fid)

    ref_hash = entry.get("ref_audio_hash") or ""
    if ref_hash:
        result["tts_cached"] = _purge_tts_cache(user_id, ref_hash)

    result["entry"] = delete(user_id, name)
    return result
