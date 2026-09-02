"""Desktop audio keeps direct server relays and tunnels remote relays."""

import base64
import socket
import threading

import pytest

from core import capability_auth as ca
from services import audio_proxy


@pytest.fixture()
def cap_db(tmp_path):
    ca._reset_for_tests()
    ca.init_db(tmp_path / "caps.json")
    with audio_proxy._audio_lock:
        audio_proxy._audio_sources.clear()
        audio_proxy._audio_tokens.clear()
        audio_proxy._active_proxies.clear()
        audio_proxy._active_relay_proxies.clear()
    yield
    with audio_proxy._audio_lock:
        audio_proxy._audio_sources.clear()
        audio_proxy._audio_tokens.clear()
        audio_proxy._active_proxies.clear()
        audio_proxy._active_relay_proxies.clear()
    ca._reset_for_tests()


class _BrowserSocket:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.timeout = None
        self.shutdown_calls = []

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, _size):
        return b""

    def shutdown(self, how):
        self.shutdown_calls.append(how)

    def close(self):
        self.closed = True


class _Relay:
    def __init__(self):
        self.calls = []

    def _request(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return {"ok": True}


def _meta():
    return {"auth_user_id": "alice", "remote_addr": "127.0.0.1"}


def test_remote_audio_uses_relay_without_server_local_tcp(
        cap_db, monkeypatch):
    relay = _Relay()
    token = audio_proxy.register_audio_source(
        "desktop-remote", "", 6180, owner_user_id="alice",
        relay_service=relay, relay_id="relay-1")
    browser = _BrowserSocket()
    closes = []

    monkeypatch.setattr(
        audio_proxy.socket, "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "remote audio must not use server-local TCP"))
    monkeypatch.setattr(
        "services.vnc_proxy._send_command_to_relay",
        lambda _relay, command: closes.append(command))

    audio_proxy.audio_ws_proxy(
        browser,
        {"session_id": "desktop-remote", "token": token}, _meta())

    assert relay.calls[0][0] == "desktop_audio_open"
    assert relay.calls[0][1]["port"] == 6180
    assert relay.calls[0][1]["local_screen"] is False
    assert closes[-1]["action"] == "desktop_audio_close"
    assert browser.closed is True


def test_direct_server_audio_keeps_direct_dispatch(cap_db, monkeypatch):
    token = audio_proxy.register_audio_source(
        "desktop-server", "172.17.0.2", 6180,
        owner_user_id="alice")
    browser = _BrowserSocket()
    calls = []
    monkeypatch.setattr(
        audio_proxy, "_audio_ws_direct_proxy",
        lambda sock, sid, host, port: calls.append((sock, sid, host, port)))
    monkeypatch.setattr(
        audio_proxy, "_audio_ws_relay_proxy",
        lambda *_args: pytest.fail("direct server audio must not use relay"))

    audio_proxy.audio_ws_proxy(
        browser,
        {"session_id": "desktop-server", "token": token}, _meta())

    assert calls == [(browser, "desktop-server", "172.17.0.2", 6180)]


def test_relay_audio_packet_is_forwarded_as_browser_binary_frame(cap_db):
    browser = _BrowserSocket()
    stop = threading.Event()
    with audio_proxy._audio_lock:
        audio_proxy._active_relay_proxies["desktop-remote"] = [{
            "relay_id": "relay-1",
            "relay_session_id": "audio-1",
            "browser_sock": browser,
            "stop": stop,
            "send_lock": threading.Lock(),
            "closed": False,
        }]

    audio_proxy.dispatch_audio_data(
        "relay-1", "audio-1",
        base64.b64encode(b"opus").decode("ascii"))

    assert browser.sent == [b"\x82\x04opus"]


def test_dispatch_audio_close_interrupts_without_cross_thread_close(cap_db):
    browser = _BrowserSocket()
    stop = threading.Event()
    with audio_proxy._audio_lock:
        audio_proxy._audio_sources["desktop-remote"] = {"relay_id": "relay-1"}
        audio_proxy._active_relay_proxies["desktop-remote"] = [{
            "relay_id": "relay-1",
            "relay_session_id": "audio-1",
            "browser_sock": browser,
            "stop": stop,
            "send_lock": threading.Lock(),
            "closed": False,
        }]

    audio_proxy.dispatch_audio_close("relay-1", "audio-1")

    assert stop.is_set()
    assert browser.shutdown_calls == [socket.SHUT_RDWR]
    assert browser.closed is False


def test_kill_proxies_interrupts_without_closing_owner_socket():
    class _Socket:
        def __init__(self):
            self.shutdown_calls = []
            self.closed = False

        def shutdown(self, how):
            self.shutdown_calls.append(how)

        def close(self):
            self.closed = True

    stop = threading.Event()
    backend = _Socket()

    audio_proxy._kill_proxies([(stop, backend)])

    assert stop.is_set()
    assert backend.shutdown_calls == [socket.SHUT_RDWR]
    assert backend.closed is False


def test_direct_proxy_joins_readers_before_closing_socket(
        cap_db, monkeypatch):
    backend_sock, backend_peer = socket.socketpair()
    browser_sock, browser_peer = socket.socketpair()

    class _BackendSocket:
        def fileno(self):
            return backend_sock.fileno()

        def setsockopt(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def recv(self, size):
            return backend_sock.recv(size)

        def shutdown(self, how):
            return backend_sock.shutdown(how)

        def close(self):
            return backend_sock.close()

    class _TrackedSocket:
        def __init__(self, sock):
            self._sock = sock
            self.recv_started = threading.Event()
            self.receiving = False
            self.closed = False
            self.closed_while_receiving = False
            self.shutdown_calls = []

        def fileno(self):
            return self._sock.fileno()

        def setsockopt(self, *args):
            return self._sock.setsockopt(*args)

        def recv(self, size):
            self.receiving = True
            self.recv_started.set()
            try:
                return self._sock.recv(size)
            finally:
                self.receiving = False

        def sendall(self, data):
            return self._sock.sendall(data)

        def shutdown(self, how):
            self.shutdown_calls.append(how)
            return self._sock.shutdown(how)

        def close(self):
            self.closed_while_receiving |= self.receiving
            self.closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return self._sock.close()

    browser = _TrackedSocket(browser_sock)
    monkeypatch.setattr(audio_proxy.socket, "create_connection",
                        lambda *_args, **_kwargs: _BackendSocket())
    runner = threading.Thread(
        target=audio_proxy._audio_ws_direct_proxy,
        args=(browser, "desktop-server", "127.0.0.1", 6180),
        daemon=True)
    runner.start()
    try:
        # A declared 125-byte browser frame with no payload leaves the browser
        # relay blocked in recv() after select() reports the header readable.
        browser_peer.sendall(b"\x82\x7d")
        assert browser.recv_started.wait(1)
        backend_peer.close()
        runner.join(timeout=8)
        assert not runner.is_alive()
        assert browser.receiving is False
        assert browser.closed_while_receiving is False
        assert socket.SHUT_RDWR in browser.shutdown_calls
    finally:
        try:
            backend_peer.close()
        except OSError:
            pass
        browser_peer.close()

