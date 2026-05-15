from core.handlers.read import ReadHandler


class _Svc:
    def read_file(self, path, local=False):
        return b"image-bytes"


def test_read_image_from_filesystem_service_returns_vision_marker(monkeypatch):
    handler = ReadHandler()
    monkeypatch.setattr(handler, "_resolve", lambda source: (_Svc(), ""))

    out = handler.execute({"path": "sample.png", "source": "relay"})

    assert out.startswith("Image: sample.png")
    assert "__image_data__:image/png:" in out
    assert "use see" not in out.lower()
