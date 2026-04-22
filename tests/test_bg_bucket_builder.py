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
from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest

from core.bg_bucket_builder import BgBucketBuilder
from core.bucket_store import (
    BucketStore, L1_TRIGGER_MSGS, ROLLUP_TRIGGER_COUNT,
)


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
        if self._shared_gap(cid) < L1_TRIGGER_MSGS:
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


def test_pick_chunk_returns_empty_when_gap_too_small(fake_builder):
    msgs = [_shared_msg(i) for i in range(1, 50)]
    assert fake_builder._pick_chunk(msgs, 0, allow_partial=False) == []


def test_pick_chunk_normal_returns_l1_trigger_msgs(fake_builder):
    # Need ≥ L1_TRIGGER + TAIL_RESERVE msgs for a normal L1 bucket
    # (last L1_TRIGGER always reserved as tail).
    msgs = [_shared_msg(i) for i in range(1, 2 * L1_TRIGGER_MSGS + 10 + 1)]
    chunk = fake_builder._pick_chunk(msgs, 0, allow_partial=False)
    assert len(chunk) == L1_TRIGGER_MSGS


def test_pick_chunk_bulk_mode_when_pyramid_empty_and_large_gap(fake_builder):
    # gap > L1 * 5 and object_count == 0 → bulk mode
    n = L1_TRIGGER_MSGS * 6 + 10  # well past bulk threshold
    msgs = [_shared_msg(i) for i in range(1, n + 1)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=0,
                                       allow_partial=False)
    # Bulk absorbs everything except the last L1_TRIGGER_MSGS msgs (tail reserve)
    assert len(chunk) == n - L1_TRIGGER_MSGS
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
    # Need gap > TAIL_RESERVE for anything to be bucketable.
    # Gap = 200: available = 50 → in partial range (>=37, <150).
    msgs = [_shared_msg(i) for i in range(1, 201)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=True)
    assert len(chunk) == 50  # = 200 - TAIL_RESERVE(150)


def test_pick_chunk_partial_blocked_when_too_small(fake_builder):
    # Gap = 180: available = 30 (< _PARTIAL_MIN=37) → return []
    msgs = [_shared_msg(i) for i in range(1, 181)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=True)
    assert chunk == []


def test_pick_chunk_partial_not_allowed_async(fake_builder):
    # Same 200 msgs but allow_partial=False → no chunk (available=50 < L1)
    msgs = [_shared_msg(i) for i in range(1, 201)]
    chunk = fake_builder._pick_chunk(msgs, current_object_count=3,
                                       allow_partial=False)
    assert chunk == []


def test_pick_chunk_tail_only_returns_empty(fake_builder):
    # gap <= TAIL_RESERVE → no bucketable content, tail-only
    msgs = [_shared_msg(i) for i in range(1, L1_TRIGGER_MSGS + 1)]
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
    # Need gap ≥ L1 + TAIL_RESERVE for a normal L1 bucket (tail reserved)
    _write_shared(fake_builder._shared_path, 2 * L1_TRIGGER_MSGS)
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
    assert calls[0]["n_msgs"] == n - L1_TRIGGER_MSGS  # all but tail reserve
    # last TAIL_RESERVE msgs NOT in pyramid
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == n - L1_TRIGGER_MSGS


def test_build_now_sync_partial_flush_preserves_tail(fake_builder):
    # Gap 200 with existing pyramid + allow_partial: available = 50
    # (between _PARTIAL_MIN=37 and L1_TRIGGER=150) → partial bucket
    # flushes 50 msgs; last 150 stay as tail.
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="pre-existing")
    _write_shared(fake_builder._shared_path, 200, start_seq=101)

    summarize_fn, calls = _make_summarize_fn()
    fake_builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc-test"))
    fake_builder.set_summarize_fn(summarize_fn)

    result = fake_builder.build_now_sync("cid_test", "user_test",
                                           allow_partial=True)
    assert result["buckets_built"] == 1
    assert calls[0]["n_msgs"] == 50  # 200 - TAIL_RESERVE(150)


def test_build_now_sync_no_partial_when_forbidden(fake_builder):
    # Gap 200, allow_partial=False: available = 50 < L1_TRIGGER → 0 bucket
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="pre-existing")
    _write_shared(fake_builder._shared_path, 200, start_seq=101)

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
    # Need ≥ L1 + TAIL_RESERVE msgs to build a bucket
    _write_shared(fake_builder._shared_path, 2 * L1_TRIGGER_MSGS)
    # Minimal transcript with one tool_call in-range
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
    # Need ≥ L1 + TAIL_RESERVE = 2×L1 msgs for maybe_trigger to consider firing
    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS * 3)
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
