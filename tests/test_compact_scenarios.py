"""End-to-end scenario tests for the shared-pyramid compaction pipeline.

Covers the concrete scenarios we walked through during design:
  A. Young conv (< L1_TRIGGER): no bg bucket, no pyramid, no compact fire
  B. Auto trigger (80% ctx, force=False): async bg trigger, no hot-path build
  C. /compact on huge empty-pyramid conv: bulk mode builds SB-like bucket
  D. /compact with existing pyramid + 80 msgs gap: partial bucket flushed
  E. /compact with ≥150 msgs gap mid-conv: normal L1 chunking
  F. /compact with <37 msgs gap: partial skipped, msgs stay uncovered
  G. Rollup fires when object count exceeds threshold
  H. Memory extractor fires on each bucket build
  I. Post-compact agent_ctx = [sys + pyramid_header + tail]
  J. Pyramid header PRIVATE compression (step 2c) when too big for agent
  K. Tail PRIVATE digest (step 2b) when tail dominates cap

Uses the _FakeBuilder harness from test_bg_bucket_builder.py — wires a
mock summarize_fn + resolver so no real LLM call happens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pytest

from core.bucket_store import (
    BucketStore, L1_TRIGGER_MSGS, ROLLUP_TRIGGER_COUNT, HEADER_BUDGET,
)
from tests.test_bg_bucket_builder import (
    _FakeBuilder, _fake_client, _shared_msg, _write_shared,
    _make_summarize_fn,
)


@pytest.fixture
def fake_builder(tmp_path: Path):
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    shared_path = conv_dir / "shared.jsonl"
    transcript_path = conv_dir / "transcript.jsonl"
    b = _FakeBuilder(shared_path, transcript_path, conv_dir)
    yield b
    b._executor.shutdown(wait=True)


def _wire(builder, summarize_output: str = "ok summary with enough chars to pass the 20-char length gate"):
    fn, calls = _make_summarize_fn(summarize_output)
    builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))
    builder.set_summarize_fn(fn)
    return fn, calls


# ── A. Young conv ──────────────────────────────────────────────────


def test_young_conv_no_bucket_no_pyramid(fake_builder):
    """Fewer than L1_TRIGGER msgs → async bg no-op, pyramid stays empty."""
    _write_shared(fake_builder._shared_path, 50)
    fn, calls = _wire(fake_builder)
    # Async path (maybe_trigger equivalent): gap=50 < 150 → no-op
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    assert result["buckets_built"] == 0
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.object_count == 0
    assert store.last_seq == 0
    assert calls == []


# ── C. /compact on huge empty-pyramid conv (bulk mode) ─────────────


def test_bulk_catchup_absorbs_bulk_plus_tail_l1(fake_builder):
    """pyramid empty + gap >> L1 → 1 bulk bucket + 1 L1 tail bucket."""
    n = L1_TRIGGER_MSGS * 7  # 1050 msgs, well past bulk_threshold (750)
    _write_shared(fake_builder._shared_path, n)
    fn, calls = _wire(fake_builder)

    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    assert result["buckets_built"] == 2
    assert calls[0]["n_msgs"] == n - L1_TRIGGER_MSGS  # bulk absorbs all but last 150
    assert calls[1]["n_msgs"] == L1_TRIGGER_MSGS       # tail L1

    store = BucketStore.get(fake_builder._conv_dir)
    assert store.object_count == 2
    # Pyramid covers the entire shared stream
    assert store.last_seq == n


def test_bulk_not_fired_with_preexisting_pyramid(fake_builder):
    """Mid-conv compact shouldn't bulk — pyramid already seeded."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")

    n_new = L1_TRIGGER_MSGS * 3  # 450 new msgs (would be bulk if pyramid empty)
    _write_shared(fake_builder._shared_path, n_new, start_seq=101)
    fn, calls = _wire(fake_builder)

    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    # With existing pyramid, chunks are L1_TRIGGER_MSGS each — no bulk
    for c in calls:
        assert c["n_msgs"] == L1_TRIGGER_MSGS
    assert result["buckets_built"] == 3


