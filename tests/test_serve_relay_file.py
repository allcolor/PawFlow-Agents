"""ServeRelayFileTask — proxies a relay/filesystem service file over HTTP.

Used by the chat UI to inline media stored on the user's relay
(<img src="/fs/<service>/<path>">) without an extra call_tool round-trip.
"""

from unittest.mock import patch

import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory  # noqa: E402


def _make_ff(service: str, rest: str, principal: str = "user1") -> FlowFile:
    ff = FlowFile(content=b"")
    ff.set_attribute("http.path.service_name", service)
    ff.set_attribute("http.path.rest", rest)
    ff.set_attribute("http.auth.principal", principal)
    return ff


def _new_task():
    cls = TaskFactory.get("serveRelayFile")
    return cls({})


def test_registered():
    cls = TaskFactory.get("serveRelayFile")
    assert cls.__name__ == "ServeRelayFileTask"


def test_missing_service_returns_400():
    task = _new_task()
    ff = _make_ff("", "some/path.png")
    out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "400"


def test_missing_auth_returns_401():
    task = _new_task()
    ff = _make_ff("relay1", "image.png", principal="")
    out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "401"


def test_unknown_service_returns_404():
    task = _new_task()
    ff = _make_ff("does_not_exist", "image.png")
    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=None):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "404"


def test_service_without_read_file_returns_400():
    task = _new_task()
    ff = _make_ff("relay1", "image.png")

    class _Svc:
        pass  # no read_file method

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "400"


def test_file_not_found_returns_404():
    task = _new_task()
    ff = _make_ff("relay1", "missing.png")

    class _Svc:
        def read_file(self, path):
            raise FileNotFoundError(path)

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "404"


def test_permission_denied_returns_403():
    task = _new_task()
    ff = _make_ff("relay1", "secret.png")

    class _Svc:
        def read_file(self, path):
            raise PermissionError(path)

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "403"


def test_image_served_with_correct_content_type():
    task = _new_task()
    ff = _make_ff("relay1", "assets/hero.png")
    payload = b"\x89PNG\r\n\x1a\nfake"

    class _Svc:
        def read_file(self, path):
            assert path == "assets/hero.png"
            return payload

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type") == "image/png"
    assert "hero.png" in out.get_attribute("http.response.header.Content-Disposition")
    assert out.get_attribute("http.response.header.Content-Length") == str(len(payload))
    assert out.get_content() == payload


def test_audio_served_with_audio_mime():
    task = _new_task()
    ff = _make_ff("relay1", "speech.mp3")

    class _Svc:
        def read_file(self, path):
            return b"ID3\x04\x00fake-mp3-bytes"

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type") == "audio/mpeg"


def test_video_served_with_video_mime():
    task = _new_task()
    ff = _make_ff("relay1", "clip.mp4")

    class _Svc:
        def read_file(self, path):
            return b"\x00\x00\x00\x18ftypmp42fake"

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type") == "video/mp4"


def test_string_payload_is_encoded():
    task = _new_task()
    ff = _make_ff("relay1", "note.txt")

    class _Svc:
        def read_file(self, path):
            return "hello world"  # str, not bytes

    with patch("tasks.io.serve_relay_file.find_fs_service", return_value=_Svc()):
        out = task.execute(ff)[0]
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_content() == b"hello world"
