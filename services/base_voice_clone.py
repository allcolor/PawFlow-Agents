"""Base interface for voice-cloning TTS services.

Contract: clone_speak(text, reference_audio_url, **kwargs)
    -> {"audio_bytes": bytes, "content_type": str, "source_url": str}

Two provider paradigms are supported via the same method:

  A. **Voice-ID persistent** (ElevenLabs, PlayHT, Cartesia, Resemble)
     The provider hosts the cloned voice and returns an opaque voice_id.
     Providers implementing this paradigm MUST override `ensure_voice_id`
     to create/reuse a voice_id, and SHOULD override `delete_voice_id`
     so cascade cleanup can free the upstream quota slot.

  B. **Zero-shot** (Fish Audio, F5-TTS, CosyVoice, XTTS, OpenVoice)
     The reference audio is sent at every call. No state on provider side.
     `ensure_voice_id` returns "" (the default) — the cache layer stores
     the final rendered audio keyed on (ref_audio_hash, text, params).

Every provider still exposes a single public method `clone_speak` — the
paradigm is internal. The handler calls clone_speak() and lets the provider
decide whether to do a two-step flow behind the scenes.
"""

from abc import abstractmethod

from services.base_tts import BaseTTSService


class BaseVoiceCloneService(BaseTTSService):
    """Abstract base for all voice-cloning TTS services.

    Implementations MUST override `clone_speak`. They SHOULD override
    `ensure_voice_id` if the provider uses voice_id caching (paradigm A).
    """

    @abstractmethod
    def clone_speak(self,
                    text: str,
                    reference_audio_url: str,
                    reference_text: str = "",
                    language: str = "",
                    **kwargs) -> dict:
        """Synthesize `text` in the voice of `reference_audio_url`.

        Args:
            text: the text to speak.
            reference_audio_url: URL (http://... or fs://filestore/...) of
                the voice sample to clone. 10-30 s of clean speech is the
                usual sweet spot.
            reference_text: transcription of the reference audio. Required
                by some providers (Fish Audio) for best quality; optional
                on others.
            language: BCP-47 language tag (e.g. "fr", "en", "ja"). Provider
                may ignore if the model is language-agnostic.

        Returns:
            {
                "audio_bytes": bytes,
                "content_type": str,       # e.g. "audio/mpeg"
                "source_url": str,         # provider URL if any, else ""
                "voice_id": str (optional) # if the provider uses voice_id
            }
        """
        ...

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        """TTS-compatible alias for cloned-voice synthesis.

        ``voice`` maps to provider voice_id for persistent-voice providers.
        Zero-shot providers receive reference audio through kwargs, same as
        the existing ``clone_speak`` path.
        """
        if voice and "voice_id" not in kwargs:
            kwargs["voice_id"] = voice
        return self.clone_speak(text=text, language=language, **kwargs)

    def ensure_voice_id(self, reference_audio_url: str,
                        reference_text: str = "",
                        name: str = "",
                        **kwargs) -> str:
        """Optional: create or reuse a persistent voice_id on the provider.

        Providers in paradigm B (zero-shot) should leave this unimplemented
        (returns empty string). Providers in paradigm A (ElevenLabs, PlayHT,
        ...) override it to POST the sample once, cache the voice_id, and
        return it.

        Returns the voice_id (provider-opaque string) or "" if not applicable.
        """
        return ""

    def create_voice(self, name: str, reference_audio_url: str,
                     reference_text: str = "", language: str = "",
                     **kwargs) -> dict:
        """Create a provider voice when supported by the provider."""
        voice_id = self.ensure_voice_id(
            reference_audio_url=reference_audio_url,
            reference_text=reference_text,
            name=name,
            **kwargs,
        )
        return {"voice_id": voice_id or ""}

    def delete_voice_id(self, voice_id: str) -> bool:
        """Optional: delete a voice_id on the provider (cleanup cascade).

        Returns True if deleted (or never existed). Zero-shot providers
        default to True (nothing to delete).
        """
        return True

    def delete_voice(self, voice_id: str, **kwargs) -> bool:
        return self.delete_voice_id(voice_id)
