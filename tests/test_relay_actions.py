"""Tests for the extracted leaf relay actions (_relay_actions)."""
import base64
import builtins
import errno
import http.server
import sys
import threading
import types

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
    emitted = []
    res = ra.http_proxy(
        {"port": http_backend, "method": "GET", "req_path": "/"},
        on_output=lambda kind, data: emitted.append((kind, data)))
    assert res["ok"] is True
    assert res["data"]["status"] == 200
    assert emitted[0][0] == "start"
    assert b"".join(
        base64.b64decode(data) for kind, data in emitted if kind == "chunk"
    ) == b"hello-proxy"
    assert emitted[-1] == ("end", None)


def test_http_proxy_missing_port():
    assert ra.http_proxy({})["ok"] is False


def test_http_proxy_connection_error():
    # nothing listening on this port -> error result, not raise
    res = ra.http_proxy(
        {"port": 1, "req_path": "/"},
        on_output=lambda _kind, _data: None)
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
    payload = b"# updated fs paths\n"
    res = ra.update_scripts({"scripts": {"_fs_paths.py": base64.b64encode(payload).decode()}})
    assert res["ok"] is True
    assert "_fs_paths.py" in res["data"]["updated"]
    assert (tmp_path / "_fs_paths.py").read_bytes() == payload


def test_update_scripts_reloads_readonly_split_module_before_facade(
        monkeypatch, tmp_path):
    payload = b"VALUE = 'fresh'\n"
    target = tmp_path / "_fs_edit.py"
    target.write_bytes(payload)
    monkeypatch.setattr(ra, "_script_dir", lambda: str(tmp_path))

    real_open = builtins.open

    def readonly_open(path, mode="r", *args, **kwargs):
        if str(path) == str(target) and mode == "wb":
            raise OSError(errno.EROFS, "read-only bind mount")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", readonly_open)
    edit_module = types.ModuleType("_fs_edit")
    edit_module.__file__ = str(target)
    edit_module.VALUE = "stale"
    facade_module = types.ModuleType("fs_actions")
    monkeypatch.setitem(sys.modules, "_fs_edit", edit_module)
    monkeypatch.setitem(sys.modules, "fs_actions", facade_module)
    reloaded = []

    def reload_facade(module):
        assert edit_module.VALUE == "fresh"
        reloaded.append(module.__name__)
        return module

    monkeypatch.setattr(ra.importlib, "reload", reload_facade)

    res = ra.update_scripts({
        "scripts": {"_fs_edit.py": base64.b64encode(payload).decode()},
    })

    assert res["ok"] is True
    assert res["data"]["updated"] == ["_fs_edit.py"]
    assert res["data"]["readonly_skipped"] == []
    assert edit_module.VALUE == "fresh"
    assert reloaded == ["fs_actions"]


def test_update_scripts_reloads_optional_sibling_then_facade(
        monkeypatch, tmp_path):
    # fs_archive.py alone changes: the facade re-exports its action, so it
    # must be reloaded after fs_archive even though fs_actions.py was not
    # part of the push (2026-09-03 remote relay regression).
    monkeypatch.setattr(ra, "_script_dir", lambda: str(tmp_path))
    archive_module = types.ModuleType("fs_archive")
    facade_module = types.ModuleType("fs_actions")
    monkeypatch.setitem(sys.modules, "fs_archive", archive_module)
    monkeypatch.setitem(sys.modules, "fs_actions", facade_module)
    reloaded = []
    monkeypatch.setattr(
        ra, "_reload_module",
        lambda module: reloaded.append(module.__name__) or module)

    res = ra.update_scripts({
        "scripts": {"fs_archive.py": base64.b64encode(b"# new\n").decode()},
    })

    assert res["ok"] is True
    assert res["data"]["updated"] == ["fs_archive.py"]
    assert reloaded == ["fs_archive", "fs_actions"]
