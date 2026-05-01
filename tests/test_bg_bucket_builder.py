"""Tests for core/bg_bucket_builder.py.

Focus:
- _pick_chunk dispatching (bulk / normal / partial / empty)
- maybe_trigger dedup + resolver gating
- build_now_sync end-to-end with mocked resolver + summarize_fn
- memory_auto_extract call path (mocked)
- rollup firing when object_count exceeds trigger

The bg worker runs real file I/O on ConversationStore so we use a
tmp-path redirection fixture (the same one tests/conftest.py already
wires up for the rest of the suite).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.bg_bucket_builder import BgBucketBuilder
from core.bucket_store import (
    BUCKET_OUTPUT_TARGET, BucketStore, L1_TRIGGER_MSGS, ROLLUP_TRIGGER_COUNT,
    TAIL_RESERVE, TAIL_TOKEN_BUDGET,
)

# _PARTIAL_MIN is a private class attribute; re-export under a clean
# name so tests can reason about partial-bucket flush thresholds
# without poking at underscore-prefixed names.
PARTIAL_MIN = BgBucketBuilder._PARTIAL_MIN
MIN_BG_INPUT_CHARS = int(
    BUCKET_OUTPUT_TARGET * BgBucketBuilder._MIN_BG_INPUT_MULTIPLIER
    * BgBucketBuilder._CHARS_PER_TOKEN_EST)


# ── helpers ──────────────────────────────────────────────────────


def _shared_msg(seq: int, role: str = "user", content: str = "") -> Dict[str, Any]:
    return {
        "role": role,
        "content": content or f"msg #{seq}",
        "seq": seq,
        "ts": 100.0 + seq,
        "timestamp": 100.0 + seq,
        "msg_id": f"m{seq:06d}",
        "conversation_id": "cid_test",
        "user_id": "user_test",
    }


def _fake_client():
    c = MagicMock(name="fake_llm_client")
    c.default_model = "fake-model"
    # complete / complete_stream shouldn't be called by the bg worker
    # since we mock _summarize_fn. But if something slips through, give
    # a safe default so the test doesn't hang.
    c.complete.return_value = MagicMock(content="fallback", tokens_in=0,
                                          tokens_out=0, model="fake-model")
    return c


class _FakeBuilder(BgBucketBuilder):
    """BgBucketBuilder subclass that skips the real ConversationStore
    (which requires the full app stack) — file paths are provided
    directly by the fixture.
    """

    def __init__(self, shared_path: Path, transcript_path: Path,
                  conv_dir: Path):
        super().__init__(max_workers=1)
        self._shared_path = shared_path
        self._transcript_path = transcript_path
        self._conv_dir = conv_dir

    def _load_shared_since(self, cid, after_seq):
        if not self._shared_path.exists():
            return []
        out = []
        with open(self._shared_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if int(row.get("seq") or 0) > after_seq:
                    out.append(row)
        out.sort(key=lambda m: (
            float(m.get("ts") or 0.0), int(m.get("seq") or 0)))
        return out

    def _extract_trace(self, cid, first_seq, last_seq):
        # Minimal: read transcript if exists
        if not self._transcript_path.exists():
            return {"edits": {}, "creates": [], "reads": {}, "deletes": [],
                    "commands": [], "delegations": []}
        raw = []
        with open(self._transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    raw.append(json.loads(line))
        from core.tool_activity_digest import extract_tool_activity
        return extract_tool_activity(raw, first_seq, last_seq)

    def _shared_gap(self, cid):
        if not self._shared_path.exists():
            return 0
        store = BucketStore.get(self._conv_dir)
        last_seq = store.last_seq
        n = 0
        with open(self._shared_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if int(row.get("seq") or 0) > last_seq:
                    n += 1
        return n

    # Override the parts that need ConversationStore.instance()
    def _build_pending_buckets(self, cid, user_id):
        store = BucketStore.get(self._conv_dir)
        if self._summarizer_resolver is None or self._summarize_fn is None:
            return
        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
        if not client:
            return
        built = 0
        while True:
            shared_msgs = self._load_shared_since(cid, store.last_seq)
            chunk = self._pick_chunk(shared_msgs, store.object_count,
                                       allow_partial=False)
            if not chunk:
                break
            if not self._build_one_bucket(cid, user_id, store, chunk,
                                            client, ctx_max):
                break
            built += 1
            self._maybe_rollup(store, client, user_id, ctx_max, cid)
        if built:
            self._publish_built(cid, built, store)

    def build_now_sync(self, cid, user_id, allow_partial=True):
        store = BucketStore.get(self._conv_dir)
        if self._summarizer_resolver is None or self._summarize_fn is None:
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": store.object_count,
                     "final_last_seq": store.last_seq}
        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
        if not client:
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": store.object_count,
                     "final_last_seq": store.last_seq}
        with self._pending_lock:
            self._pending.add(cid)
        buckets_built = 0
        rollups_fired = 0
        try:
            while True:
                shared_msgs = self._load_shared_since(cid, store.last_seq)
                chunk = self._pick_chunk(shared_msgs, store.object_count,
                                           allow_partial=allow_partial)
                if not chunk:
                    break
                if not self._build_one_bucket(cid, user_id, store, chunk,
                                                client, ctx_max):
                    break
                buckets_built += 1
                if self._maybe_rollup(store, client, user_id, ctx_max, cid):
                    rollups_fired += 1
        finally:
            with self._pending_lock:
                self._pending.discard(cid)
        return {"buckets_built": buckets_built,
                 "rollups_fired": rollups_fired,
                 "final_object_count": store.object_count,
                 "final_last_seq": store.last_seq}

    def maybe_trigger(self, cid, user_id):
        if self._summarizer_resolver is None:
            return
        # Test-only simplified gating: only the shared-gap path. The
        # production token-trigger path is exercised in test_pawflow.
        if self._shared_gap(cid) < L1_TRIGGER_MSGS + TAIL_RESERVE:
            return
        with self._pending_lock:
            if cid in self._pending:
                return
            self._pending.add(cid)
        try:
            self._executor.submit(self._run_job, cid, user_id)
        except RuntimeError:
            with self._pending_lock:
                self._pending.discard(cid)

    def _publish_built(self, cid, count, store):
        # Skip SSE in tests
        pass

    def _publish_progress(self, cid, stage, payload):
        pass


@pytest.fixture
def fake_builder(tmp_path: Path):
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    shared_path = conv_dir / "shared.jsonl"
    transcript_path = conv_dir / "transcript.jsonl"
    b = _FakeBuilder(shared_path, transcript_path, conv_dir)
    yield b
    b._executor.shutdown(wait=True)


def _write_shared(path: Path, n: int, start_seq: int = 1):
    with open(path, "a", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps(_shared_msg(start_seq + i)) + "\n")


# ── _pick_chunk tests ────────────────────────────────────────────


def test_pick_chunk_returns_empty_when_below_tail_reserve(fake_builder):
    # Fewer than TAIL_RESERVE msgs → available ≤ 0 → []
    msgs = [_shared_msg(i) for i in range(1, max(1, TAIL_RESERVE))]
    assert fake_builder._pick_chunk(msgs, 0, allow_partial=False) == []


def test_pick_chunk_normal_returns_l1_trigger_msgs(fake_builder):
    # Need ≥ L1_TRIGGER + TAIL_RESERVE msgs for a normal L1 bucket
    # (last TAIL_RESERVE always reserved as tail).
    msgs = [_shared_msg(i)
             for i in range(1, L1_TRIGGER_MSGS + TAIL_RESERVE + 5 + 1)]
    chunk = fake_builder._pick_chunk(msgs, 0, allow_partial=False)
    assert len(chunk) == L1_TRIGGER_MSGS


def test_pick_chunk_bulk_mode_when_pyramid_empty_and_large_gap(fake_builder):
    # gap > L1 * 5 and object_count == 0 → bulk mode
    n = L1_TRIGGER_MSGS * 6 + 10  # well past bulk threshold
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=0,
                                       allow_partial=False)
    # Bulk absorbs everything except the last TAIL_RESERVE msgs.
    assert len(chunk) == n - TAIL_RESERVE
    # ... which is way more than a normal L1 chunk
    assert len(chunk) > L1_TRIGGER_MSGS * 2


def test_pick_chunk_bulk_not_fired_when_pyramid_not_empty(fake_builder):
    n = L1_TRIGGER_MSGS * 6
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=5,
                                       allow_partial=False)
    # No bulk mode mid-conv: normal L1 chunking
    assert len(chunk) == L1_TRIGGER_MSGS


def test_pick_chunk_partial_when_allowed(fake_builder):
    # Need gap > TAIL_RESERVE for anything to be bucketable. Pick a gap
    # that yields available in [_PARTIAL_MIN, L1_TRIGGER) so the partial
    # branch fires (not normal-L1, not empty).
    available = max(PARTIAL_MIN, L1_TRIGGER_MSGS // 3)
    n = available + TAIL_RESERVE
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=True)
    assert len(chunk) == available


def test_pick_chunk_partial_blocked_when_too_small(fake_builder):
    # available < _PARTIAL_MIN → []
    n = TAIL_RESERVE + max(0, PARTIAL_MIN - 1)
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=True)
    assert chunk == []


def test_pick_chunk_partial_not_allowed_async(fake_builder):
    # Same gap but allow_partial=False → no chunk (available < L1_TRIGGER)
    available = max(PARTIAL_MIN, L1_TRIGGER_MSGS // 3)
    n = available + TAIL_RESERVE
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=False)
    assert chunk == []


def test_pick_chunk_tail_only_returns_empty(fake_builder):
    # gap == TAIL_RESERVE → available = 0 → no bucketable content
    msgs = [_shared_msg(i) for i in range(1, TAIL_RESERVE + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=0,
                                       allow_partial=True)
    assert chunk == []


# ── build_now_sync integration ──────────────────────────────────


def _make_summarize_fn(output: str = "## Narrative\nok\n\n## Files & operations\nnone"):
    """Produce a summarize_fn that records its calls and returns a
    canned summary."""
    calls = []

    def _summarize(messages, client, **kwargs):
        calls.append({"n_msgs": len(messages), **kwargs})
        return output
    return _summarize, calls


def test_build_now_sync_builds_normal_l1_bucket(fake_builder):
    # Need gap ≥ L1 + TAIL_RESERVE for a normal L1 bucket (tail reserved).
    # Want exactly one L1 fired (no leftover for a second chunk), so use
    # L1 + TAIL_RESERVE on the nose.
    n = L1_TRIGGER_MSGS + TAIL_RESERVE
    _write_shared(fake_builder._shared_path, n)
    summarize_fn, calls = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)

    result = fake_builder.build_now_sync("cid_test", "user_test",
                                           allow_partial=False)
    assert result["buckets_built"] == 1
    assert result["final_object_count"] == 1
    assert len(calls) == 1
    assert calls[0]["n_msgs"] == L1_TRIGGER_MSGS
    # Last TAIL_RESERVE msgs stay un-bucketed
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == L1_TRIGGER_MSGS  # only first chunk bucketed


def test_build_now_sync_bulk_catchup_preserves_tail(fake_builder):
    # Large gap (7×L1) with empty pyramid → bulk absorbs n - TAIL_RESERVE
    # in one bucket; the last TAIL_RESERVE msgs stay as tail (no second
    # bucket created in the same sync pass).
    n = L1_TRIGGER_MSGS * 7
    _write_shared(fake_builder._shared_path, n)
    summarize_fn, calls = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)

    result = fake_builder.build_now_sync("cid_test", "user_test",
                                           allow_partial=False)
    assert result["buckets_built"] == 1  # only the bulk bucket
    assert calls[0]["n_msgs"] == n - TAIL_RESERVE  # all but tail reserve
    # last TAIL_RESERVE msgs NOT in pyramid
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == n - TAIL_RESERVE


def test_build_now_sync_partial_flush_preserves_tail(fake_builder):
    # gap with existing pyramid + allow_partial: available in
    # [_PARTIAL_MIN, L1_TRIGGER) → partial bucket; tail reserved.
    available = max(PARTIAL_MIN, L1_TRIGGER_MSGS // 3)
    n = available + TAIL_RESERVE
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="pre-existing")
    _write_shared(fake_builder._shared_path, n, start_seq=101)

    summarize_fn, calls = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)

    result = fake_builder.build_now_sync("cid_test", "user_test",
                                           allow_partial=True)
    assert result["buckets_built"] == 1
    assert calls[0]["n_msgs"] == available


def test_build_now_sync_no_partial_when_forbidden(fake_builder):
    # available < L1_TRIGGER and allow_partial=False → 0 bucket
    available = max(PARTIAL_MIN, L1_TRIGGER_MSGS // 3)
    n = available + TAIL_RESERVE
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="pre-existing")
    _write_shared(fake_builder._shared_path, n, start_seq=101)

    summarize_fn, calls = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)

    result = fake_builder.build_now_sync("cid_test", "user_test",
                                           allow_partial=False)
    assert result["buckets_built"] == 0
    assert calls == []


def test_build_now_sync_no_resolver_is_noop(fake_builder):
    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS)
    # Don't register resolver/summarize_fn
    result = fake_builder.build_now_sync("cid_test", "user_test")
    assert result["buckets_built"] == 0


def test_build_now_sync_empty_summary_breaks_loop(fake_builder):
    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS * 3)

    # summarize_fn returns too-short output → _build_one_bucket returns
    # False → outer loop breaks
    def _empty_summary(*args, **kwargs):
        return "short"

    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(_empty_summary)

    result = fake_builder.build_now_sync("cid_test", "user_test")
    assert result["buckets_built"] == 0


# ── bucket persistence ──────────────────────────────────────────


def test_build_persists_tool_trace_on_bucket(fake_builder):
    # Exactly L1 + TAIL_RESERVE msgs: one L1 bucket fires, the rest is
    # tail reserved (allow_partial default is True but available=0).
    _write_shared(fake_builder._shared_path,
                   L1_TRIGGER_MSGS + TAIL_RESERVE)
    # Minimal transcript with one tool_call in-range (covered by the
    # L1 bucket which spans seq 1..L1_TRIGGER_MSGS).
    with open(fake_builder._transcript_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "role": "assistant", "seq": 50, "ts": 150.0,
            "msg_id": "a50",
            "tool_calls": [{"id": "tc1", "name": "edit",
                             "arguments": {"path": "src/foo.py"}}],
        }) + "\n")

    summarize_fn, _ = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)
    fake_builder.build_now_sync("cid_test", "user_test")

    store = BucketStore.get(fake_builder._conv_dir)
    docs = store.get_all_summaries()
    assert len(docs) == 1
    trace = docs[0].get("tool_trace")
    assert trace is not None
    assert trace["edits"] == {"src/foo.py": 1}


# ── dedup ───────────────────────────────────────────────────────


def test_maybe_trigger_dedupes_in_flight_jobs(fake_builder):
    # Need ≥ L1 + TAIL_RESERVE for maybe_trigger to consider firing.
    _write_shared(fake_builder._shared_path,
                   L1_TRIGGER_MSGS + TAIL_RESERVE + 5)
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))

    # Simulate an in-flight job for cid_test
    with fake_builder._pending_lock:
        fake_builder._pending.add("cid_test")
    # maybe_trigger should early-return, not submit a job
    fake_builder.maybe_trigger("cid_test", "user_test")
    # Still in pending from our manual add, never submitted new
    with fake_builder._pending_lock:
        assert "cid_test" in fake_builder._pending


# ── Token-budget trigger (production BgBucketBuilder.maybe_trigger) ──


def test_token_trigger_fires_below_msg_threshold(tmp_path, monkeypatch):
    """The production maybe_trigger fires when transcript_chars exceed
    TAIL_TOKEN_BUDGET × 0.7 even if the shared msg gap is below
    L1_TRIGGER. This is the whole point of the token-based trigger:
    tool-heavy turns blow the tail-token budget long before
    L1_TRIGGER msgs of conversation accumulate.
    """
    bb = BgBucketBuilder(max_workers=1)
    submitted: List[str] = []

    def _fake_submit(fn, cid, user_id):
        submitted.append((cid, user_id))
        class _F:
            def result(self, *a, **k): return None
        return _F()
    monkeypatch.setattr(bb._executor, "submit", _fake_submit)

    bb.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))
    bb.set_summarize_fn(_make_summarize_fn()[0])

    cid = "cid_token_trigger"
    # Pre-seed caches: shared msgs well below L1+TAIL_RESERVE, but
    # transcript chars above the token-budget trigger threshold.
    _msg_gap = max(PARTIAL_MIN + TAIL_RESERVE, 20)
    bb._shared_seq_cache[cid] = _msg_gap
    bb._pyramid_seq_cache[cid] = 0
    bb._shared_unbucketed_rows_cache[cid] = _msg_gap
    bb._shared_unbucketed_chars_cache[cid] = MIN_BG_INPUT_CHARS + 1000
    bb._transcript_chars_post_pyramid_cache[cid] = (
        int(TAIL_TOKEN_BUDGET * 0.7 * 3.5) + 1000)  # over threshold

    bb.maybe_trigger(cid, "uid")
    # Token-trigger fires even though seq_gap << L1_TRIGGER + TAIL_RESERVE
    assert len(submitted) == 1
    bb._executor.shutdown(wait=False)


def test_maybe_trigger_submits_background_job_without_foreground_gate(tmp_path, monkeypatch):
    bb = BgBucketBuilder(max_workers=1)
    submitted: List[str] = []

    monkeypatch.setattr(
        bb._executor, "submit",
        lambda *a, **kw: submitted.append(a) or None)
    bb.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))
    bb.set_summarize_fn(_make_summarize_fn()[0])

    cid = "cid_background"
    bb._shared_seq_cache[cid] = L1_TRIGGER_MSGS + TAIL_RESERVE + 1
    bb._pyramid_seq_cache[cid] = 0
    bb._shared_unbucketed_rows_cache[cid] = L1_TRIGGER_MSGS + TAIL_RESERVE + 1
    bb._shared_unbucketed_chars_cache[cid] = MIN_BG_INPUT_CHARS + 1000
    bb._transcript_chars_post_pyramid_cache[cid] = 0

    bb.maybe_trigger(cid, "uid")

    assert len(submitted) == 1
    with bb._pending_lock:
        assert cid in bb._pending
    bb._executor.shutdown(wait=False)


def test_no_trigger_below_both_thresholds(tmp_path, monkeypatch):
    """Both triggers below threshold -> no submit."""
    bb = BgBucketBuilder(max_workers=1)
    submitted: List[str] = []
    monkeypatch.setattr(
        bb._executor, "submit",
        lambda *a, **kw: submitted.append(a) or None)
    bb.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))

    cid = "cid_no_trigger"
    bb._shared_seq_cache[cid] = TAIL_RESERVE + PARTIAL_MIN + 1
    bb._pyramid_seq_cache[cid] = 0
    bb._shared_unbucketed_rows_cache[cid] = TAIL_RESERVE + PARTIAL_MIN + 1
    bb._shared_unbucketed_chars_cache[cid] = MIN_BG_INPUT_CHARS + 1000
    bb._transcript_chars_post_pyramid_cache[cid] = 100  # tiny

    bb.maybe_trigger(cid, "uid")
    assert submitted == []
    bb._executor.shutdown(wait=False)


def test_token_trigger_waits_for_useful_shared_input(tmp_path, monkeypatch):
    """Do not pay an LLM call for tiny bg bucket inputs.

    Background buckets target 2000 tokens; they need at least 4x that
    much useful shared content before token-pressure can submit a job.
    """
    bb = BgBucketBuilder(max_workers=1)
    submitted: List[str] = []
    monkeypatch.setattr(
        bb._executor, "submit",
        lambda *a, **kw: submitted.append(a) or None)
    bb.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))

    cid = "cid_tiny_bg_input"
    bb._shared_seq_cache[cid] = TAIL_RESERVE + PARTIAL_MIN + 1
    bb._pyramid_seq_cache[cid] = 0
    bb._shared_unbucketed_rows_cache[cid] = TAIL_RESERVE + PARTIAL_MIN + 1
    bb._shared_unbucketed_chars_cache[cid] = 2_682
    bb._transcript_chars_post_pyramid_cache[cid] = (
        int(TAIL_TOKEN_BUDGET * 0.7 * 3.5) + 1000)

    bb.maybe_trigger(cid, "uid")

    assert submitted == []
    bb._executor.shutdown(wait=False)



def test_note_transcript_bytes_appended_accumulates(tmp_path):
    """note_transcript_bytes_appended adds to the cache entry; cold
    cache (no key yet) is left alone — _seed populates it later.
    """
    bb = BgBucketBuilder(max_workers=1)
    cid = "cid_chars_test"
    # Cold cache: note is no-op (will be populated by _seed)
    bb.note_transcript_bytes_appended(cid, 1234)
    assert cid not in bb._transcript_chars_post_pyramid_cache

    # Warm: accumulates
    bb._transcript_chars_post_pyramid_cache[cid] = 0
    bb.note_transcript_bytes_appended(cid, 1000)
    bb.note_transcript_bytes_appended(cid, 500)
    assert bb._transcript_chars_post_pyramid_cache[cid] == 1500

    # Decrement (when bucket lands)
    bb.note_pyramid_chars_bucketed(cid, 800)
    assert bb._transcript_chars_post_pyramid_cache[cid] == 700

    # Floor at 0
    bb.note_pyramid_chars_bucketed(cid, 99999)
    assert bb._transcript_chars_post_pyramid_cache[cid] == 0
    bb._executor.shutdown(wait=False)
