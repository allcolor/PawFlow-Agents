"""Base interface for speech-to-text providers."""

from abc import abstractmethod

from core.base_service import BaseService


class BaseSTTService(BaseService):
    """Abstract base for providers that transcribe speech to text."""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes = b"", audio_path: str = "",
                   mime_type: str = "", language: str = "",
                   prompt: str = "", **kwargs) -> dict:
        """Transcribe audio.

        Returns:
            {"text": str, "language": str, "duration": float, "segments": list}
        """
        ...

