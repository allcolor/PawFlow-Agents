"""A command is never run twice across a relay reconnect.

Incident 2026-09-26 11:02Z: the relay watchdog forced a reconnect while a
45-minute ``pytest`` ran. The server retried the in-flight ``exec`` with the
same request_id on the new connection; the relay, which tracked commands per
connection, ran it a second time in parallel, and the first run's result was
sent on the dead socket and lost.
"""
import json
import struct
import threading
import time
import types

import pytest

from pawflow_relay import _relay_msg_loop as ml
from pawflow_relay import command_ledger
from pawflow_relay._relay_msg_loop import ConnContext, ConnSession
from pawflow_relay.command_ledger import RUN, CommandLedger

CLOSE = (0x08, struct.pack("!H", 1000) + b"bye")


@pytest.fixture
def ledger(monkeypatch):
    fresh = CommandLedger()
    monkeypatch.setattr(ml, "LEDGER", fresh)
    return fresh


def _cmd(action, request_id):
    return (0x01, json.dumps({"type": "command", "action": action,
                              "request_id": request_id}).encode("utf-8"))


def _session(frames, execute, sends, sock):
    it = iter(frames)

    def recv(_sock):
        return next(it)
    return ConnSession(ConnContext(
        sock=sock, send_lock=threading.Lock(),
        ws_frame_send=lambda s, f, opcode=0x1: sends.append((s, json.loads(f))),
        ws_frame_recv=recv, socket_diag={}, last_activity=[0.0], pool=None,
        execute_command=execute,
        term_mgr=types.SimpleNamespace(sessions={}),
        children=types.SimpleNamespace(
            handle_spawn=lambda *a: None, handle_stop=lambda *a: None),
        child_cfg=object(), term_send=lambda f: None,
        fuse_clients=(None, None, None), remote_mount_mgr=None,
        resolve_spawn_docker_env=lambda: object()))


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


def test_a_retry_during_the_run_does_not_run_it_again(ledger):
    runs = []
    release = threading.Event()

    def execute(msg, on_output=None):
        runs.append(msg["request_id"])
        assert release.wait(10)
        return {"data": {"ok": True, "stdout": "once"}}

    old_sock, new_sock = object(), object()
    sends = []
    _session([_cmd("exec", "rid-1"), CLOSE], execute, sends, old_sock).run()
    assert _wait(lambda: runs == ["rid-1"])

    # Reconnect: the server retries the same request on the new connection.
    _session([_cmd("exec", "rid-1"), CLOSE], execute, sends, new_sock).run()
    release.set()

    assert _wait(lambda: len(sends) == 1)
    time.sleep(0.2)
    assert runs == ["rid-1"], "the retry must not run the command again"
    sock, frame = sends[0]
    assert sock is new_sock, "the result goes out on the live connection"
    assert frame["request_id"] == "rid-1"
    assert frame["data"]["stdout"] == "once"


def test_a_retry_after_the_run_is_answered_from_its_record(ledger):
    runs, sends = [], []

    def execute(msg, on_output=None):
        runs.append(msg["request_id"])
        return {"data": {"ok": True}}

    _session([_cmd("exec", "rid-2"), CLOSE], execute, sends, object()).run()
    assert _wait(lambda: len(sends) == 1)
    _session([_cmd("exec", "rid-2"), CLOSE], execute, sends, object()).run()

    assert _wait(lambda: len(sends) == 2)
    assert runs == ["rid-2"]
    assert sends[1][1] == sends[0][1]


def test_a_result_that_could_not_be_sent_waits_for_the_retry(ledger):
    runs, delivered = [], []

    def execute(msg, on_output=None):
        runs.append(1)
        return {"data": {"ok": True}}

    def dead_send(sock, frame, opcode=0x1):
        raise OSError(9, "Bad file descriptor")

    session = _session([], execute, [], object())
    # The connection died under the running command.
    ledger.attach(session.sock, dead_send, session.send_lock)
    session._handle_command({"type": "command", "action": "exec",
                             "request_id": "rid-3"})
    assert _wait(lambda: runs == [1] and "rid-3" not in ledger._running)

    _session([_cmd("exec", "rid-3"), CLOSE], execute, delivered,
             object()).run()

    assert _wait(lambda: len(delivered) == 1)
    assert runs == [1]


def test_the_record_is_bounded(monkeypatch):
    monkeypatch.setattr(command_ledger, "_MAX_RESULTS", 2)
    record = CommandLedger()
    for rid in ("a", "b", "c"):
        assert record.begin(rid) == RUN
        record.finish(rid, rid.encode())

    assert record.begin("a") == RUN, "the oldest record was evicted"
    assert record.begin("c") == b"c"


def test_records_expire(monkeypatch):
    record = CommandLedger()
    record.begin("a")
    record.finish("a", b"a")
    now = time.monotonic()
    monkeypatch.setattr(command_ledger.time, "monotonic",
                        lambda: now + command_ledger._RESULT_TTL_SECONDS + 1)

    assert record.begin("a") == RUN


def test_sending_without_a_connection_is_an_error():
    with pytest.raises(ConnectionError):
        CommandLedger().send(b"{}")
