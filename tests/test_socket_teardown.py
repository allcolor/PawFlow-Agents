"""Regression tests for the descriptor-reuse corruption (TLS alert in SQLite).

A socket must never be close()d by a thread that does not own it: the owner
may be inside SSL_read with the raw descriptor number, and a later open()
(a SQLite store) would receive that number and get TLS bytes written into
its file. See docs/SQLITE_CORRUPTION_DIAGNOSTICS.md.
"""
import asyncio
import datetime
import os
import socket
import sqlite3
import ssl
import threading
import time

import pytest

from core._llm_client_driver import _LLMClientDriverMixin
from core.socket_teardown import abort_http_connection, shutdown_socket
from services._relay_ws import _attach_sync_sock_to_loop

TLS_RECORD_HEADER = bytes.fromhex("170303")


def _blocked_reader(sock):
    """Start a thread blocked in recv() on ``sock`` and return (thread, result)."""
    result = {}
    started = threading.Event()

    def run():
        started.set()
        try:
            result["data"] = sock.recv(65536)
        except OSError as exc:
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(2)
    time.sleep(0.05)
    return thread, result


class _Conn:
    """Minimal stand-in for http.client.HTTPConnection."""

    def __init__(self, sock):
        self.sock = sock
        self.closed = False

    def close(self):
        self.closed = True


def test_shutdown_unblocks_reader_without_freeing_descriptor():
    left, right = socket.socketpair()
    try:
        fd = left.fileno()
        thread, result = _blocked_reader(left)
        assert shutdown_socket(left) is True
        thread.join(2)
        assert not thread.is_alive()
        assert result.get("data") == b""
        assert left.fileno() == fd
        probe = os.open(os.devnull, os.O_RDONLY)
        try:
            assert probe != fd
        finally:
            os.close(probe)
    finally:
        left.close()
        right.close()


def test_shutdown_helpers_tolerate_missing_or_dead_sockets():
    assert shutdown_socket(None) is False
    assert shutdown_socket(object()) is False
    assert abort_http_connection(None) is False
    assert abort_http_connection(_Conn(None)) is False
    left, right = socket.socketpair()
    right.close()
    left.close()
    assert shutdown_socket(left) is False


def test_llm_client_abort_shuts_down_but_never_closes_the_stream_socket():
    class _Client(_LLMClientDriverMixin):
        def __init__(self, conn):
            self._abort = threading.Event()
            self.provider = "openai"
            self._active_http_conn = conn

    left, right = socket.socketpair()
    try:
        fd = left.fileno()
        conn = _Conn(left)
        thread, result = _blocked_reader(left)
        client = _Client(conn)
        client.abort()
        thread.join(2)
        assert client._abort.is_set()
        assert not thread.is_alive()
        assert result.get("data") == b""
        assert conn.closed is False
        assert left.fileno() == fd
    finally:
        left.close()
        right.close()


class _BridgeSocket:
    """Socket stub whose recv blocks until released and records lifecycle."""

    def __init__(self, eof_immediately=False):
        self.recv_entered = threading.Event()
        self.release_recv = threading.Event()
        self.shutdown_called = threading.Event()
        self.closed = threading.Event()
        if eof_immediately:
            self.release_recv.set()

    def setblocking(self, value):
        assert value is True

    def recv(self, _size):
        self.recv_entered.set()
        self.release_recv.wait(timeout=2)
        return b""

    def sendall(self, _data):
        pass

    def shutdown(self, _how):
        self.shutdown_called.set()
        self.release_recv.set()

    def close(self):
        assert self.shutdown_called.is_set() or self.release_recv.is_set()
        self.closed.set()


def test_bridge_close_lets_the_pump_thread_release_the_socket():
    loop = asyncio.new_event_loop()
    sock = _BridgeSocket()
    try:
        _reader, writer = _attach_sync_sock_to_loop(sock, loop)
        assert sock.recv_entered.wait(timeout=2)
        writer.close()
        assert sock.shutdown_called.wait(timeout=2)
        assert sock.closed.wait(timeout=2)
    finally:
        loop.close()


def test_bridge_close_after_pump_exit_closes_immediately():
    loop = asyncio.new_event_loop()
    sock = _BridgeSocket(eof_immediately=True)
    try:
        _reader, writer = _attach_sync_sock_to_loop(sock, loop)
        deadline = time.time() + 2
        while not sock.recv_entered.is_set() and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        writer.close()
        assert sock.closed.wait(timeout=2)
        assert not sock.shutdown_called.is_set()
    finally:
        loop.close()


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return cert_path, key_path


def test_aborting_a_tls_stream_never_writes_into_a_database_opened_after(tmp_path):
    """End-to-end reproduction of the production incident, on the fixed path.

    A client thread is blocked inside SSL_read on a partially delivered TLS
    record. Another thread aborts the stream, then opens a SQLite database.
    With close() the database would receive the descriptor number and the
    reader's TLS alert; with shutdown() the number stays reserved.
    """
    pytest.importorskip("cryptography")
    cert_path, key_path = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    server_ctx.load_cert_chain(str(cert_path), str(key_path))
    client_ctx = ssl.create_default_context(cafile=str(cert_path))
    client_ctx.minimum_version = ssl.TLSVersion.TLSv1_3

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    gate = threading.Event()
    server_done = threading.Event()

    def server():
        raw, _ = listener.accept()
        try:
            incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
            tls = server_ctx.wrap_bio(incoming, outgoing, server_side=True)
            while True:
                try:
                    tls.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    pending = outgoing.read()
                    if pending:
                        raw.sendall(pending)
                    incoming.write(raw.recv(65536))
            raw.sendall(outgoing.read())
            tls.write(b"A" * 4000)
            record = outgoing.read()
            raw.sendall(record[:1000])
            gate.wait(10)
            time.sleep(0.2)
            raw.sendall(record[1000:1500])
            time.sleep(0.5)
        finally:
            raw.close()
            server_done.set()

    threading.Thread(target=server, daemon=True).start()
    client = client_ctx.wrap_socket(
        socket.create_connection(listener.getsockname()),
        server_hostname="localhost")
    fd = client.fileno()
    conn = _Conn(client)
    thread, _result = _blocked_reader(client)
    time.sleep(0.5)

    assert abort_http_connection(conn) is True
    database = tmp_path / "after-abort.sqlite3"
    db = sqlite3.connect(str(database), isolation_level=None)
    db.execute("CREATE TABLE t(x)")
    for _ in range(200):
        db.execute("INSERT INTO t VALUES (?)", ("x" * 100,))
    gate.set()
    thread.join(10)
    assert not thread.is_alive()
    assert client.fileno() == fd
    client.close()
    db.execute("INSERT INTO t VALUES (1)")
    db.close()
    server_done.wait(5)
    listener.close()

    content = database.read_bytes()
    assert content[:16] == b"SQLite format 3\x00"
    assert TLS_RECORD_HEADER not in content
    check = sqlite3.connect(str(database))
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()
