"""Media tool handlers hand the relay they are given to their service."""

import pytest

from core.file_store import FileStore
from core.handlers.media import (
    AudioGenerationHandler,
    EditImageHandler,
    ImageModelInfoHandler,
)


class _Service:
    ACCEPTS_FILESTORE_URLS = True

    def __init__(self):
        self.contexts = []

    def set_runtime_context(self, **kwargs):
        self.contexts.append(kwargs)

    def get_model_info(self):
        return {"model": "test"}

    def generate(self, **kwargs):
        return {"audio_bytes": b"audio-data", "content_type": "audio/mpeg"}

    def edit_image(self, **kwargs):
        return {"image_bytes": b"image-data", "content_type": "image/png"}


@pytest.fixture(autouse=True)
def _file_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FileStore, "_instance", FileStore(base_dir=str(tmp_path / "files")))


@pytest.mark.parametrize("handler_cls, arguments", [
    (ImageModelInfoHandler, {}),
    (AudioGenerationHandler, {"prompt": "calm piano"}),
    (EditImageHandler, {
        "prompt": "add a red hat",
        "image_urls": ["fs://filestore/source-id/source.png"],
    }),
])
def test_media_handler_hands_its_relay_to_the_service(handler_cls, arguments):
    service = _Service()
    handler = handler_cls()
    handler.set_service_resolver(lambda: (service, ""))
    handler.set_user_id("alice")
    handler.set_conversation_id("conv-1")
    handler.set_relay_id("Relay-B", True)
    if hasattr(handler, "set_base_url"):
        handler.set_base_url("http://localhost:9090")

    result = handler.execute(arguments)

    assert not result.startswith("Error"), result
    assert service.contexts[-1]["relay_id"] == "Relay-B"
    assert service.contexts[-1]["relay_local"] is True
