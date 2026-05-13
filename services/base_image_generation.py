"""Base interface for image generation services.

Contract: generate(**kwargs) -> {"image_bytes": bytes, "content_type": str}
or {"image_path": str, "content_type": str} for file-backed providers.
The tool handler calls generate(), each provider implements its own logic.
"""

from abc import abstractmethod
from core.base_service import BaseService


class BaseImageGenerationService(BaseService):
    """Abstract base for all image generation services."""

    @abstractmethod
    def generate(self, **kwargs) -> dict:
        """Generate an image.

        Returns:
            {"image_bytes": bytes, "content_type": str}
        """
        ...
