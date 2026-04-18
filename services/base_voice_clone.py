"""Base interface for voice-cloning TTS services.

Contract: clone_speak(text, reference_audio_url, **kwargs)
    -> {"audio_bytes": bytes, "content_type": str, "source_url": str}

Two provider paradigms are supported via the same method:

  A. **Zero-shot** (Fish Audio, F5-TTS, CosyVoice, XTTS, OpenVoice)
     The reference audio is sent at every call. No state on provider side.
     Cache layer stores the final rendered audio keyed on
     (ref_audio_hash, text, params).

  B. **Voice-ID persistent** (ElevenLabs, PlayHT, Cartesia, Resemble)
     The provider hosts the cloned voice and returns an opaque voice_id.
     Providers implementing this paradigm should override `ensure_voice_id`
     to create/reuse a voice_id, and store it via the cache layer.

Every provider still exposes a single public method `clone_speak` — the
paradigm is internal. The handler calls clone_speak() and lets the provider
decide whether to do a two-step flow behind the scenes.
"""

from abc import abstractmethod

from core.base_service import BaseService


class BaseVoiceCloneService(BaseService):
    """Abstract base for all voice-cloning TTS services.

    Implementations MUST override `clone_speak`. They SHOULD override
    `ensure_voice_id` if the provider uses voice_id caching (paradigm B).
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

    def ensure_voice_id(self, reference_audio_url: str,
                        reference_text: str = "",
                        name: str = "",
                        **kwargs) -> str:
        """Optional: create or reuse a persistent voice_id on the provider.

        Providers in paradigm A (zero-shot) should leave this unimplemented
        (returns empty string). Providers in paradigm B (ElevenLabs, PlayHT,
        ...) override it to POST the sample once, cache the voice_id, and
        return it.

        Returns the voice_id (provider-opaque string) or "" if not applicable.
        """
        return ""

    def delete_voice_id(self, voice_id: str) -> bool:
        """Optional: delete a voice_id on the provider (cleanup cascade).

        Returns True if deleted (or never existed). Zero-shot providers
        default to True (nothing to delete).
        """
        return True
