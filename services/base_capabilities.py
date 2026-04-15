"""Base interfaces for additional media-generation capabilities.

Each abstract class defines the contract handlers expect from any
provider (Pixazo, Replicate, Fal.ai, a custom API, …). Providers
subclass the relevant base and implement the single public method.

Return shape is always
    {"bytes": bytes, "content_type": str, "source_url": str}
plus a category-specific alias (`image_bytes`, `video_bytes`, …) that
spells out what kind of payload the caller just received.
"""

from abc import abstractmethod

from core.base_service import BaseService


class BaseImage3DService(BaseService):
    """Generate a 3D model (GLB / GLTF / OBJ / USDZ) from an image or prompt."""

    @abstractmethod
    def generate_3d(self, prompt: str = "", image_url: str = "",
                    **kwargs) -> dict:
        """Return {"bytes": bytes, "content_type": str, "source_url": str}."""
        ...


class BaseImageUpscaleService(BaseService):
    """Upscale an image (2x, 4x, 8x) with an AI model."""

    @abstractmethod
    def upscale(self, image_url: str, scale: int = 2, **kwargs) -> dict:
        ...


class BaseTryOnService(BaseService):
    """Virtual try-on: dress a person image with a garment image."""

    @abstractmethod
    def try_on(self, person_image: str, garment_image: str,
               **kwargs) -> dict:
        ...


class BaseLipsyncService(BaseService):
    """Sync a face video/image with an audio track."""

    @abstractmethod
    def lipsync(self, video_url: str = "", image_url: str = "",
                audio_url: str = "", **kwargs) -> dict:
        ...


class BaseImageTrainerService(BaseService):
    """Fine-tune / train an image model (LoRA / full) on a dataset."""

    @abstractmethod
    def train(self, dataset_url: str, base_model: str = "",
              **kwargs) -> dict:
        """Return {"lora_url": str, "status": str, ...} — async job."""
        ...
