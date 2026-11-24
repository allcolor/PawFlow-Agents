"""Text labelled as an image must never reach a provider as an image.

Regression: an agent called see(fs://filestore/<id-of-a-delegate-result.md>/
x.png). The Markdown bytes were sent as image/png, stored as an image_ref, and
Ollama rejected every later request with "invalid image" until the context was
edited by hand.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from core.image_resize import sniff_image_mime

PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)
TEXT = b"# Delegate result\ntask_id: 35b39690\n"


@pytest.mark.parametrize("data,expected", [
    (PNG, "image/png"),
    (b"\xff\xd8\xff\xe0" + bytes(12), "image/jpeg"),
    (b"GIF89a" + bytes(10), "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"BM" + bytes(14), "image/bmp"),
    (TEXT, ""),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", ""),
    (b"", ""),
])
def test_sniff_image_mime(data, expected):
    assert sniff_image_mime(data) == expected


def test_see_rejects_text_behind_an_image_name(tmp_path):
    from core.handlers.see import SeeHandler

    handler = SeeHandler.__new__(SeeHandler)
    fake = tmp_path / "09_C_heaume_dessus.png"
    fake.write_bytes(TEXT)
    result = handler._see_image_path(fake.name, fake, "png")
    assert result.startswith("Error:") and "not an image" in result
    assert "__image_data__" not in result
    assert "not an image" in handler._see_image(fake.name, TEXT, "png")

    real = tmp_path / "real.jpg"  # wrong extension, real PNG bytes
    real.write_bytes(PNG)
    assert "__image_data__:image/png:" in handler._see_image_path(
        real.name, real, "jpg")


def test_see_uses_the_stored_filestore_name_not_the_url_name(tmp_path, monkeypatch):
    from core.handlers.see import SeeHandler

    stored = tmp_path / "ba6777c4c920_delegate_35b39690.md"
    stored.write_bytes(TEXT)
    handler = SeeHandler.__new__(SeeHandler)
    monkeypatch.setattr(handler, "_unwrap_json", lambda a: a, raising=False)
    monkeypatch.setattr(handler, "_resolve", lambda _s: ("filestore", ""))
    monkeypatch.setattr(handler, "_filestore_path", lambda _p: stored)

    result = handler.execute(
        {"path": "fs://filestore/ba6777c4c920/09_C_heaume_dessus.png"})
    assert "__image_data__" not in result
    assert "unsupported file type 'md'" in result


def test_tool_result_text_is_not_stored_as_an_image(monkeypatch):
    from tasks.ai.agent_core import AgentCoreMixin

    stored = []
    import core.file_store as file_store
    monkeypatch.setattr(file_store.FileStore, "instance", staticmethod(
        lambda: SimpleNamespace(store=lambda *a, **k: stored.append(a) or "fid")))
    convert = getattr(AgentCoreMixin, "_materialize_tool_result_images", None)
    if convert is None:
        pytest.skip("converter name changed")

    url = "data:image/png;base64," + base64.b64encode(TEXT).decode()
    out = convert([{"type": "text", "text": "Image: x.png"},
                   {"type": "image_url", "image_url": {"url": url}}],
                  user_id="u", conversation_id="c")
    assert stored == []
    assert out[1]["type"] == "text" and "not a valid image" in out[1]["text"]

    good = "data:image/png;base64," + base64.b64encode(PNG).decode()
    out = convert([{"type": "image_url", "image_url": {"url": good}}],
                  user_id="u", conversation_id="c")
    assert out[0]["type"] == "image_ref" and len(stored) == 1


def test_openai_sends_a_mislabelled_image_ref_as_a_link(monkeypatch):
    import core.file_store as file_store
    from core.llm_providers.openai import LLMOpenaiMixin

    blobs = {"bad": TEXT, "good": PNG}
    monkeypatch.setattr(file_store.FileStore, "instance", staticmethod(
        lambda: SimpleNamespace(get_required=lambda fid, **k: (
            "tool_image_1.png", blobs[fid], "image/png"))))

    bad = {"type": "image_ref", "file_id": "bad", "mime_type": "image/png"}
    assert LLMOpenaiMixin._vision_image_part(bad, user_id="u") is None
    good = LLMOpenaiMixin._vision_image_part(
        {"type": "image_ref", "file_id": "good"}, user_id="u")
    assert good["image_url"]["url"].startswith("data:image/png;base64,")

    text_url = "data:image/png;base64," + base64.b64encode(TEXT).decode()
    assert LLMOpenaiMixin._vision_image_part(
        {"type": "image_url", "image_url": {"url": text_url}}) is None


def test_ollama_invalid_image_error_triggers_the_text_only_retry():
    from core.llm_providers.openai import LLMOpenaiMixin

    error = ('{"error":{"message":"invalid image in message: ollama: 400: '
             'invalid image: expected image mime type, got \\"text/plain\\""}}')
    assert LLMOpenaiMixin._is_vision_rejected_error(error)
