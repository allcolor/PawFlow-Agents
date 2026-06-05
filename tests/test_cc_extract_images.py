"""Tests for claude_code._extract_images placeholder policy.

When an image is forwarded to CC via the native vision channel, it keeps the
FileStore URL as a reusable handle, but does not leave a generic placeholder
like "[image: foo.png]" behind.

Historical images (older user messages, not re-injected into vision on
resume) DO get a text placeholder so the model still knows an image was
there — but it's only a marker, not a second copy.
"""
import pytest

from core.llm_client import LLMMessage
from core.llm_providers.claude_code import LLMClaudeCodeMixin


def _msg(role, content):
    # bypass the ts/seq invariant for pure structural tests
    return LLMMessage(role=role, content=content,
                      timestamp=1.0, seq=1, conversation_id="test_conv")


def test_last_user_image_url_no_placeholder():
    # data: URL in the LAST user message goes to vision, no placeholder.
    msgs = [
        _msg("user", [
            {"type": "text", "text": "hello"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]),
    ]
    blocks = LLMClaudeCodeMixin._extract_images(
        msgs, user_id="u", conversation_id="c")
    assert len(blocks) == 1
    # The text content should NOT contain "[image]" placeholder
    assert all(b.get("type") != "text" or b.get("text") != "[image]"
               for b in msgs[0].content if isinstance(b, dict))
    # Only the original text block survives
    _texts = [b["text"] for b in msgs[0].content
              if isinstance(b, dict) and b.get("type") == "text"]
    assert _texts == ["hello"]


def test_last_user_image_ref_no_placeholder(tmp_path, monkeypatch):
    # Set up a fake FileStore entry
    from core.file_store import FileStore
    fs = FileStore.instance()
    _fid = fs.store("foo.png", b"\x89PNGfake", "image/png",
                    user_id="u", conversation_id="c")
    try:
        msgs = [
            _msg("user", [
                {"type": "text", "text": "see this"},
                {"type": "image_ref", "file_id": _fid,
                 "filename": "foo.png", "mime_type": "image/png"},
            ]),
        ]
        blocks = LLMClaudeCodeMixin._extract_images(
            msgs, user_id="u", conversation_id="c")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"
        # No "[image: foo.png]" placeholder left behind; FileStore link remains.
        _texts = [b.get("text", "") for b in msgs[0].content
                  if isinstance(b, dict) and b.get("type") == "text"]
        assert "see this" in _texts
        assert any(f"fs://filestore/{_fid}/foo.png" in t for t in _texts)
        assert not any("[image:" in t for t in _texts)
    finally:
        try: fs.delete(_fid)
        except Exception: pass


def test_older_user_image_keeps_placeholder():
    # Historical user message: image stays as text placeholder, not in vision
    msgs = [
        _msg("user", [
            {"type": "text", "text": "earlier turn"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,OLD"}},
        ]),
        _msg("assistant", "ack"),
        _msg("user", [{"type": "text", "text": "current turn, no image"}]),
    ]
    # The CURRENT user has no list content — wait, it does (one text block)
    # _last_user_idx picks the LAST user with list content → msgs[2]
    # msgs[0] is older, placeholder should remain
    blocks = LLMClaudeCodeMixin._extract_images(
        msgs, user_id="u", conversation_id="c")
    assert blocks == []  # no images in the current turn
    _first_texts = [b.get("text", "") for b in msgs[0].content
                    if isinstance(b, dict) and b.get("type") == "text"]
    # The old image should have been replaced with a "[image]" placeholder
    assert "[image]" in _first_texts


def test_older_user_image_ref_keeps_filestore_link_with_filename():
    msgs = [
        _msg("user", [
            {"type": "text", "text": "earlier"},
            {"type": "image_ref", "file_id": "xxx",
             "filename": "old.png"},
        ]),
        _msg("assistant", "ack"),
        _msg("user", [{"type": "text", "text": "now"}]),
    ]
    # Older image_ref: placeholder kept, NOT loaded (no FileStore lookup)
    blocks = LLMClaudeCodeMixin._extract_images(
        msgs, user_id="u", conversation_id="c")
    assert blocks == []
    _old_texts = [b.get("text", "") for b in msgs[0].content
                  if isinstance(b, dict) and b.get("type") == "text"]
    assert any("fs://filestore/xxx/old.png" in t for t in _old_texts)


def test_text_only_user_message_unchanged():
    msgs = [_msg("user", [{"type": "text", "text": "hi"}])]
    blocks = LLMClaudeCodeMixin._extract_images(
        msgs, user_id="u", conversation_id="c")
    assert blocks == []
    assert msgs[0].content == [{"type": "text", "text": "hi"}]


def test_gemini_current_image_ref_keeps_filestore_link(monkeypatch):
    from core.llm_providers.gemini import LLMGeminiMixin

    class _Store:
        def get_required(self, file_id, user_id="", conversation_id=""):
            assert (file_id, user_id, conversation_id) == ("img1", "u", "c")
            return "shot.png", b"PNG", "image/png"

    monkeypatch.setattr("core.file_store.FileStore.instance", staticmethod(lambda: _Store()))
    msgs = [_msg("user", [
        {"type": "text", "text": "see this"},
        {"type": "image_ref", "file_id": "img1", "filename": "shot.png"},
    ])]

    blocks = LLMGeminiMixin._gemini_acp_extract_images(
        msgs, user_id="u", conversation_id="c")

    assert len(blocks) == 1
    texts = [b.get("text", "") for b in msgs[0].content
             if isinstance(b, dict) and b.get("type") == "text"]
    assert "see this" in texts
    assert "Attached image: fs://filestore/img1/shot.png" in texts
