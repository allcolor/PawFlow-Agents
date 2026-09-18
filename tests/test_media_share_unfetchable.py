"""An unshareable reference must be reported, not silently sent to a vendor.

Regression: the tool relay carried no file_base_url, so the capability handlers
resolved `fs://filestore/<id>/<name>` against the dead dev default
http://localhost:9090 and handed that to Meshy. Meshy cannot fetch it, its task
failed, and the tool result said nothing about why.
"""

from core.file_store import FileStore
from core.handlers._capability_base import _CapabilityHandlerBase
from core.media_share import TemporaryPublicRefs

_PUBLIC = "https://webchat.example.org"
_LOCAL = "http://localhost:9090"


def _store(tmp_path):
    store = FileStore(base_dir=str(tmp_path / "files"))
    FileStore._instance = store
    return store


def _make_file(store, user_id="u1"):
    return store.store("logo.png", b"\x89PNG", "image/png",
                       user_id=user_id, conversation_id="c1")


def test_unfetchable_ref_is_reported_with_the_fix(tmp_path):
    store = _store(tmp_path)
    try:
        fid = _make_file(store)
        share = TemporaryPublicRefs(_LOCAL, "u1")

        assert share.public_url(f"fs://filestore/{fid}/logo.png") == \
            f"{_LOCAL}/files/{fid}"

        warning = share.unfetchable_warning()
        assert fid in warning
        assert _LOCAL in warning
        assert "public_callback_base_url" in warning
        assert "file_base_url" in warning
    finally:
        FileStore._instance = None


def test_no_warning_when_the_ref_is_shared_publicly(tmp_path):
    store = _store(tmp_path)
    try:
        fid = _make_file(store)
        share = TemporaryPublicRefs(_PUBLIC, "u1")

        assert share.public_url(f"fs://filestore/{fid}/logo.png").startswith(_PUBLIC)
        assert share.unfetchable_warning() == ""
    finally:
        FileStore._instance = None


def test_no_warning_for_a_provider_that_reads_filestore_locally(tmp_path):
    store = _store(tmp_path)
    try:
        fid = _make_file(store)

        class _Local:
            ACCEPTS_FILESTORE_URLS = True

        share = TemporaryPublicRefs(_LOCAL, "u1")
        share.public_url(f"fs://filestore/{fid}/logo.png", service=_Local())

        assert share.unfetchable_warning() == ""
    finally:
        FileStore._instance = None


def test_handler_result_carries_the_unshareable_ref_warning(tmp_path):
    store = _store(tmp_path)
    try:
        class _Handler(_CapabilityHandlerBase):
            @property
            def name(self):
                return "test_share_handler"

            @property
            def description(self):
                return "share warning test handler"

            @property
            def parameters_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, arguments):
                url = self._rewrite(arguments["image_url"])
                return f"generated from {url}"

        handler = _Handler()
        handler.set_base_url(_LOCAL)
        handler.set_user_id("u1")
        fid = _make_file(store)

        result = handler.execute({"image_url": f"fs://filestore/{fid}/logo.png"})

        assert result.startswith(f"generated from {_LOCAL}/files/{fid}")
        assert "no internet-reachable base URL" in result
    finally:
        FileStore._instance = None
