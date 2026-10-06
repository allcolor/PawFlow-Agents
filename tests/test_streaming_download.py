import io
import json
import types
from pathlib import Path

import pytest

import core.conversation_access as conversation_access
from services import _http_download_stream as download_stream


class _Server:
    def __init__(self, accept=True):
        self.accept = accept
        self.transfers = []

    def transfer_current_dispatch_to_long_lived(self, reason, request=None):
        self.transfers.append(reason)
        return self.accept


class _Handler:
    def __init__(self, server=None):
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}
        self._renew_cookie = ""
        self.close_connection = False
        self.server = server or _Server()
        self.connection = object()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        return None


class _Session:
    username = "alice"


class _Relay:
    def __init__(self, chunks, kind="file", size=None):
        self.chunks = chunks
        self.kind = kind
        self.size = sum(len(c) for c in chunks) if size is None else size
        self.yielded = 0

    def stat(self, path):
        return types.SimpleNamespace(kind=self.kind, size=self.size)

    def iter_file_chunks(self, path):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


@pytest.fixture
def relay(monkeypatch):
    holder = {"relay": _Relay([b"abc", b"def", b"gh"]), "denied": False}

    def fake_require_write(cid, user):
        if holder["denied"]:
            raise conversation_access.ConversationAccessError("nope")

    monkeypatch.setattr(conversation_access, "require_write",
                        fake_require_write)
    monkeypatch.setattr(download_stream, "find_fs_service",
                        lambda *_a, **_kw: holder["relay"])
    return holder


QUERY = "conversation_id=conv&service=relay&path=exports%2Fvid%C3%A9o%20%22x%22.mp4"


def test_relay_download_streams_chunks_with_length_and_attachment(relay):
    handler = _Handler()

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.status == 200
    assert handler.wfile.getvalue() == b"abcdefgh"
    headers = handler.response_headers
    assert headers["Content-Length"] == "8"
    assert headers["Content-Type"] == "video/mp4"
    disposition = headers["Content-Disposition"]
    assert disposition.startswith('attachment; filename="vid_o _x_.mp4"')
    assert "filename*=UTF-8''vid%C3%A9o%20%22x%22.mp4" in disposition
    assert "\n" not in disposition and "\r" not in disposition
    assert handler.server.transfers == ["relay download"]
    assert handler.close_connection is True


def test_relay_download_requires_user_and_conversation_access(relay):
    anonymous = _Handler()
    download_stream.handle_relay_download(anonymous, None, QUERY)
    assert anonymous.status == 403

    relay["denied"] = True
    stranger = _Handler()
    download_stream.handle_relay_download(stranger, _Session(), QUERY)
    assert stranger.status == 404
    assert relay["relay"].yielded == 0


def test_relay_download_rejects_missing_params_and_directories(relay):
    missing = _Handler()
    download_stream.handle_relay_download(
        missing, _Session(), "conversation_id=conv&service=relay")
    assert missing.status == 400

    relay["relay"] = _Relay([], kind="directory", size=0)
    directory = _Handler()
    download_stream.handle_relay_download(directory, _Session(), QUERY)
    assert directory.status == 400
    assert "directory" in json.loads(directory.wfile.getvalue())["error"]


def test_relay_download_failure_mid_stream_leaves_body_short(relay):
    class _Broken(_Relay):
        def iter_file_chunks(self, path):
            yield b"abc"
            raise ConnectionError("relay disconnected")

    relay["relay"] = _Broken([b"abc", b"def"])
    handler = _Handler()

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.response_headers["Content-Length"] == "6"
    assert handler.wfile.getvalue() == b"abc"
    assert handler.close_connection is True


def test_relay_download_without_long_lived_slot_sends_nothing(relay):
    handler = _Handler(server=_Server(accept=False))

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.status is None
    assert relay["relay"].yielded == 0


def test_download_fast_path_precedes_generic_body_read():
    source = Path("services/_http_request.py").read_text(encoding="utf-8")
    branch = source.index('path == "/api/fs/download"')
    generic_read = source.index("self.rfile.read(content_length)")
    assert branch < generic_read


def test_file_explorer_download_navigates_to_streaming_route():
    src = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")
    download = src[src.index("function _feDl(name)"):src.index("\nfunction _feUpload()")]

    assert "'/api/fs/download?'" in download
    assert "fs_read_file" not in download
    assert "Blob" not in download
    assert "createObjectURL" not in download
