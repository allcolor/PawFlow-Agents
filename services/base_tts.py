"""Base interface for text-to-speech providers.

All speech providers expose the same primary operation: synthesize text
with a provider voice. Providers may also support registering and deleting
voices, but those are optional capabilities on the same TTS provider model.
"""

from abc import abstractmethod

from core.base_service import BaseService


class BaseTTSService(BaseService):
    """Abstract base for providers that can synthesize speech."""

    @abstractmethod
    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        """Synthesize speech.

        Returns:
            {"audio_bytes": bytes, "content_type": str}
            or {"audio_path": str, "content_type": str}
        """
        ...

    def create_voice(self, name: str, reference_audio_url: str,
                     reference_text: str = "", language: str = "",
                     **kwargs) -> dict:
        """Optional: create or register a provider voice."""
        raise NotImplementedError("provider does not support voice creation")

    def delete_voice(self, voice_id: str, **kwargs) -> bool:
        """Optional: delete a provider voice."""
        return True
