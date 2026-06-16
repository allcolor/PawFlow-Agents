import asyncio
import json
import threading

import pytest

from services.filesystem_service import RelayService


class _Reader:
    pass


def _pending(reader=None):
    evt = threading.Event()
    holder = {}
    if reader is not None:
        holder["_relay_reader"] = reader
    return evt, holder


def test_clear_relay_unblocks_pending_for_closed_connection_only():
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    old_reader = _Reader()
    live_reader = _Reader()
    old_evt, old_holder = _pending(old_reader)
    live_evt, live_holder = _pending(live_reader)

    svc._relay_pool = [
        {"reader": old_reader, "tasks": set()},
        {"reader": live_reader, "tasks": set()},
    ]
    svc._pending = {
        "old": (old_evt, old_holder),
        "live": (live_evt, live_holder),
    }

    svc._clear_relay(reader=old_reader)

    assert old_evt.is_set()
    assert old_holder["error"] == "Relay disconnected"
    assert not live_evt.is_set()
    assert "old" not in svc._pending
    assert "live" in svc._pending
    assert svc._relay_pool == [{"reader": live_reader, "tasks": set()}]


def test_clear_last_relay_unblocks_all_pending_even_without_reader_tag():
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    reader = _Reader()
    tagged_evt, tagged_holder = _pending(reader)
    untagged_evt, untagged_holder = _pending()

    svc._relay_pool = [{"reader": reader, "tasks": set()}]
    svc._pending = {
        "tagged": (tagged_evt, tagged_holder),
        "untagged": (untagged_evt, untagged_holder),
    }

    svc._clear_relay(reader=reader)

    assert tagged_evt.is_set()
    assert untagged_evt.is_set()
    assert tagged_holder["error"] == "Relay disconnected"
    assert untagged_holder["error"] == "Relay disconnected"
    assert svc._pending == {}
    assert svc._relay_pool == []


def test_request_retries_transient_relay_disconnect(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}
    sleeps = []
    request_ids = []

    def _request_once(action, path=".", **kwargs):
        calls["count"] += 1
        request_ids.append(kwargs.get("_request_id"))
        if calls["count"] == 1:
            raise Exception("Relay disconnected")
        assert action == "read_file"
        assert path == "README.md"
        return "ok"

    monkeypatch.setattr(svc, "_request_once", _request_once)
    # fs_mod.time is the shared global time module, so the patch is process-wide;
    # only record sleeps from this test's thread to avoid capturing stray
    # background-thread sleeps (e.g. bg bucket builder) that flake the assert.
    _tid = threading.get_ident()
    monkeypatch.setattr(
        fs_mod.time, "sleep",
        lambda delay: sleeps.append(delay) if threading.get_ident() == _tid else None)

    assert svc._request("read_file", "README.md") == "ok"
    assert calls["count"] == 2
    assert sleeps == [5.0]
    assert request_ids[0] == request_ids[1]


def test_request_does_not_retry_functional_relay_error(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}

    def _request_once(action, path=".", **kwargs):
        calls["count"] += 1
        raise Exception("file not found")

    monkeypatch.setattr(svc, "_request_once", _request_once)
    monkeypatch.setattr(fs_mod.time, "sleep", lambda _delay: None)

    with pytest.raises(Exception, match="file not found"):
        svc._request("read_file", "missing.txt")
    assert calls["count"] == 1


def test_request_marks_relay_disconnect_after_retry_exhaustion(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}
    sleeps = []
    request_ids = []

    def _request_once(_action, _path=".", **kwargs):
        calls["count"] += 1
        request_ids.append(kwargs.get("_request_id"))
        raise Exception("Relay disconnected")

    monkeypatch.setattr(svc, "_request_once", _request_once)
    # Process-wide patch (shared time module): record only this thread's sleeps
    # so a concurrent background-thread time.sleep can't pollute the assert.
    _tid = threading.get_ident()
    monkeypatch.setattr(
        fs_mod.time, "sleep",
        lambda delay: sleeps.append(delay) if threading.get_ident() == _tid else None)

    with pytest.raises(Exception, match="Relay transport retry attempts exhausted"):
        svc._request("read_file", "README.md")

    assert calls["count"] == 5
    assert sleeps == [5.0, 5.0, 5.0, 5.0]
    assert len(set(request_ids)) == 1


