import time
from pathlib import Path


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


def test_stream_compatible_fallback_only_fires_when_pool_extra_is_missing():
    """A concrete resume_pool_idx that misses the exact key means the slot
    changed on purpose (rotation, slot removal) — the fallback must not
    resurrect the old slot's container. Only extra-missing (-1) may fall
    back."""
    src = Path("core/llm_providers/_gemini_stream.py").read_text(
        encoding="utf-8")
    assert "if live_session is None and resume_pool_idx < 0:" in src
