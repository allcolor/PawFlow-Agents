"""Tests for the extracted leaf relay actions (_relay_actions)."""
import base64
import http.server
import threading

import pytest

from pawflow_relay import _relay_actions as ra


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"hello-proxy"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

@pytest.fixture
def http_backend():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


def test_http_proxy_roundtrip(http_backend):
    res = ra.http_proxy({"port": http_backend, "method": "GET", "req_path": "/"})
    assert res["ok"] is True
    assert res["data"]["status"] == 200
    assert base64.b64decode(res["data"]["body"]) == b"hello-proxy"


def test_http_proxy_missing_port():
    assert ra.http_proxy({})["ok"] is False


def test_http_proxy_connection_error():
    # nothing listening on this port -> error result, not raise
    res = ra.http_proxy({"port": 1, "req_path": "/"})
    assert res["ok"] is False and "Proxy error" in res["error"]


def test_script_hash_shape():
    res = ra.script_hash()
    assert res["ok"] is True
    h = res["data"]["hash"]
    assert isinstance(h, str) and len(h) == 16


def test_update_scripts_empty():
    assert ra.update_scripts({"scripts": {}})["ok"] is False


def test_update_scripts_ignores_unknown_files(monkeypatch, tmp_path):
    monkeypatch.setattr(ra, "_script_dir", lambda: str(tmp_path))
    res = ra.update_scripts({"scripts": {"evil.py": base64.b64encode(b"x").decode()}})
    assert res["ok"] is True
    assert res["data"]["updated"] == []
    assert not (tmp_path / "evil.py").exists()


def test_update_scripts_writes_known_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ra, "_script_dir", lambda: str(tmp_path))
    # _fs_paths.py is a known relay file NOT in the hot-reload list, so no
    # import side effects.
    payload = b"# updated fs paths\n"
    res = ra.update_scripts({"scripts": {"_fs_paths.py": base64.b64encode(payload).decode()}})
    assert res["ok"] is True
    assert "_fs_paths.py" in res["data"]["updated"]
    assert (tmp_path / "_fs_paths.py").read_bytes() == payload
