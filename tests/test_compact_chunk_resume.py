"""Tests for the intermediate-chunk resume cache in _summarize_chunked.

Scenario: a chunked compact builds N per-chunk summaries before the final
merge. If chunk K fails (LLM crash, stall-watchdog kill, server kill),
the retry should skip chunks 1..K-1 — they already have stored summaries
— and only re-run chunk K onwards.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tasks.ai.agent_summarize import AgentSummarizeMixin


class _Harness(AgentSummarizeMixin):
    """Thin host class so we can call _summarize_chunked directly.
    Patches _call_summarize per-test to control pass/fail per chunk."""

    def __init__(self):
        self._stub_results: dict[str, str] = {}
        self._call_log: list[tuple[int, int]] = []  # (chunk_len, already_seen?)
        self._seen_chunks: set[str] = set()
        # Signals for fault injection
        self._fail_after_nth = None  # if set, raise RuntimeError after N successful calls
        self._successful_calls = 0

    def _call_summarize(self, client, text, *, target_tokens=0,
                        user_id="", agent_name="", llm_service="",
                        conversation_id="", compact_instructions="",
                        final=True):
        # If this is the FINAL merge pass, look for the joined "=== Chunk i/n notes ===" markers
        is_merge = "=== Chunk" in text
        if is_merge:
            return "FINAL MERGED SUMMARY " * 5
        self._call_log.append(len(text))
        self._seen_chunks.add(text)
        if (self._fail_after_nth is not None
                and self._successful_calls >= self._fail_after_nth):
            raise RuntimeError(f"injected failure at chunk {self._successful_calls + 1}")
        self._successful_calls += 1
        # Return a stable per-chunk summary so the cache finds the right
        # bytes on resume.
        return f"summary-of-{hashlib.sha256(text.encode()).hexdigest()[:8]} " * 5


def _make_big_text(n_chunks: int, chunk_len: int) -> str:
    """Build a text that will split into exactly n_chunks of ~chunk_len."""
    lines = []
    for c in range(n_chunks):
        # Fill one chunk with short lines
        remaining = chunk_len
        while remaining > 20:
            line = f"chunk{c}line" + "x" * 10
            lines.append(line)
            remaining -= len(line) + 1
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _redirect_runtime(tmp_path, monkeypatch):
    """Isolate the compact_cache dir per test."""
    import core.paths as _paths
    monkeypatch.setattr(_paths, "RUNTIME_DIR", tmp_path, raising=False)
    yield


def test_all_chunks_succeed_cache_wiped_at_end():
    harness = _Harness()
    text = _make_big_text(n_chunks=5, chunk_len=500)
    result = harness._summarize_chunked(
        client=None, text=text, chunk_char_limit=500,
        target_tokens=100, conversation_id="cid_test", final=True)
    assert "FINAL MERGED" in result
    # Cache should be wiped after successful final pass
    cache_dir = harness._compact_chunk_cache_dir("cid_test")
    assert cache_dir is not None
    assert not cache_dir.exists()


def test_crash_preserves_cached_chunks_for_resume():
    """Simulate: chunks 1-3 succeed then chunk 4 crashes. Cache keeps
    chunks 1-3; retry skips them, only processes chunks 4-5."""
    harness = _Harness()
    text = _make_big_text(n_chunks=5, chunk_len=500)

    # First attempt: fail after 3 successful chunks
    harness._fail_after_nth = 3
    with pytest.raises(RuntimeError):
        harness._summarize_chunked(
            client=None, text=text, chunk_char_limit=500,
            target_tokens=100, conversation_id="cid_resume", final=True)
    first_attempt_calls = harness._successful_calls
    assert first_attempt_calls == 3

    # Cache dir should exist with 3 chunk files
    cache_dir = harness._compact_chunk_cache_dir("cid_resume")
    assert cache_dir.is_dir()
    cached_files = list(cache_dir.glob("chunk_*.txt"))
    assert len(cached_files) == 3

    # Second attempt: no injection, should resume from cache
    harness2 = _Harness()
    result = harness2._summarize_chunked(
        client=None, text=text, chunk_char_limit=500,
        target_tokens=100, conversation_id="cid_resume", final=True)
    assert "FINAL MERGED" in result
    # Only chunks 4, 5 should have been LLM-called on retry (3 from cache)
    assert harness2._successful_calls == 2
    # Cache wiped after final success
    assert not cache_dir.exists()


def test_cache_path_is_content_addressed():
    """Identical chunks produce the same cache path (hash-keyed) so
    subsequent compacts on the same input reuse prior work."""
    harness = _Harness()
    cache_dir = harness._compact_chunk_cache_dir("cid_cas")
    path_a = harness._compact_chunk_cache_path(cache_dir, "hello world")
    path_b = harness._compact_chunk_cache_path(cache_dir, "hello world")
    path_c = harness._compact_chunk_cache_path(cache_dir, "different")
    assert path_a == path_b
    assert path_a != path_c


def test_empty_conversation_id_raises():
    """A compact without cid is an impossible state — raise at the
    cache-dir boundary so the caller bug surfaces immediately instead
    of silently losing resume capability."""
    harness = _Harness()
    text = _make_big_text(n_chunks=3, chunk_len=500)
    with pytest.raises(ValueError, match="conversation_id"):
        harness._summarize_chunked(
            client=None, text=text, chunk_char_limit=500,
            target_tokens=100, conversation_id="", final=True)


def test_summarize_via_cc_kill_on_delivery_not_treated_as_failure(monkeypatch):
    """_summarize_via_cc deliberately kills CC the moment compact_result
    delivers (see _stream_claude_code). CC exits non-zero → complete_stream
    raises. But the summary IS delivered. The caller must poll the event
    BEFORE treating the exception as a fatal attempt failure, otherwise
    it retries an already-successful compact and eventually gives up."""
    from core.handlers import compact_result as _cr
    from tasks.ai.agent_summarize import AgentSummarizeMixin

    class _Host(AgentSummarizeMixin):
        pass

    host = _Host()
    # Synthetic CC-like client that simulates the kill scenario:
    # registers the summary on the event (as compact_result handler would
    # on a real tool call), then raises to mimic the non-zero CC exit.
    class _FakeCC:
        def __init__(self):
            self._client = self
            self._conversation_id = ""
            self._agent_name = ""
            self._user_id = ""
            self._event_cid = ""
        def clone_for_call(self):
            return self
        def complete_stream(self, messages, max_tokens, **kwargs):
            # Simulate the compact_result handler firing mid-stream,
            # then the CC kill producing a non-zero exit.
            _cr._pending["CK_test"]["summary"] = "VALID SUMMARY " * 20
            _cr._pending["CK_test"]["event"].set()
            raise RuntimeError(
                "LLMClientError: Claude CLI stream exited with code 1")

    _cr.set_compact_key("CK_test")
    calls = {"count": 0}

    def _fake_pub(msg):
        calls["count"] += 1

    result = host._summarize_via_cc(
        _FakeCC(), prompt="summarize this", file_id="fid",
        compact_key="CK_test", target_tokens=500,
        max_retries=3, _pub=_fake_pub,
        conversation_id="cid_kill_test", user_id="uid")
    assert "VALID SUMMARY" in result
    # Only ONE attempt because the summary was already delivered — the
    # kill-caused exception MUST NOT trigger a retry.
    assert calls["count"] == 1, (
        "retry fired on a successful compact — kill after compact_result "
        "should be recognised as success, not as an attempt failure")


def test_intermediate_pass_keeps_cache_for_outer():
    """When called with final=False (nested chunked call), the cache
    is NOT wiped — the outer caller will wipe it."""
    harness = _Harness()
    text = _make_big_text(n_chunks=4, chunk_len=500)
    harness._summarize_chunked(
        client=None, text=text, chunk_char_limit=500,
        target_tokens=100, conversation_id="cid_nested", final=False)
    cache_dir = harness._compact_chunk_cache_dir("cid_nested")
    assert cache_dir.is_dir()
    # Manual cleanup to avoid polluting tmp_path
    import shutil
    shutil.rmtree(cache_dir)
