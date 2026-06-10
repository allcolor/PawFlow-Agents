"""Temporary public sharing of FileStore media reference inputs."""

from unittest.mock import patch

import pytest

from core.file_store import FileStore
from core.media_share import TemporaryPublicRefs, _is_public_base

_PUBLIC = "https://webchat.example.org"


@pytest.fixture(autouse=True)
def _store(tmp_path):
    """Back FileStore.instance() with a temp dir for the duration of a test."""
    store = FileStore(base_dir=str(tmp_path / "files"))
    FileStore._instance = store
    yield store
    FileStore._instance = None


def _make_file(store, user_id="u1"):
    return store.store(
        "logo.png", b"\x89PNG", "image/png",
        user_id=user_id, conversation_id="c1")


def test_public_url_flips_to_gateway_key_and_restores(_store):
    fid = _make_file(_store)
    share = TemporaryPublicRefs(_PUBLIC, "u1")

    url = share.public_url(f"fs://filestore/{fid}/logo.png")

    assert url.startswith(f"{_PUBLIC}/files/{fid}")
    assert "?k=" in url  # gateway-key URL bypasses the private gateway
    assert _store.get_access_level(fid) == "gateway_key"

    share.restore()
    assert _store.get_access_level(fid) == "private"  # reverted after the call


def test_restore_is_idempotent(_store):
    fid = _make_file(_store)
    share = TemporaryPublicRefs(_PUBLIC, "u1")
    share.public_url(f"fs://filestore/{fid}/logo.png")
    share.restore()
    share.restore()  # second call must not raise / re-flip
    assert _store.get_access_level(fid) == "private"


def test_context_manager_restores_on_exception(_store):
    fid = _make_file(_store)
    with pytest.raises(RuntimeError):
        with TemporaryPublicRefs(_PUBLIC, "u1") as share:
            share.public_url(f"fs://filestore/{fid}/logo.png")
            assert _store.get_access_level(fid) == "gateway_key"
            raise RuntimeError("boom")
    assert _store.get_access_level(fid) == "private"


