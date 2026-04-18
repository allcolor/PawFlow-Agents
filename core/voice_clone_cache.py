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
import time
from typing import Any, Dict, List, Optional

from core.repository import ScopedRepository, SCOPE_USER

logger = logging.getLogger(__name__)

_RTYPE = "voice_clones"

# Accept letters, digits, dash, underscore — map everything else to "_".
_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def safe_name(name: str) -> str:
    """Return a filesystem-safe version of a user-supplied voice name."""
    cleaned = _NAME_RE.sub("_", (name or "").strip())
    cleaned = cleaned.strip("_") or "voice"
    return cleaned[:80]


def hash_audio(audio_bytes: bytes) -> str:
    """Compute a stable content hash for a reference audio blob.

    Currently SHA-256 of the raw bytes. Normalisation to PCM 16 kHz mono
    is a nice-to-have future optimisation — it would raise cache hit rate
    across different encodings of the same source — but the raw hash is
    already correct for the common case (same file uploaded twice).
    """
    return hashlib.sha256(audio_bytes).hexdigest()


def find_by_hash(user_id: str, provider: str,
                 ref_audio_hash: str) -> Optional[Dict[str, Any]]:
    """Find an existing voice clone for this (user, provider, audio hash).

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
              content_type: str = "audio/mpeg") -> str:
    """Cache a rendered TTS audio file. Returns the FileStore file_id."""
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
    # Tag the entry with the cache key so `tts_find` can lookup by key.
    try:
        with store._store_lock:
            store._ensure_loaded()
            e = store._entries.get(file_id)
            if e is not None:
                e["voice_cache_key"] = cache_key
        store._save_index()
    except Exception as e:
        logger.debug("voice_clone_cache.tts_store tag: %s", e)
    return file_id
