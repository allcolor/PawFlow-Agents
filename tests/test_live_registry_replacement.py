"""Live registry replacement must not leak old provider containers."""

from __future__ import annotations

import queue
import threading

from core.cc_live_registry import CCLiveSession, LiveSessionRegistry
from core.codex_live_registry import CodexLiveRegistry
from core.gemini_live_registry import GeminiLiveRegistry


class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self):
        self.terminated = True


def _cc_session(container: str) -> CCLiveSession:
    return CCLiveSession(
        proc=_FakeProc(),
        event_q=queue.Queue(),
        reader_thread=threading.Thread(target=lambda: None),
        stop_event=threading.Event(),
        pool_container=container,
        workdir="/tmp/work",
        service_id="svc",
        svc_pool_idx=0,
    )


def test_codex_register_releases_replaced_container(monkeypatch):
    released = []

    class _Pool:
        def release(self, name):
            released.append(name)

    monkeypatch.setattr("core.codex_pool.CodexPool.instance", staticmethod(lambda: _Pool()))

    reg = CodexLiveRegistry()
    key = ("u", "c", "assistant", "svc", 0)
    first = reg.register(key, "container-a", "/tmp/work", service_id="svc")
    second = reg.register(key, "container-b", "/tmp/work", service_id="svc")

    assert first.container_name == "container-a"
    assert second.container_name == "container-b"
    assert reg.get(key) is second
    assert released == ["container-a"]


def test_gemini_register_releases_replaced_container(monkeypatch):
    released = []

    class _Pool:
        def release(self, name):
            released.append(name)

    monkeypatch.setattr("core.gemini_pool.GeminiPool.instance", staticmethod(lambda: _Pool()))

    reg = GeminiLiveRegistry()
    key = ("u", "c", "gemini", "svc")
    first = reg.register(key, "container-a", "/tmp/work", service_id="svc")
    second = reg.register(key, "container-b", "/tmp/work", service_id="svc")

    assert first.container_name == "container-a"
    assert second.container_name == "container-b"
    assert reg.get(key) is second
    assert released == ["container-a"]


def test_cc_register_tears_down_replaced_session(monkeypatch):
    released = []

    class _Pool:
        def release(self, name):
            released.append(name)

    monkeypatch.setattr("core.claude_code_pool.ClaudeCodePool.instance", staticmethod(lambda: _Pool()))

    reg = LiveSessionRegistry()
    key = ("u", "c", "assistant", "svc", 0)
    first = _cc_session("container-a")
    second = _cc_session("container-b")

    reg.register(key, first)
    reg.register(key, second)

    assert reg.get(key) is second
    assert first.stop_event.is_set()
    assert first.proc.terminated
    assert released == ["container-a"]