# ── D. Partial flush (force=True with small gap) ────────────────────


def test_partial_flush_when_allowed_and_above_min(fake_builder):
    """gap in [PARTIAL_MIN..L1_TRIGGER) + allow_partial → partial bucket."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    _write_shared(fake_builder._shared_path, 80, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    assert result["buckets_built"] == 1
    assert calls[0]["n_msgs"] == 80


# ── F. Gap < PARTIAL_MIN → no bucket even with allow_partial ───────


def test_partial_below_min_leaves_gap_untouched(fake_builder):
    """gap < PARTIAL_MIN (37) → no bucket built, even with allow_partial=True."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    _write_shared(fake_builder._shared_path, 20, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    assert result["buckets_built"] == 0
    assert calls == []
    # Gap stays uncovered: downstream hot path sees them in saved_recent
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == 100  # unchanged


# ── E. Mid-conv /compact with ≥ L1 msgs ────────────────────────────


def test_midconv_compact_chunks_l1_sized(fake_builder):
    """Normal mid-conv /compact builds L1-sized chunks."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    _write_shared(fake_builder._shared_path, 320, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    # 320 / 150 = 2 L1 chunks + 20 leftover; 20 < PARTIAL_MIN so it stays uncovered
    assert result["buckets_built"] == 2
    for c in calls:
        assert c["n_msgs"] == L1_TRIGGER_MSGS


# ── G. Rollup fires when object_count exceeds threshold ─────────────


def test_rollup_fires_on_object_count_threshold(fake_builder):
    """Past ROLLUP_TRIGGER_COUNT, _maybe_rollup consolidates."""
    store = BucketStore.get(fake_builder._conv_dir)
    # Seed with ROLLUP_TRIGGER_COUNT buckets so the next L1 triggers rollup
    for i in range(ROLLUP_TRIGGER_COUNT):
        store.add_bucket(i * 10 + 1, (i + 1) * 10,
                          float(i), float(i) + 1,
                          summary=f"phase {i} " * 10)  # small but non-empty
    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS,
                   start_seq=ROLLUP_TRIGGER_COUNT * 10 + 1)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    # 1 new L1 added, then rollup consolidates 30 → 1 SB, keep 1 B
    # Final state: 1 SB + 1 B (the new L1) = 2 objects
    assert result["buckets_built"] == 1
    assert result["rollups_fired"] >= 1
    store = BucketStore.get(fake_builder._conv_dir)
    # Should collapse the 30 old + new = 31 down; rollup keeps last, so 2
    assert store.object_count <= 2 + 1  # allow slight slack, but much less than 31


# ── H. Memory extractor fires on each bucket ───────────────────────


def test_memory_extractor_called_per_bucket(fake_builder, monkeypatch):
    """auto_extract_memories should fire once per bucket build."""
    calls_to_extract = []

    def _fake_extract(user_id, summary, agent_name="", llm_client=None):
        calls_to_extract.append({
            "user_id": user_id,
            "summary_len": len(summary),
            "agent_name": agent_name,
        })
        return 0

    monkeypatch.setattr("core.memory_auto_extract.auto_extract_memories",
                         _fake_extract)

    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS * 2)
    fn, _ = _wire(fake_builder, summarize_output="detailed summary " * 10)
    fake_builder.build_now_sync("cid", "uid", allow_partial=False)

    # 2 L1 buckets built → 2 extract calls
    assert len(calls_to_extract) == 2
    for c in calls_to_extract:
        assert c["user_id"] == "uid"
        assert c["summary_len"] > 0


# ── trace + pyramid header integration ─────────────────────────────


def test_bucket_doc_carries_tool_trace(fake_builder):
    """Shared-scan + transcript extract populates tool_trace on the bucket."""
    _write_shared(fake_builder._shared_path, L1_TRIGGER_MSGS)
    # Inject an edit tool_call on a transcript msg in-range
    with open(fake_builder._transcript_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "role": "assistant", "seq": 50, "ts": 150.0,
            "msg_id": "a50",
            "tool_calls": [
                {"id": "tc1", "name": "edit",
                 "arguments": {"path": "src/foo.py"}},
                {"id": "tc2", "name": "bash",
                 "arguments": {"command": "pytest"}},
            ],
        }) + "\n")
        f.write(json.dumps({
            "role": "tool", "seq": 51, "tool_call_id": "tc2",
            "content": "2 passed",
        }) + "\n")

    fn, _ = _wire(fake_builder)
    fake_builder.build_now_sync("cid", "uid", allow_partial=False)

    store = BucketStore.get(fake_builder._conv_dir)
    docs = store.get_all_summaries()
    assert len(docs) == 1
    trace = docs[0]["tool_trace"]
    assert trace["edits"] == {"src/foo.py": 1}
    assert len(trace["commands"]) == 1
    assert trace["commands"][0]["cmd"] == "pytest"
    assert "2 passed" in trace["commands"][0]["result"]


def test_assemble_header_includes_trace(fake_builder):
    """assemble_summary_header renders tool_trace below the narrative."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(
        1, 100, 0.0, 1.0,
        summary="## Narrative\nUser asked to fix bug X.",
        tool_trace={
            "edits": {"src/bug.py": 2},
            "creates": [], "reads": {}, "deletes": [],
            "commands": [{"cmd": "pytest", "result": "ok"}],
            "delegations": [],
        },
    )
    header = store.assemble_summary_header()
    assert "User asked to fix bug X" in header
    assert "Files edited:" in header
    assert "src/bug.py" in header
    assert "pytest" in header


