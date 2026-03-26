"""Base interface for audio generation services.

Contract: generate(**kwargs) -> {"audio_bytes": bytes, "content_type": str}
The tool handler calls generate(), each provider implements its own logic.
"""

from abc import abstractmethod
from core.base_service import BaseService


class BaseAudioGenerationService(BaseService):
    """Abstract base for all audio generation services."""

    @abstractmethod
    def generate(self, **kwargs) -> dict:
        """Generate audio.

        Returns:
            {"audio_bytes": bytes, "content_type": str}
        """
        ...
