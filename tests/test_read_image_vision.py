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


class _BigSvc:
    def read_file(self, path, local=False):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (4000, 2250), (10, 120, 200)).save(buf, format="PNG")
        return buf.getvalue()


def test_read_oversized_image_is_downscaled_to_vision_ceiling(monkeypatch):
    # Regression: read.py used to emit the raw oversized base64 image,
    # which CC then rejected ('dimensions exceed the 2000x2000px limit').
    # It must now downscale via resize_image_for_vision (720p ceiling).
    import base64
    import io
    from PIL import Image
    from core.image_resize import MAX_DIM

    handler = ReadHandler()
    monkeypatch.setattr(handler, "_resolve", lambda source: (_BigSvc(), ""))

    out = handler.execute({"path": "big.png", "source": "relay"})

    # Oversized PNG is re-encoded to JPEG by the downscaler.
    assert "__image_data__:image/jpeg:" in out
    b64 = out.split("__image_data__:image/jpeg:", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(img.size) <= MAX_DIM
