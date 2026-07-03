import time


def test_gemini_live_lookup_falls_back_when_pool_idx_extra_is_missing():
    from core.gemini_live_registry import GeminiLiveRegistry

    reg = GeminiLiveRegistry()
    key = ("user", "conv", "assistant", "svc", 2)
    session = reg.register(
        key, "container", "/tmp/work", service_id="svc",
        session_id="session")
    session.last_used = time.monotonic()

    assert reg.get(("user", "conv", "assistant", "svc", -1)) is None
    compatible = reg.get_compatible("user", "conv", "assistant", "svc")
    assert compatible == (key, session)
    assert session.svc_pool_idx == 2
    assert reg.status()[0]["svc_pool_idx"] == 2

    reg._sweeper_stop.set()
    with reg._lock:
        reg._containers.clear()
