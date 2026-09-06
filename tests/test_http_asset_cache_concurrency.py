"""Deterministic asset-cache read and publication races."""

import io
import os
import threading
from pathlib import Path

import pytest

from services import _http_request
from services._http_request import _RequestHandler


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "tasks/io/chat_ui"
    root.mkdir(parents=True)
    monkeypatch.setattr(_http_request, "__file__", str(tmp_path / "services/_http_request.py"))
    monkeypatch.setattr(_RequestHandler, "_chat_js_cache", {})
    return root


def request(name, method="GET"):
    handler = object.__new__(_RequestHandler)
    handler.command = method
    handler.wfile = io.BytesIO()
    headers, statuses = {}, []
    handler.send_response = statuses.append
    handler.send_header = headers.__setitem__
    handler.end_headers = lambda: None
    assert handler._handle_chat_js_asset("/chat/js/" + name)
    assert statuses == [200]
    return handler.wfile.getvalue(), headers


def test_slow_read_does_not_block_other_asset_hits_or_misses(assets, monkeypatch):
    for name in ("slow.js", "warm.js", "cold.js"):
        (assets / name).write_bytes(name.encode())
    request("warm.js")
    entered, release, completed = (threading.Event() for _ in range(3))
    original_read = Path.read_bytes
    results, errors = [], []

    def read(path):
        if path.name == "slow.js":
            entered.set()
            assert release.wait(3)
        return original_read(path)

    def slow():
        try:
            results.append(request("slow.js"))
        except BaseException as exc:
            errors.append(exc)

    def unrelated():
        try:
            results.extend([request("warm.js"), request("cold.js")])
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    monkeypatch.setattr(Path, "read_bytes", read)
    reader = threading.Thread(target=slow)
    other = threading.Thread(target=unrelated)
    reader.start()
    try:
        assert entered.wait(2)
        other.start()
        assert completed.wait(2), "An asset read held the global cache lock"
        assert not errors
    finally:
        release.set()
        reader.join(3)
        if other.ident:
            other.join(3)
    assert not reader.is_alive()
    assert not errors
    assert {body for body, _ in results} == {b"slow.js", b"warm.js", b"cold.js"}


@pytest.mark.parametrize("pause_at", ["read", "after_stat"])
def test_replacement_during_read_or_publication_never_installs_stale_bytes(
        assets, monkeypatch, pause_at):
    asset = assets / "race.js"
    asset.write_bytes(b"old")
    before = asset.stat()
    entered, release = threading.Event(), threading.Event()
    original_read, original_stat = Path.read_bytes, Path.stat
    state = {"read": False, "paused": False}
    results, errors = [], []

    def pause():
        state["paused"] = True
        entered.set()
        assert release.wait(3)

    def read(path):
        body = original_read(path)
        if path == asset and threading.current_thread() is reader:
            state["read"] = True
            if pause_at == "read":
                pause()
        return body

    def stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if (path == asset and threading.current_thread() is reader
                and pause_at == "after_stat" and state["read"]
                and not state["paused"]):
            pause()
        return value

    def slow():
        try:
            results.append(request("race.js"))
        except BaseException as exc:
            errors.append(exc)

    reader = threading.Thread(target=slow)
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "stat", stat)
    reader.start()
    try:
        assert entered.wait(2)
        replacement = assets / "race.new"
        replacement.write_bytes(b"new")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(asset)
        assert request("race.js")[0] == b"new"
    finally:
        release.set()
        reader.join(3)
    assert not reader.is_alive()
    assert not errors
    assert results[0][0] == b"new"
    assert _RequestHandler._chat_js_cache[str(asset)][1] == b"new"
    body, headers = request("race.js", "HEAD")
    assert body == b""
    assert headers["Content-Length"] == "3"
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