def test_non_filestore_refs_pass_through(_store):
    share = TemporaryPublicRefs(_PUBLIC, "u1")
    assert share.public_url("https://cdn.example/x.png") == "https://cdn.example/x.png"
    assert share.public_url("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    assert share.public_url("") == ""


def test_service_that_reads_filestore_locally_is_untouched(_store):
    fid = _make_file(_store)

    class _LocalService:
        ACCEPTS_FILESTORE_URLS = True

    share = TemporaryPublicRefs(_PUBLIC, "u1")
    ref = f"fs://filestore/{fid}/logo.png"
    assert share.public_url(ref, service=_LocalService()) == ref
    assert _store.get_access_level(fid) == "private"  # never flipped


def test_non_public_base_does_not_flip_and_returns_legacy_url(_store):
    fid = _make_file(_store)
    share = TemporaryPublicRefs("http://localhost:9090", "u1")

    url = share.public_url(f"fs://filestore/{fid}/logo.png")

    # No public URL is possible, so leave access untouched and return the
    # legacy form (unchanged behaviour for local/dev setups).
    assert url == f"http://localhost:9090/files/{fid}"
    assert _store.get_access_level(fid) == "private"


def test_service_public_callback_base_used_when_handler_base_is_localhost(_store):
    """Regression: the dead localhost:9090 handler default must not win.

    When the tool relay has no file_base_url, the handler base is the dev
    default http://localhost:9090. The media service still carries the real
    public root in public_callback_base_url (same value used for webhooks),
    so the ref must be flipped and rewritten against THAT, not localhost.
    """
    fid = _make_file(_store)

    class _Svc:
        public_callback_base_url = _PUBLIC

    share = TemporaryPublicRefs("http://localhost:9090", "u1")
    url = share.public_url(f"fs://filestore/{fid}/logo.png", service=_Svc())

    assert url.startswith(f"{_PUBLIC}/files/{fid}")
    assert "?k=" in url
    assert "localhost" not in url
    assert _store.get_access_level(fid) == "gateway_key"
    share.restore()
    assert _store.get_access_level(fid) == "private"


def test_service_with_non_public_callback_base_still_no_flip(_store):
    fid = _make_file(_store)

    class _Svc:
        public_callback_base_url = "http://localhost:9090"

    share = TemporaryPublicRefs("http://localhost:9090", "u1")
    url = share.public_url(f"fs://filestore/{fid}/logo.png", service=_Svc())

    assert url == f"http://localhost:9090/files/{fid}"
    assert _store.get_access_level(fid) == "private"


def test_already_public_file_not_restored_to_private(_store):
    fid = _make_file(_store)
    _store.set_access(fid, "public", owner_user_id="u1")
    share = TemporaryPublicRefs(_PUBLIC, "u1")

    url = share.public_url(f"fs://filestore/{fid}/logo.png")

    assert url == f"{_PUBLIC}/files/{fid}"  # public access => no ?k=
    share.restore()
    assert _store.get_access_level(fid) == "public"  # left as-is


def test_video_handler_shares_filestore_ref_during_generation(_store):
    from core.handlers.media import VideoGenerationHandler

    fid = _make_file(_store)
    captured = {}

    class _VideoSvc:
        def image_to_video(self, **kwargs):
            captured.update(kwargs)
            # The provider would fetch the asset right now: it must be public.
            captured["access_during"] = _store.get_access_level(fid)
            return {"video_bytes": b"MP4", "content_type": "video/mp4"}

    handler = VideoGenerationHandler()
    handler.set_base_url(_PUBLIC)
    handler.set_user_id("u1")
    handler.set_conversation_id("c1")
    handler.set_service_resolver(lambda *a: (_VideoSvc(), ""))

    with patch("core.storage_resolver.StorageResolver") as mock_storage:
        mock_storage.return_value.write.return_value = {"file_id": "out-vid"}
        result = handler.execute({
            "prompt": "animate",
            "image_url": f"fs://filestore/{fid}/logo.png",
        })

    assert "out-vid" in result
    assert "?k=" in captured["image_url"]
    assert captured["access_during"] == "gateway_key"
    # Access is revoked once generation returns.
    assert _store.get_access_level(fid) == "private"


def test_capability_base_wraps_execute_with_temporary_share(_store):
    from core.handlers.capabilities import _CapabilityHandlerBase

    fid = _make_file(_store)
    seen = {}

    class _Dummy(_CapabilityHandlerBase):
        @property
        def name(self):
            return "dummy"

        @property
        def description(self):
            return "dummy capability"

        @property
        def parameters_schema(self):
            return {"type": "object", "properties": {}}

        def execute(self, arguments):
            url = self._rewrite(arguments["image_url"])
            seen["url"] = url
            seen["access_during"] = _store.get_access_level(fid)
            return url

    handler = _Dummy()
    handler.set_base_url(_PUBLIC)
    handler.set_user_id("u1")
    handler.set_conversation_id("c1")

    handler.execute({"image_url": f"fs://filestore/{fid}/logo.png"})

    assert "?k=" in seen["url"]
    assert seen["access_during"] == "gateway_key"
    # The base-class wrapper revokes the share after execute() returns.
    assert _store.get_access_level(fid) == "private"
    assert handler._share is None


def test_is_public_base():
    assert _is_public_base("https://webchat.example.org") is True
    assert _is_public_base("https://1.2.3.4") is True
    assert _is_public_base("http://localhost:9090") is False
    assert _is_public_base("http://127.0.0.1:9090") is False
    assert _is_public_base("http://192.168.1.10") is False
    assert _is_public_base("http://10.0.0.5") is False
    assert _is_public_base("") is False
    assert _is_public_base("ftp://example.org") is False
