import base64
import io
import json
from pathlib import Path

import pytest

from core._file_store_stream import store_stream
from core.file_store import FileStore
from services import _http_upload_stream as upload_stream
from services._filesystem_ops import _RelayFsOpsMixin


class _UploadHandler:
    def __init__(self, body, *, content_type="application/octet-stream",
                 include_length=True):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Type": content_type}
        if include_length:
            self.headers["Content-Length"] = str(len(body))
        self.status = None
        self.response_headers = {}
        self._renew_cookie = ""

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        return None


class _Session:
    username = "alice"


def test_filestore_stream_writes_chunks_without_joining(tmp_path):
    store = FileStore(base_dir=str(tmp_path / "files"))
    chunks = (chunk for chunk in (b"abc", b"defg", b"h"))

    file_id = store_stream(
        store, "video.mp4", chunks, expected_size=8,
        user_id="alice", conversation_id="conv", content_type="video/mp4")

    metadata = store.get_metadata(file_id)
    assert metadata["size"] == 8
    assert Path(store._entries[file_id]["path"]).read_bytes() == b"abcdefgh"
    assert not list((tmp_path / "files").rglob("*.part"))


def test_filestore_stream_rejects_short_body_without_publishing(tmp_path):
    store = FileStore(base_dir=str(tmp_path / "files"))

    with pytest.raises(ValueError, match="size mismatch"):
        store_stream(
            store, "short.bin", iter([b"abc"]), expected_size=4,
            user_id="alice", conversation_id="conv")

    assert store._entries == {}
    assert not list((tmp_path / "files").rglob("*.part"))


def test_filestore_stream_rejects_long_body_without_publishing(tmp_path):
    store = FileStore(base_dir=str(tmp_path / "files"))

    with pytest.raises(ValueError, match="exceeded Content-Length"):
        store_stream(
            store, "long.bin", iter([b"abc", b"def"]), expected_size=5,
            user_id="alice", conversation_id="conv")

    assert store._entries == {}
    assert not list((tmp_path / "files").rglob("*.part"))


def test_relay_stream_encodes_only_each_bounded_transport_chunk():
    class Relay(_RelayFsOpsMixin):
        def __init__(self):
            self.calls = []

        def _request(self, action, path, **kwargs):
            self.calls.append((action, path, kwargs))
            return {}

    relay = Relay()
    written = relay.write_file_stream(
        "video.mp4", iter([b"abc", b"def"]), expected_size=6)

    assert written == 6
    assert [base64.b64decode(call[2]["data"]) for call in relay.calls] == [b"abc", b"def"]
    assert all(call[2]["_retry_on_disconnect"] is False
               for call in relay.calls)
    assert relay.calls[-1][2]["done"] is True


def test_relay_upload_publishes_only_after_stream_completion(monkeypatch):
    class Relay:
        def __init__(self):
            self.events = []

        def write_file_stream(self, path, chunks, expected_size):
            payload = b"".join(chunks)
            self.events.append(("write", path, payload, expected_size))
            return len(payload)

        def exec(self, path, command, timeout):
            self.events.append(("publish", path, command, timeout))
            return {"returncode": 0}

        def delete_file(self, path):
            self.events.append(("delete", path))

    relay = Relay()
    monkeypatch.setattr(upload_stream, "find_fs_service",
                        lambda *_args, **_kwargs: relay)

    written = upload_stream._stream_to_relay(
        "alice", "conv", "relay", "exports/video file.mp4",
        iter([b"abc", b"def"]), 6)

    assert written == 6
    assert [event[0] for event in relay.events] == ["write", "publish"]
    temp_path = relay.events[0][1]
    assert temp_path.startswith("exports/video file.mp4.pawflow-upload-")
    assert temp_path.endswith(".part")
    assert relay.events[1][2].startswith("mv -f -- ")
    assert "'exports/video file.mp4'" in relay.events[1][2]


def test_relay_upload_removes_temporary_file_after_failure(monkeypatch):
    class Relay:
        def __init__(self):
            self.deleted = []

        def write_file_stream(self, path, chunks, expected_size):
            next(iter(chunks))
            raise ConnectionError("relay disconnected")

        def delete_file(self, path):
            self.deleted.append(path)

    relay = Relay()
    monkeypatch.setattr(upload_stream, "find_fs_service",
                        lambda *_args, **_kwargs: relay)

    with pytest.raises(ConnectionError, match="relay disconnected"):
        upload_stream._stream_to_relay(
            "alice", "conv", "relay", "video.mp4",
            iter([b"abc", b"def"]), 6)

    assert len(relay.deleted) == 1
    assert relay.deleted[0].startswith("video.mp4.pawflow-upload-")
    assert relay.deleted[0].endswith(".part")


def test_raw_http_upload_reads_bounded_chunks_and_returns_one_file(monkeypatch):
    handler = _UploadHandler(b"abcdefgh", content_type="video/mp4")
    captured = {}

    def fake_store_stream(store, filename, chunks, **kwargs):
        captured.update(kwargs)
        captured["store"] = store
        captured["filename"] = filename
        captured["chunks"] = list(chunks)
        return "file123"

    fake_store = object()
    monkeypatch.setattr(upload_stream, "store_stream", fake_store_stream)
    monkeypatch.setattr(upload_stream.FileStore, "instance",
                        classmethod(lambda _cls: fake_store))
    monkeypatch.setattr(upload_stream, "_CHUNK_SIZE", 3)

    upload_stream.handle_upload_stream(
        handler, 8, _Session(),
        "filename=video.mp4&conversation_id=conv&ttl=120")

    assert handler.status == 200
    assert captured["store"] is fake_store
    assert captured["filename"] == "video.mp4"
    assert captured["chunks"] == [b"abc", b"def", b"gh"]
    assert captured["expected_size"] == 8
    assert captured["content_type"] == "video/mp4"
    assert captured["user_id"] == "alice"
    assert captured["conversation_id"] == "conv"
    assert captured["ttl"] == 120
    response = json.loads(handler.wfile.getvalue())
    assert response["files"][0]["file_id"] == "file123"
    assert response["files"][0]["size"] == 8


def test_raw_http_upload_rejects_missing_user_before_reading_body():
    handler = _UploadHandler(b"secret")

    upload_stream.handle_upload_stream(handler, 6, None, "filename=x.bin")

    assert handler.status == 403
    assert handler.rfile.tell() == 0
    assert json.loads(handler.wfile.getvalue())["error"] == (
        "Authenticated user is required")


def test_http_upload_fast_path_precedes_generic_body_read():
    source = Path("services/_http_request.py").read_text(encoding="utf-8")
    branch = source.index('path == "/api/upload"')
    generic_read = source.index("self.rfile.read(content_length)")
    assert branch < generic_read

    handler = Path("services/_http_upload_stream.py").read_text(encoding="utf-8")
    assert "read(min(_CHUNK_SIZE, remaining))" in handler
    assert "read(content_length)" not in handler
    assert "BytesParser" not in handler
