"""Capability tool handlers (provider-backed media/voice operations).

The shared base + helpers live in _capability_base.py and the image/3D handler
group in _capability_handlers.py (split to keep files <=800 lines). This module
keeps the voice/video handlers and re-exports the full set so the
core.handlers.capabilities import path is unchanged.
"""

import logging
import os
import time
from typing import Any, Dict


from core.handlers._capability_base import (  # noqa: F401
    _INTERNAL_TTS_ARG_NAMES,
    _SERVICE_ARG_NAMES,
    _CapabilityHandlerBase,
    _provider_identity,
    _provider_version,
    _voice_id_value,
)
from core.handlers._capability_3d_handlers import (  # noqa: F401
    Animate3DModelHandler,
    Retexture3DModelHandler,
    Rig3DModelHandler,
)
from core.handlers._capability_handlers import (  # noqa: F401
    DescribeImageHandler,
    Generate3DHandler,
    LipsyncHandler,
    RemixImageHandler,
    RemoveBackgroundHandler,
    TrainImageModelHandler,
    TryOnHandler,
    UpscaleImageHandler,
    UpscaleVideoHandler,
)

logger = logging.getLogger(__name__)


class SpeechToVideoHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "speech_to_video"

    @property
    def description(self) -> str:
        return (
            "Generate a lip-synced video from a face image and audio track "
            "(Wan 2.2 Speech-to-Video). The output video shows the person "
            "speaking/singing along with the audio. Requires `image_url` "
            "(face photo) and `audio_url` (speech/music)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Style/scene description (e.g. 'beach vacation, man in sunglasses')"},
                "image_url": {"type": "string", "description": "Face image URL (HTTP or fs://filestore/<id>/<name>)"},
                "audio_url": {"type": "string", "description": "Audio track URL (speech or music)"},
                "resolution": {"type": "string", "description": "Output resolution: '480p', '580p', '720p' (default '480p')"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override model (default 'wan2.2-s2v')."},
            },
            "required": ["image_url", "audio_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no video service available'}"
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        audio_url = self._rewrite(arguments.get("audio_url", "") or "", service=svc)
        if not image_url or not audio_url:
            return "Error: `image_url` and `audio_url` are required"
        if not hasattr(svc, 'speech_to_video'):
            return "Error: the active video service does not support speech_to_video"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "image_url", "audio_url", *_SERVICE_ARG_NAMES)}
            r = svc.speech_to_video(image_url=image_url, audio_url=audio_url, **kwargs)
            filename = arguments.get("path") or f"s2v_{int(time.time())}.mp4"
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Speech-to-video generated")
        except Exception as e:
            return f"Error generating speech-to-video: {e}"


# ── Voice Clone ─────────────────────────────────────────────────────