class _BrokenReader:
    def exception(self):
        return TimeoutError("network interface changed")

    async def readexactly(self, _size):
        raise self.exception()


class _Writer:
    def __init__(self):
        self.writes = 0

    def write(self, _data):
        self.writes += 1

    async def drain(self):
        return None


@pytest.mark.asyncio
async def test_relay_main_loop_exits_when_reader_stores_socket_timeout():
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    reader = _BrokenReader()
    writer = _Writer()

    with pytest.raises(TimeoutError):
        await svc._relay_main_loop(reader, writer, svc, asyncio.Lock(), set())

    assert writer.writes == 0


@pytest.mark.asyncio
async def test_relay_main_loop_ignores_bad_json_frame(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    frames = iter([(0x01, b"{bad-json"), (0x08, b"")])

    async def _fake_recv(_reader):
        return next(frames)

    monkeypatch.setattr(fs_mod, "_ws_recv_frame", _fake_recv)

    await svc._relay_main_loop(object(), _Writer(), svc, asyncio.Lock(), set())


@pytest.mark.asyncio
async def test_relay_main_loop_keeps_session_after_dispatch_error(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    frames = iter([
        (0x01, json.dumps({"type": "result", "request_id": "rid"}).encode()),
        (0x08, b""),
    ])

    async def _fake_recv(_reader):
        return next(frames)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("dispatch broke")

    monkeypatch.setattr(fs_mod, "_ws_recv_frame", _fake_recv)
    monkeypatch.setattr(svc, "_dispatch_relay_msg", _boom)

    await svc._relay_main_loop(object(), _Writer(), svc, asyncio.Lock(), set())


@pytest.mark.asyncio
async def test_relay_main_loop_labels_result_with_pending_action(monkeypatch):
    import services.filesystem_service as fs_mod

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    evt = asyncio.Event()
    holder = {"_action": "grep"}
    svc._pending["rid"] = (evt, holder)
    frames = iter([
        (0x01, json.dumps({"type": "result", "request_id": "rid"}).encode()),
        (0x08, b""),
    ])
    conn_state = {
        "last_msg_type": "",
        "last_request_id": "",
        "last_action": "",
        "close_info": "",
    }

    async def _fake_recv(_reader):
        return next(frames)

    monkeypatch.setattr(fs_mod, "_ws_recv_frame", _fake_recv)

    await svc._relay_main_loop(
        object(), _Writer(), svc, asyncio.Lock(), set(), conn_state)

    assert conn_state["last_msg_type"] == "result"
    assert conn_state["last_request_id"] == "rid"
    assert conn_state["last_action"] == "grep"


@pytest.mark.asyncio
async def test_relay_request_handler_returns_eio_on_fs_exception(monkeypatch):
    import services.filesystem_service as fs_mod

    class _BoomFs:
        def handle(self, _method, _args):
            raise RuntimeError("disk vanished")

    sent = []

    async def _capture_send(_writer, data, opcode=0x01):
        sent.append((opcode, json.loads(data.decode("utf-8"))))

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    monkeypatch.setattr(svc, "_get_server_fs", lambda: _BoomFs())
    monkeypatch.setattr(fs_mod, "_ws_send_frame", _capture_send)

    await svc._handle_relay_request(
        {"type": "relay_request", "request_id": "rid1",
         "method": "sfs.read", "args": {"path": "x"}},
        object(), asyncio.Lock())

    assert sent == [(0x01, {
        "type": "relay_response",
        "request_id": "rid1",
        "error": "EIO",
        "errno": 5,
        "message": "sfs.read failed: disk vanished",
    })]
