import asyncio
import json
import threading

import pytest

from services._relay_ws import _attach_sync_sock_to_loop
from services.filesystem_service import RelayService


class _Reader:
    pass


def _pending(reader=None):
    evt = threading.Event()
    holder = {}
    if reader is not None:
        holder["_relay_reader"] = reader
    return evt, holder


def test_sync_socket_bridge_send_is_never_blocked_by_idle_recv():
    class _FullDuplexSocket:
        def __init__(self):
            self.recv_entered = threading.Event()
            self.release_recv = threading.Event()
            self.sent = threading.Event()
            self.in_recv = False
            self.closed = False

        def setblocking(self, value):
            assert value is True

        def recv(self, _size):
            self.in_recv = True
            self.recv_entered.set()
            self.release_recv.wait(timeout=2)
            self.in_recv = False
            return b""

        def sendall(self, _data):
            self.sent.set()

        def close(self):
            self.closed = True

    loop = asyncio.new_event_loop()
    sock = _FullDuplexSocket()
    _reader, writer = _attach_sync_sock_to_loop(sock, loop)
    assert sock.recv_entered.wait(timeout=2)

    send_thread = threading.Thread(target=writer.write, args=(b"frame",))
    send_thread.start()
    assert sock.sent.wait(timeout=0.25)
    assert sock.in_recv is True
    sock.release_recv.set()
    send_thread.join(timeout=2)

    assert sock.sent.is_set()
    assert not send_thread.is_alive()
    writer.close()
    loop.close()


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
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}
    waits = []
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
    monkeypatch.setattr(
        svc._relay_available, "wait",
        lambda timeout: waits.append(timeout) or True)

    assert svc._request("read_file", "README.md") == "ok"
    assert calls["count"] == 2
    assert waits == [5.0]
    assert request_ids[0] == request_ids[1]


def test_request_does_not_retry_functional_relay_error(monkeypatch):
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}

    def _request_once(action, path=".", **kwargs):
        calls["count"] += 1
        raise Exception("file not found")

    monkeypatch.setattr(svc, "_request_once", _request_once)

    with pytest.raises(Exception, match="file not found"):
        svc._request("read_file", "missing.txt")
    assert calls["count"] == 1


def test_request_marks_relay_disconnect_after_retry_exhaustion(monkeypatch):
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    calls = {"count": 0}
    waits = []
    request_ids = []

    def _request_once(_action, _path=".", **kwargs):
        calls["count"] += 1
        request_ids.append(kwargs.get("_request_id"))
        raise Exception("Relay disconnected")

    monkeypatch.setattr(svc, "_request_once", _request_once)
    monkeypatch.setattr(
        svc._relay_available, "wait",
        lambda timeout: waits.append(timeout) or False)

    with pytest.raises(Exception, match="Relay transport retry attempts exhausted"):
        svc._request("read_file", "README.md")

    assert calls["count"] == 5
    assert waits == [5.0, 5.0, 5.0, 5.0]
    assert len(set(request_ids)) == 1


def test_relay_availability_tracks_pool_lifecycle(monkeypatch):
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    reader = _Reader()
    monkeypatch.setattr(svc, "push_remote_fs_manifest", lambda: None)

    assert not svc._relay_available.is_set()
    svc._set_relay(reader, object(), object(), object(), set())
    assert svc._relay_available.is_set()

    svc._clear_relay(reader=reader)
    assert not svc._relay_available.is_set()


def test_reconnect_signal_wakes_request_without_fixed_delay(monkeypatch):
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    first_failed = threading.Event()
    calls = {"count": 0}

    def _request_once(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            first_failed.set()
            raise Exception("Relay disconnected")
        return "ok"

    monkeypatch.setattr(svc, "_request_once", _request_once)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "value", svc._request("read_file", "README.md")))
    worker.start()
    assert first_failed.wait(timeout=1)
    assert worker.is_alive()
    svc._relay_available.set()
    worker.join(timeout=1)

    assert result == {"value": "ok"}
    assert not worker.is_alive()


def test_managed_relay_disconnect_has_no_manual_cli_instruction():
    svc = RelayService({
        "_service_id": "MyWorkspace", "token": "tok",
        "server_managed": True,
    })

    with pytest.raises(
            Exception,
            match="Managed relay 'MyWorkspace' is reconnecting") as exc:
        svc._request_once("stat", ".")

    assert "python tools/pawflow_relay.py" not in str(exc.value)


def test_managed_relay_reconnecting_triggers_retry_and_respawn(monkeypatch):
    # The pool-empty error of a MANAGED relay must be treated as a transport
    # disconnect: the retry loop is what calls ensure_managed_relay_alive(),
    # the only automatic respawn path. Raising it straight through left a
    # dead managed container "reconnecting" forever until a manual reconnect.
    svc = RelayService({
        "_service_id": "MyWorkspace", "token": "tok",
        "server_managed": True,
    })
    ensures = {"count": 0}
    monkeypatch.setattr(
        svc, "ensure_managed_relay_alive",
        lambda: ensures.__setitem__("count", ensures["count"] + 1) or False)
    monkeypatch.setattr(svc._relay_available, "wait", lambda timeout: False)

    with pytest.raises(Exception, match="Relay transport retry attempts exhausted"):
        svc._request("stat", ".")

    assert ensures["count"] == 4  # once per retry between the 5 attempts


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
    import services._relay_conn as fs_mod  # _relay_main_loop/_ws_recv_frame live here post-split

    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    frames = iter([(0x01, b"{bad-json"), (0x08, b"")])

    async def _fake_recv(_reader):
        return next(frames)

    monkeypatch.setattr(fs_mod, "_ws_recv_frame", _fake_recv)

    await svc._relay_main_loop(object(), _Writer(), svc, asyncio.Lock(), set())


@pytest.mark.asyncio
async def test_relay_main_loop_keeps_session_after_dispatch_error(monkeypatch):
    import services._relay_conn as fs_mod  # _relay_main_loop/_ws_recv_frame live here post-split

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
    import services._relay_conn as fs_mod  # _relay_main_loop/_ws_recv_frame live here post-split

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
    import services._relay_conn as fs_mod  # _handle_relay_request/_ws_send_frame live here post-split

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
