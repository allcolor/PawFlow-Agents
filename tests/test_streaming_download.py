import io
import json
import types
from pathlib import Path

import pytest

import core.conversation_access as conversation_access
from services import _http_download_stream as download_stream
from services._filesystem_ops import _RelayFsOpsMixin
from services._http_base import _SECURITY_HEADERS
from tools.fs_actions import action_read_chunk


class _Server:
    def __init__(self, accept=True):
        self.accept = accept
        self.transfers = []

    def transfer_current_dispatch_to_long_lived(self, reason, request=None):
        self.transfers.append(reason)
        return self.accept


class _Handler:
    def __init__(self, server=None, headers=None):
        self.wfile = io.BytesIO()
        self.headers = headers or {}
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
        self.ranges = []

    def stat(self, path):
        return types.SimpleNamespace(kind=self.kind, size=self.size)

    def iter_file_chunks(self, path):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    def iter_file_range(self, path, start, end):
        self.ranges.append((start, end))
        yield b"".join(self.chunks)[start:end + 1]


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
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Type"] == "video/mp4"
    disposition = headers["Content-Disposition"]
    assert disposition.startswith('attachment; filename="vid_o _x_.mp4"')
    assert "filename*=UTF-8''vid%C3%A9o%20%22x%22.mp4" in disposition
    assert "\n" not in disposition and "\r" not in disposition
    assert "Content-Security-Policy" not in headers
    assert handler.server.transfers == ["relay download"]
    assert handler.close_connection is True


def test_relay_preview_streams_inline_in_a_script_sandbox(relay):
    handler = _Handler()

    download_stream.handle_relay_download(handler, _Session(), QUERY + "&inline=1")

    assert handler.status == 200
    assert handler.wfile.getvalue() == b"abcdefgh"
    headers = handler.response_headers
    assert headers["Content-Length"] == "8"
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Content-Disposition"].startswith('inline; filename="vid_o _x_.mp4"')
    # Opened directly in a tab, relay HTML must not run script in the webchat origin.
    assert headers["Content-Security-Policy"] == (
        _SECURITY_HEADERS["Content-Security-Policy"] + "; sandbox")
    assert handler.server.transfers == ["relay download"]
    assert handler.close_connection is True


def test_relay_pdf_preview_is_not_sandboxed(relay):
    handler = _Handler()

    download_stream.handle_relay_download(
        handler, _Session(),
        "conversation_id=conv&service=relay&path=docs%2Freport.pdf&inline=1")

    headers = handler.response_headers
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"].startswith('inline; filename="report.pdf"')
    assert "Content-Security-Policy" not in headers
    assert handler.wfile.getvalue() == b"abcdefgh"


@pytest.mark.parametrize("header, body, content_range", [
    ("bytes=2-5", b"cdef", "bytes 2-5/8"),
    ("bytes=6-", b"gh", "bytes 6-7/8"),
    ("bytes=-3", b"fgh", "bytes 5-7/8"),
    ("bytes=4-100", b"efgh", "bytes 4-7/8"),
    ("Bytes=0-0", b"a", "bytes 0-0/8"),
])
def test_relay_range_streams_only_the_requested_bytes(
        relay, header, body, content_range):
    handler = _Handler(headers={"Range": header})

    download_stream.handle_relay_download(handler, _Session(), QUERY + "&inline=1")

    assert handler.status == 206
    assert handler.wfile.getvalue() == body
    headers = handler.response_headers
    assert headers["Content-Range"] == content_range
    assert headers["Content-Length"] == str(len(body))
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Disposition"].startswith('inline; filename="vid_o _x_.mp4"')
    assert headers["Content-Security-Policy"] == (
        _SECURITY_HEADERS["Content-Security-Policy"] + "; sandbox")
    # The relay reads only the range, never the whole file.
    assert relay["relay"].yielded == 0
    assert len(relay["relay"].ranges) == 1
    assert handler.server.transfers == ["relay download"]
    assert handler.close_connection is True


@pytest.mark.parametrize("header", ["bytes=8-", "bytes=100-200", "bytes=-0"])
def test_relay_range_past_the_end_is_416_with_the_file_size(relay, header):
    handler = _Handler(headers={"Range": header})

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.status == 416
    assert handler.response_headers["Content-Range"] == "bytes */8"
    assert handler.server.transfers == []
    assert relay["relay"].yielded == 0
    assert relay["relay"].ranges == []


@pytest.mark.parametrize("header", [
    "bytes=0-1,4-5", "items=0-1", "bytes=5-3", "bytes=x-", "bytes=-",
])
def test_relay_download_sends_the_whole_file_for_ranges_it_ignores(
        relay, header):
    handler = _Handler(headers={"Range": header})

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.status == 200
    assert handler.wfile.getvalue() == b"abcdefgh"
    assert handler.response_headers["Content-Length"] == "8"
    assert "Content-Range" not in handler.response_headers
    assert relay["relay"].ranges == []


def test_relay_range_on_an_empty_file_sends_the_empty_file(relay):
    relay["relay"] = _Relay([])
    handler = _Handler(headers={"Range": "bytes=0-"})

    download_stream.handle_relay_download(handler, _Session(), QUERY)

    assert handler.status == 200
    assert handler.response_headers["Content-Length"] == "0"
    assert handler.wfile.getvalue() == b""


def test_relay_range_reads_only_the_chunks_covering_it(tmp_path):
    payload = bytes(range(256)) * 40
    (tmp_path / "video.mp4").write_bytes(payload)

    class Relay(_RelayFsOpsMixin):
        def __init__(self):
            self.indexes = []

        def _request(self, action, path, **kwargs):
            # The relay's own stateless chunk reader answers.
            assert action == "read_chunk"
            self.indexes.append(kwargs["index"])
            return action_read_chunk(str(tmp_path), str(tmp_path / path), kwargs)

    relay = Relay()
    middle = relay.iter_file_range("video.mp4", 5000, 7100, chunk_size=1024)
    assert b"".join(middle) == payload[5000:7101]
    assert relay.indexes == [4, 5, 6]

    relay.indexes.clear()
    tail = relay.iter_file_range("video.mp4", 10000, 10239, chunk_size=1024)
    assert b"".join(tail) == payload[10000:]
    assert relay.indexes == [9]

    # A file that shrank since its stat ends the range early.
    relay.indexes.clear()
    short = relay.iter_file_range("video.mp4", 10000, 12000, chunk_size=1024)
    assert b"".join(short) == payload[10000:]
    assert relay.indexes == [9, 10]


def test_relay_short_range_reads_a_short_chunk(tmp_path):
    payload = bytes(range(256)) * 4096
    (tmp_path / "doc.pdf").write_bytes(payload)
    calls = []

    class Relay(_RelayFsOpsMixin):
        def _request(self, action, path, **kwargs):
            calls.append((kwargs["index"], kwargs["chunk_size"]))
            return action_read_chunk(str(tmp_path), str(tmp_path / path), kwargs)

    piece = Relay().iter_file_range("doc.pdf", 900000, 900099)
    assert b"".join(piece) == payload[900000:900100]
    # A PDF viewer's small piece costs one 64 KiB read, not a 4 MiB chunk.
    assert calls == [(900000 // 65536, 65536)]

    calls.clear()
    rest = Relay().iter_file_range("doc.pdf", 100, len(payload) - 1)
    assert b"".join(rest) == payload[100:]
    assert [size for _index, size in calls] == [len(payload) - 100] * 2


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