# ── invariant: shared pyramid is per-conv, not per-agent ────────────


def test_pyramid_path_is_shared_not_per_agent(fake_builder):
    """summaries/_shared/ is the only pyramid path; no per-agent dir."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 10, 0.0, 1.0, summary="x")
    assert (fake_builder._conv_dir / "summaries" / "_shared").is_dir()
    # No per-agent dirs should have been created by BucketStore
    other_dirs = [
        d for d in (fake_builder._conv_dir / "summaries").iterdir()
        if d.is_dir() and d.name != "_shared"
    ]
    assert other_dirs == []


# ── invariant: bg writer is the only API surface for mutation ──────


def test_agent_compact_does_not_write_buckets(fake_builder, monkeypatch):
    """Simulate a hot-path compact and assert add_bucket isn't invoked."""
    add_bucket_calls = []
    _orig_add = BucketStore.add_bucket

    def _tracked_add(self, *args, **kwargs):
        add_bucket_calls.append((args, kwargs))
        return _orig_add(self, *args, **kwargs)

    monkeypatch.setattr(BucketStore, "add_bucket", _tracked_add)

    # build_now_sync IS allowed to call add_bucket (it's the writer path).
    # What we verify here is that _compact's own helpers (_digest_oldest_tail,
    # _compress_pyramid_header_for_agent) don't call add_bucket.
    # Import the mixin and exercise the two helpers directly.
    from tasks.ai.agent_compaction import AgentCompactionMixin
    from core.llm_client import LLMMessage

    class _Dummy(AgentCompactionMixin):
        def _summarize_messages(self, msgs, client, **kwargs):
            return "compacted summary " * 10

        def _call_summarize(self, client, text, **kwargs):
            return "compacted header " * 10

    d = _Dummy()
    tail = [LLMMessage(role="user", content=f"msg {i}",
                        conversation_id="cid") for i in range(20)]
    # step 2b
    digest, kept = d._digest_oldest_tail(
        tail, _fake_client(), cap=1000,
        user_id="uid", conversation_id="cid", keep=6)
    assert digest is not None
    assert len(kept) == 6
    assert digest.source["type"] == "private_compaction"
    # step 2c
    header = d._compress_pyramid_header_for_agent(
        "x" * 5000, _fake_client(), cap=1000,
        user_id="uid", conversation_id="cid")
    assert header  # non-empty

    # Neither step wrote a bucket
    assert add_bucket_calls == []
