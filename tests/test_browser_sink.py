"""BrowserSink keeps slow viewers from stalling the relay connection loop."""
import threading
import time

from services._browser_sink import BrowserSink


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Sock:
    def __init__(self, block=None):
        self.sent = []
        self.closed = False
        self.shut = False
        self._block = block

    def sendall(self, data):
        if self._block is not None:
            self._block.wait(5)
            if self.shut:
                raise OSError("socket shut down")
        self.sent.append(bytes(data))

    def shutdown(self, _how):
        self.shut = True
        if self._block is not None:
            self._block.set()

    def close(self):
        self.closed = True


def test_send_never_blocks_on_a_stuck_viewer():
    sock = _Sock(block=threading.Event())
    sink = BrowserSink(sock, "stuck")
    start = time.perf_counter()
    for _ in range(100):
        assert sink.send(b"x" * 1000)
    assert time.perf_counter() - start < 0.5
    sink.abort()
    assert _wait(lambda: sock.closed)


def test_frames_are_delivered_in_order_then_close_frame_last():
    sock = _Sock()
    sink = BrowserSink(sock, "ordered")
    for index in range(50):
        sink.send(bytes([index]))
    sink.finish(b"\x88\x00")
    assert _wait(lambda: sock.closed)
    assert sock.sent == [bytes([i]) for i in range(50)] + [b"\x88\x00"]
    assert sink.send(b"late") is False


def test_viewer_too_far_behind_is_disconnected():
    sock = _Sock(block=threading.Event())
    sink = BrowserSink(sock, "slow", max_backlog_bytes=10_000)
    assert sink.send(b"a" * 6_000)
    # The first piece may already be in the (blocked) sendall; keep pushing
    # until the queued backlog overflows.
    results = [sink.send(b"b" * 6_000) for _ in range(3)]
    assert False in results
    assert _wait(lambda: sock.closed)
    assert sink.closed


def test_send_failure_closes_the_sink():
    class _Broken(_Sock):
        def sendall(self, data):
            raise BrokenPipeError("gone")

    sock = _Broken()
    sink = BrowserSink(sock, "broken")
    sink.send(b"frame")
    assert _wait(lambda: sock.closed)
    assert sink.send(b"more") is False
