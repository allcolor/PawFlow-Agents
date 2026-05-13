"""Base interface for video generation services.

Contract: generate(**kwargs) -> {"video_bytes": bytes, "content_type": str}
or {"video_path": str, "content_type": str} for file-backed providers.
The tool handler calls generate(), each provider implements its own logic.
"""

from abc import abstractmethod
from core.base_service import BaseService


class BaseVideoGenerationService(BaseService):
    """Abstract base for all video generation services."""

    @abstractmethod
    def generate(self, **kwargs) -> dict:
        """Generate a video.

        Returns:
            {"video_bytes": bytes, "content_type": str}
        """
        ...