class CloneVoiceHandler(_CapabilityHandlerBase):
    """Create (or reuse) a voice clone resource from a reference audio sample.

    The resulting voice is stored as a `voice_clones` resource in the user
    repository. Subsequent calls with the same reference audio return the
    cached entry without re-contacting the provider.

    Zero-shot providers (Fish Audio) just keep a pointer to the reference
    audio; persistent-voice_id providers (ElevenLabs, PlayHT) would POST
    the sample once and cache the returned voice_id here.
    """

    @property
    def name(self) -> str:
        return "clone_voice"

    @property
    def description(self) -> str:
        return (
            "Register a voice clone from a reference audio sample. Returns "
            "a `name` the agent can pass to `speak` to synthesize text in "
            "that voice. The same reference audio is detected automatically "
            "(hash-based) and the existing clone is reused — no duplicate "
            "creation, no extra cost. 10-30 s of clean speech is the "
            "recommended sample length. The optional `reference_text` "
            "(transcription of the sample) improves synthesis quality on "
            "zero-shot providers. The user must have the right to clone "
            "the voice in the sample."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User-friendly name for this voice clone (unique per user). Letters, digits, '-' and '_' are kept; other characters become '_'."},
                "reference_audio_url": {"type": "string", "description": "Reference voice sample (HTTP URL or fs://filestore/<id>/<name>). 10-30 s of clean speech recommended."},
                "reference_text": {"type": "string", "description": "Transcription of the reference audio. Optional; improves quality on zero-shot providers (Fish Audio)."},
                "language": {"type": "string", "description": "BCP-47 language tag of the sample (e.g. 'fr', 'en')."},
            },
            "required": ["name", "reference_audio_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no voice-clone service available'}"

        name = (arguments.get("name") or "").strip()
        ref_url_raw = arguments.get("reference_audio_url") or ""
        ref_url = self._rewrite(ref_url_raw, service=svc)
        if not name or not ref_url:
            return ("Error: `name` and `reference_audio_url` are required")

        reference_text = arguments.get("reference_text") or ""
        language = arguments.get("language") or ""

        from core import voice_clone_cache as _cache

        provider = _provider_identity(svc)
        provider_version = _provider_version(svc)

        # Download reference audio so we can hash it.
        try:
            import urllib.request
            req = urllib.request.Request(
                ref_url, headers={"User-Agent": "PawFlow-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - reference audio URLs are user-provided HTTP(S) media inputs.
                ref_bytes = resp.read()
                ref_ct = resp.headers.get("Content-Type",
                                           "application/octet-stream")
        except Exception as e:
            return f"Error downloading reference_audio_url: {e}"

        if not ref_bytes:
            return "Error: reference_audio_url returned empty content"

        ref_hash = _cache.hash_audio(ref_bytes)

        # Already cloned with this exact audio on this provider?
        # Pass `provider_version` so entries from an older API contract
        # are treated as stale and re-cloned against the current version.
        existing = _cache.find_by_hash(
            self._user_id, provider, ref_hash, provider_version)
        if existing:
            _cache.touch(self._user_id, existing.get("name", name))
            return (f"Voice clone already exists: name={existing['name']} "
                    f"provider={provider} (cached, no cost). Use with "
                    f"`speak(voice='{existing['name']}', text=...)`.")

        # Store ref audio in FileStore for future synth calls.
        try:
            from core.file_store import FileStore
            store = FileStore.instance()
            filename = ref_url_raw.rstrip("/").split("/")[-1] or "reference.mp3"
            ref_fid = store.store(
                filename=filename,
                content=ref_bytes,
                content_type=ref_ct,
                conversation_id=self._conversation_id or "_voice_cache",
                user_id=self._user_id,
                ttl=0,
                category="voice_clone_ref",
            )
        except Exception as e:
            return f"Error storing reference audio in FileStore: {e}"

        # Paradigm A providers (ElevenLabs, ...) create a voice_id up-front.
        voice_id = ""
        if hasattr(svc, "ensure_voice_id"):
            try:
                voice_id = _voice_id_value(svc.ensure_voice_id(
                    reference_audio_url=ref_url,
                    reference_text=reference_text,
                    name=name,
                    reference_audio_bytes=ref_bytes,
                ))
            except Exception as e:
                logger.warning(
                    "ensure_voice_id failed on %s: %s — falling back to "
                    "stateless mode", provider, e)
                voice_id = ""

        entry = {
            "name": name,
            "provider": provider,
            "provider_version": provider_version,
            "voice_id": voice_id,
            "ref_audio_hash": ref_hash,
            "ref_audio_fid": ref_fid,
            "ref_audio_filename": filename,
            "ref_audio_content_type": ref_ct,
            "ref_audio_size": len(ref_bytes),
            "reference_text": reference_text,
            "language": language,
        }
        try:
            saved = _cache.save(self._user_id, entry)
        except Exception as e:
            return f"Error persisting voice clone: {e}"

        _msg = (f"Voice clone registered: name={saved['name']} "
                f"provider={provider} paradigm="
                f"{'voice_id' if voice_id else 'zero-shot'}. "
                f"Use `speak(voice='{saved['name']}', text=...)` to "
                f"synthesize speech in this voice.")
        return _msg


class SpeakHandler(_CapabilityHandlerBase):
    """Synthesize text with a registered voice clone, returning an audio URL.

    The agent addresses a voice clone by its user-friendly `voice` name
    (registered earlier via `clone_voice`). The result URL can be passed
    directly to `lipsync` or `speech_to_video`.

    A content-addressed FileStore cache is consulted first:
    (ref_audio_hash, text, language, provider) → cached mp3 avoids
    re-calling the provider for identical re-renders.
    """

    @property
    def name(self) -> str:
        return "speak"

    @property
    def description(self) -> str:
        return (
            "Synthesize speech through the active TTS provider. `voice` may "
            "be either a PawFlow voice registered by `clone_voice` or a "
            "provider-native voice name/id such as Supertonic F1, WaveSpeed "
            "Vivian, or an ElevenLabs voice id. Returns a downloadable audio "
            "URL suitable for `lipsync` / `speech_to_video`. Identical inputs "
            "hit a cache and skip the provider call entirely."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "voice": {"type": "string", "description": "PawFlow voice alias registered via `clone_voice`, or provider-native voice name/id."},
                "text": {"type": "string", "description": "Text to synthesize."},
                "language": {"type": "string", "description": "BCP-47 language tag. Provider may ignore if irrelevant."},
                "service": {"type": "string", "description": "Optional TTS service id override."},
                "audio_service": {"type": "string", "description": "Alias for service; optional TTS service id override."},
                "voice_service": {"type": "string", "description": "Alias for service; optional TTS service id override."},
                "model": {"type": "string", "description": "Provider model override when supported."},
                "destination": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["text"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no voice-clone service available'}"

        voice = (arguments.get("voice") or "").strip()
        text = arguments.get("text") or ""
        language = arguments.get("language") or ""
        if not text:
            return "Error: `text` is required"

        from core import voice_clone_cache as _cache
        storage_ttl = self._storage_ttl(arguments)

        provider = _provider_identity(svc)
        entry = _cache.get_by_name(self._user_id, voice) if voice else None
        if entry and entry.get("provider") and entry["provider"] != provider:
            return (f"Error: voice clone {voice!r} was created with provider "
                    f"{entry['provider']}, but the active voice-clone service "
                    f"is {provider}. Switch services to re-use this voice.")

        if not entry:
            # Zero-shot voice-clone providers require a registered PawFlow
            # voice because every synthesis needs reference audio. Providers
            # with native voice ids/names opt in via SUPPORTS_NATIVE_TTS_VOICES,
            # and audio TTS providers expose text_to_speech/speak directly.
            is_clone_provider = hasattr(svc, "clone_speak")
            supports_native = bool(getattr(svc, "SUPPORTS_NATIVE_TTS_VOICES", False))
            has_direct_tts = hasattr(svc, "text_to_speech") or not is_clone_provider
            if voice and is_clone_provider and not supports_native and not has_direct_tts:
                return (f"Error: unknown voice clone {voice!r}. Register it first "
                        f"with `clone_voice`, or select a TTS provider that "
                        f"supports provider-native voices.")
            return self._speak_native_voice(
                svc, provider, voice, text, language, arguments, _cache)

        ref_hash = entry.get("ref_audio_hash") or ""
        cache_key = _cache.tts_cache_key(
            ref_hash, text, language=language, provider=provider)

        # Cache hit — return existing FileStore entry.
        cached_fid = _cache.tts_find(
            self._user_id, self._conversation_id, cache_key,
            include_transient=storage_ttl > 0)
        if cached_fid:
            from core.file_store import FileStore
            meta = FileStore.instance().get_metadata(cached_fid) or {}
            filename = meta.get("filename", f"{voice}.mp3")
            url = f"fs://filestore/{cached_fid}/{filename}"
            _cache.touch(self._user_id, voice)
            return (f"Speech synthesized (cached): {url}\n"
                    f"file_id: {cached_fid}")

        # Cache miss — fetch reference bytes and call the provider.
        ref_fid = entry.get("ref_audio_fid") or ""
        ref_bytes = b""
        if ref_fid:
            try:
                from core.file_store import FileStore
                triple = FileStore.instance().get_required(
                    ref_fid, self._user_id,
                    self._conversation_id or "_voice_cache")
                _, ref_bytes, _ = triple
            except Exception as e:
                logger.warning(
                    "speak: cannot read ref audio %s (%s) — falling back "
                    "to URL fetch", ref_fid, e)
                ref_bytes = b""

        try:
            kwargs = {}
            if entry.get("voice_id"):
                kwargs["voice_id"] = _voice_id_value(entry["voice_id"])
            # Rebuild a usable URL when the service cannot use cached bytes
            # directly (for example WaveSpeedAI prediction inputs expect a
            # URL string, not a raw/base64 sample in this call path).
            ref_url = ""
            if (not ref_bytes or getattr(svc, "REQUIRES_REFERENCE_AUDIO_URL", False)) and ref_fid:
                # Share the reference sample publicly for the duration of
                # this call (revoked when execute() returns).
                ref_url = self._rewrite(f"fs://filestore/{ref_fid}", service=svc)
            r = svc.clone_speak(
                text=text,
                reference_audio_url=ref_url,
                reference_text=entry.get("reference_text") or "",
                language=language,
                reference_audio_bytes=ref_bytes or None,
                **kwargs,
            )
        except Exception as e:
            # Paradigm A: if the provider reports the voice_id is gone
            # (404 / 410 / not_found), our cached entry is stale. Cascade-
            # delete local state and ask the caller to re-register so the
            # next `speak` call doesn't hit the same dead voice_id.
            msg = str(e)
            stale = (
                entry.get("voice_id")
                and (" 404" in msg or " 410" in msg
                     or "not_found" in msg.lower()
                     or "voice_not_found" in msg.lower()))
            if stale:
                try:
                    _cache.cascade_delete(self._user_id, voice, service=None)
                except Exception as de:
                    logger.warning(
                        "speak: cascade_delete after stale voice_id failed: %s",
                        de)
                return (
                    f"Error: voice clone {voice!r} no longer exists on "
                    f"provider {provider} (got: {e}). Local entry purged "
                    f"\u2014 re-register via `clone_voice` before calling "
                    f"`speak` again.")
            return f"Error synthesizing speech: {e}"

        audio_bytes = r.get("audio_bytes") or r.get("bytes") or b""
        audio_path = r.get("audio_path") or r.get("path") or ""
        if not audio_bytes and not audio_path:
            return "Error: provider returned no audio"
        content_type = r.get("content_type", "audio/mpeg")
        ext = {
            "audio/mpeg": "mp3", "audio/mp3": "mp3",
            "audio/wav": "wav", "audio/x-wav": "wav",
            "audio/ogg": "ogg", "audio/L16": "pcm",
        }.get(content_type.split(";")[0].strip(), "mp3")
        filename = arguments.get("path") or (
            f"{voice}_{int(time.time())}.{ext}")

        # Store the rendered audio in FileStore + index it in the TTS cache.
        conv = self._conversation_id or "_voice_cache"
        try:
            if audio_path:
                try:
                    fid = _cache.tts_store_file(
                        user_id=self._user_id,
                        conversation_id=conv,
                        cache_key=cache_key,
                        filename=filename,
                        source_path=str(audio_path),
                        content_type=content_type,
                        ref_audio_hash=ref_hash,
                        ttl=storage_ttl,
                    )
                finally:
                    if r.get("_delete_media_path"):
                        try:
                            os.unlink(str(audio_path))
                        except OSError:
                            pass
            else:
                fid = _cache.tts_store(
                    user_id=self._user_id,
                    conversation_id=conv,
                    cache_key=cache_key,
                    filename=filename,
                    audio_bytes=audio_bytes,
                    content_type=content_type,
                    ref_audio_hash=ref_hash,
                    ttl=storage_ttl,
                )
        except Exception as e:
            return f"Error storing synthesized audio: {e}"

        _cache.touch(self._user_id, voice)
        url = f"fs://filestore/{fid}/{filename}"
        return f"Speech synthesized: {url}\nfile_id: {fid}"

    @staticmethod
    def _storage_ttl(arguments: Dict[str, Any]) -> int:
        try:
            return max(0, int(arguments.get("_tts_storage_ttl") or 0))
        except (TypeError, ValueError):
            return 0

    def _speak_native_voice(self, svc, provider: str, voice: str, text: str,
                            language: str, arguments: Dict[str, Any],
                            cache) -> str:
        """Synthesize with a provider-native voice through BaseTTSService."""
        if not hasattr(svc, "speak"):
            return f"Error: provider {provider} does not support speak()"

        import hashlib
        import json

        kwargs = {k: v for k, v in arguments.items()
                  if k not in ("destination", "path", "text", "voice",
                               "language", *_SERVICE_ARG_NAMES,
                               *_INTERNAL_TTS_ARG_NAMES)}
        storage_ttl = self._storage_ttl(arguments)
        provider_version = _provider_version(svc)
        sig = json.dumps({
            "provider": provider,
            "version": provider_version,
            "voice": voice,
            "params": kwargs,
        }, sort_keys=True, default=str)
        voice_key = hashlib.sha256(sig.encode("utf-8")).hexdigest()
        cache_key = cache.tts_cache_key(
            voice_key, text, language=language, provider=provider)
        cached_fid = cache.tts_find(
            self._user_id, self._conversation_id, cache_key,
            include_transient=storage_ttl > 0)
        if cached_fid:
            from core.file_store import FileStore
            meta = FileStore.instance().get_metadata(cached_fid) or {}
            filename = meta.get("filename", "speech.mp3")
            url = f"fs://filestore/{cached_fid}/{filename}"
            return (f"Speech synthesized (cached): {url}\n"
                    f"file_id: {cached_fid}")

        try:
            r = svc.speak(text=text, voice=voice, language=language, **kwargs)
        except Exception as e:
            return f"Error synthesizing speech: {e}"

        audio_bytes = r.get("audio_bytes") or r.get("bytes") or b""
        audio_path = r.get("audio_path") or r.get("path") or ""
        if not audio_bytes and not audio_path:
            return "Error: provider returned no audio"
        content_type = r.get("content_type", "audio/mpeg")
        ext = {
            "audio/mpeg": "mp3", "audio/mp3": "mp3",
            "audio/wav": "wav", "audio/x-wav": "wav",
            "audio/ogg": "ogg", "audio/L16": "pcm",
            "audio/flac": "flac",
        }.get(content_type.split(";")[0].strip(), "mp3")
        safe_voice = "".join(c if c.isalnum() or c in ("-", "_") else "_"
                              for c in (voice or "speech"))[:40] or "speech"
        filename = arguments.get("path") or f"{safe_voice}_{int(time.time())}.{ext}"
        conv = self._conversation_id or "_voice_cache"
        try:
            if audio_path:
                try:
                    fid = cache.tts_store_file(
                        user_id=self._user_id,
                        conversation_id=conv,
                        cache_key=cache_key,
                        filename=filename,
                        source_path=str(audio_path),
                        content_type=content_type,
                        ref_audio_hash="",
                        ttl=storage_ttl,
                    )
                finally:
                    if r.get("_delete_media_path"):
                        try:
                            os.unlink(str(audio_path))
                        except OSError:
                            pass
            else:
                fid = cache.tts_store(
                    user_id=self._user_id,
                    conversation_id=conv,
                    cache_key=cache_key,
                    filename=filename,
                    audio_bytes=audio_bytes,
                    content_type=content_type,
                    ref_audio_hash="",
                    ttl=storage_ttl,
                )
        except Exception as e:
            return f"Error storing synthesized audio: {e}"
        url = f"fs://filestore/{fid}/{filename}"
        return f"Speech synthesized: {url}\nfile_id: {fid}"


class DeleteVoiceHandler(_CapabilityHandlerBase):
    """Delete a previously registered voice clone — full cascade.

    Removes the user's `voice_clones` entry, the stored reference-audio
    file, every rendered TTS audio that was cached for this voice, and
    (for paradigm-A providers such as ElevenLabs) the voice_id on the
    provider itself so quota is freed upstream.
    """

    @property
    def name(self) -> str:
        return "delete_voice"

    @property
    def description(self) -> str:
        return (
            "Delete a voice clone registered via `clone_voice`. Frees the "
            "provider voice_id (ElevenLabs et al.), removes the stored "
            "reference audio sample and drops every cached synthesis "
            "rendered from this voice. Irreversible."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "voice": {"type": "string", "description": "Name of the voice clone to delete (as returned by `clone_voice`)."},
            },
            "required": ["voice"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        voice = (arguments.get("voice") or "").strip()
        if not voice:
            return "Error: `voice` is required"
        from core import voice_clone_cache as _cache
        # The service may not be configured anymore — we still delete
        # local state, only the provider cleanup step is skipped.
        svc, _ = self._get_service(arguments)
        entry = _cache.get_by_name(self._user_id, voice)
        if entry is None:
            return f"Error: unknown voice clone {voice!r}"
        result = _cache.cascade_delete(self._user_id, voice, svc)
        parts = [f"Voice clone {voice!r} deleted."]
        if result["voice_id"]:
            parts.append("Provider voice_id freed.")
        if result["ref_audio"]:
            parts.append("Reference audio removed.")
        if result["tts_cached"]:
            parts.append(f"{result['tts_cached']} cached rendering(s) purged.")
        return " ".join(parts)
